"""The database and one workspace's view of it (:class:`Docs`)."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    delete,
    event,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import ArgumentError

#: This server's own workspace: the only one until people sign in.
LOCAL = "local"
URL_ENV = "DATABASE_URL"
FILE = "evalution.db"

_meta = MetaData()
documents = Table(
    "documents",
    _meta,
    Column("workspace", String(64), primary_key=True),
    Column("kind", String(64), primary_key=True),
    # Keys sort byte by byte on both, as SQLite does: Postgres would follow the server's locale.
    Column(
        "key",
        String(512).with_variant(String(512, collation="C"), "postgresql"),
        primary_key=True,
    ),
    Column("body", Text, nullable=False),
    Column("updated_at", Float, nullable=False),
)
entries = Table(
    "entries",
    _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("workspace", String(64), nullable=False, index=True),
    Column("kind", String(64), nullable=False),
    Column("key", String(512), nullable=False),
    Column("body", Text, nullable=False),
    Column("at", Float, nullable=False),
)
schema = Table("schema", _meta, Column("version", Integer, primary_key=True))


def _documents_and_entries(db: Connection) -> None:
    _meta.create_all(db, tables=[documents, entries])


#: What takes the database from version ``n`` to ``n + 1`` is ``_STEPS[n]``, run in order, each in
#: the one transaction :meth:`Database._migrate` holds. A change of the tables is a step added
#: at the end, never an edit of one already released.
_STEPS: list[Callable[[Connection], None]] = [_documents_and_entries]
#: The version this evalution makes; :meth:`Database.open` refuses a newer database.
SCHEMA = len(_STEPS)
#: Any number of our own, the same in every process: two servers starting at once on one
#: Postgres take turns to migrate.
_MIGRATING = 0x6576616C  # "eval"


class StorageError(RuntimeError):
    """The database can't be used: unreachable, or written by a newer evalution."""


@dataclass(frozen=True)
class Entry:
    id: int
    body: Any
    at: float


def _dump(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False)


_opened: dict[str, Database] = {}
_opening = threading.Lock()


class Database:
    """One connection pool per database for the whole process (:meth:`open` gives the same one
    each time); :meth:`docs` gives a workspace's view of it."""

    def __init__(self, engine: Engine, url: str) -> None:
        self.engine = engine
        self.url = url

    @classmethod
    def open(cls, data_dir: Path, url: str | None = None) -> Database:
        """``$DATABASE_URL`` (or *url*), else SQLite in *data_dir*: the one this process already
        has open, else opened now, its tables made on first use.

        :raises StorageError: Unreachable, or made by a newer evalution.
        """
        url = _driver_url(url or os.environ.get(URL_ENV) or "")
        if not url:
            url = f"sqlite:///{data_dir.resolve() / FILE}"
        with _opening:
            if url not in _opened:
                if url.startswith("sqlite"):
                    _owner_only(data_dir / FILE)
                _opened[url] = cls._connect(url)
            return _opened[url]

    @classmethod
    def _connect(cls, url: str) -> Database:
        try:
            dialect = make_url(url).get_backend_name()
        except ArgumentError as exc:
            raise StorageError(f"{URL_ENV} isn't a database URL: {exc}") from exc
        if dialect not in _INSERTS:
            raise StorageError(f"evalution keeps its data in SQLite or Postgres, not {dialect}")
        lite = dialect == "sqlite"
        engine = create_engine(
            url,
            # A server's connection can drop while idle (a restart, a proxy): check before use.
            pool_pre_ping=not lite,
            connect_args={"check_same_thread": False, "timeout": 30} if lite else {},
        )
        if lite:
            event.listen(engine, "connect", _sqlite_pragmas)
        database = cls(engine, url)
        try:
            database._migrate()
        except StorageError:
            engine.dispose()
            raise
        return database

    def _migrate(self) -> None:
        """Bring the tables to :data:`SCHEMA`, all of it or none: one transaction, which no other
        process starting on the same database runs at the same time. Both databases undo table
        changes with the transaction; what keeps two out is the database's own lock, its write lock
        on SQLite and an advisory lock on Postgres."""
        try:
            with self.engine.begin() as db:
                _hold_for_migrating(db)
                schema.create(db, checkfirst=True)
                found = db.scalar(select(func.max(schema.c.version))) or 0
                if found > SCHEMA:
                    raise StorageError(
                        f"the database is from a newer evalution (schema {found}, this knows "
                        f"{SCHEMA}): upgrade evalution"
                    )
                for version in range(found, SCHEMA):
                    _STEPS[version](db)
                    db.execute(insert(schema).values(version=version + 1))
        except StorageError:
            raise
        except Exception as exc:  # the driver's own errors, whatever the database
            raise StorageError(f"the database can't be opened: {exc}") from exc

    def docs(self, workspace: str = LOCAL) -> Docs:
        return Docs(self, workspace)

    def close(self) -> None:
        """Let go of its connections; the next :meth:`open` connects again."""
        with _opening:
            if _opened.get(self.url) is self:
                del _opened[self.url]
        self.engine.dispose()


def _driver_url(url: str) -> str:
    """*url* with the driver evalution installs for Postgres: hosts hand out ``postgres://``."""
    for scheme in ("postgres://", "postgresql://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url.removeprefix(scheme)
    return url


def _hold_for_migrating(db: Connection) -> None:
    """Wait for, then hold until the transaction ends, the right to change the tables."""
    if db.dialect.name == "sqlite":
        # The write lock now, not at the first write: pysqlite starts no transaction before DDL.
        db.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATING})


def _owner_only(file: Path) -> None:
    """Make the database file before SQLite does, readable by its owner only: it holds prompts,
    replies and tool output. SQLite gives its journal files the same mode."""
    file.parent.mkdir(parents=True, exist_ok=True)
    if not file.exists():
        os.close(os.open(file, os.O_WRONLY | os.O_CREAT, 0o600))
    file.chmod(0o600)


def _sqlite_pragmas(connection: Any, _record: Any) -> None:
    """Readers don't wait on a writer, and ``serve`` and ``evalution run`` can share the file."""
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


class Docs:
    """One workspace's documents and entries. Blocking; safe from any thread."""

    def __init__(self, database: Database, workspace: str) -> None:
        self.database = database
        self.workspace = workspace

    @contextmanager
    def _tx(self) -> Iterator[Connection]:
        with self.database.engine.begin() as db:
            yield db

    def _where(self, kind: str, key: str) -> Any:
        c = documents.c
        return and_(c.workspace == self.workspace, c.kind == kind, c.key == key)

    # Documents

    def get(self, kind: str, key: str) -> Any | None:
        """The document, ``None`` when there is none."""
        with self._tx() as db:
            body = db.scalar(select(documents.c.body).where(self._where(kind, key)))
        return None if body is None else json.loads(body)

    def updated_at(self, kind: str, key: str) -> float | None:
        with self._tx() as db:
            return db.scalar(select(documents.c.updated_at).where(self._where(kind, key)))

    def put(self, kind: str, key: str, body: Any) -> None:
        """Write the document whole, made or replaced."""
        # One statement, so two processes writing the same key at once both land, the last winning.
        new = self._insert(kind, key, body)
        with self._tx() as db:
            db.execute(
                new.on_conflict_do_update(
                    index_elements=list(documents.primary_key),
                    set_={"body": new.excluded.body, "updated_at": new.excluded.updated_at},
                )
            )

    def create(self, kind: str, key: str, body: Any) -> bool:
        """Write the document only when there is none under *key*; whether it was made. Of two
        made at once, by any process, one is."""
        new = self._insert(kind, key, body).on_conflict_do_nothing()
        with self._tx() as db:
            # Kept for an INSERT only when asked: how many rows went in, 0 on a conflict.
            done = db.execute(new, execution_options={"preserve_rowcount": True})
            return bool(done.rowcount)

    def _insert(self, kind: str, key: str, body: Any) -> Any:
        """The dialect's own ``INSERT``, which knows ``ON CONFLICT``."""
        made = _INSERTS[self.database.engine.dialect.name](documents)
        return made.values(
            workspace=self.workspace, kind=kind, key=key, body=_dump(body), updated_at=time.time()
        )

    def delete(self, kind: str, key: str) -> bool:
        with self._tx() as db:
            return bool(db.execute(delete(documents).where(self._where(kind, key))).rowcount)

    def delete_prefix(self, kind: str, prefix: str) -> int:
        """Delete every document of *kind* whose key starts with *prefix*; how many went."""
        c = documents.c
        with self._tx() as db:
            gone = db.execute(
                delete(documents).where(
                    c.workspace == self.workspace, c.kind == kind, _starts(c.key, prefix)
                )
            )
            return int(gone.rowcount or 0)

    def keys(self, kind: str, prefix: str = "") -> list[str]:
        """The keys of *kind* starting with *prefix*, sorted."""
        c = documents.c
        with self._tx() as db:
            rows = db.scalars(
                select(c.key)
                .where(c.workspace == self.workspace, c.kind == kind, _starts(c.key, prefix))
                .order_by(c.key)
            )
            return list(rows)

    def items(self, kind: str, prefix: str = "") -> list[tuple[str, Any, float]]:
        """``(key, body, updated_at)`` of every document of *kind* starting with *prefix*."""
        c = documents.c
        with self._tx() as db:
            rows = db.execute(
                select(c.key, c.body, c.updated_at)
                .where(c.workspace == self.workspace, c.kind == kind, _starts(c.key, prefix))
                .order_by(c.key)
            ).all()
        return [(key, json.loads(body), at) for key, body, at in rows]

    # Entries

    def append(self, kind: str, key: str, body: Any) -> int:
        """Add *body* to the end of the log under *key*; its id."""
        with self._tx() as db:
            done = db.execute(
                insert(entries).values(
                    workspace=self.workspace, kind=kind, key=key, body=_dump(body), at=time.time()
                )
            )
            return int(done.inserted_primary_key[0])

    def entries(self, kind: str, key: str) -> list[Entry]:
        """The log under *key*, oldest first."""
        c = entries.c
        with self._tx() as db:
            rows = db.execute(
                select(c.id, c.body, c.at)
                .where(c.workspace == self.workspace, c.kind == kind, c.key == key)
                .order_by(c.id)
            ).all()
        return [Entry(id=i, body=json.loads(body), at=at) for i, body, at in rows]

    def clear_entries(self, kind: str, key: str) -> int:
        c = entries.c
        with self._tx() as db:
            gone = db.execute(
                delete(entries).where(c.workspace == self.workspace, c.kind == kind, c.key == key)
            )
            return int(gone.rowcount or 0)


#: The databases evalution keeps its data in, each with its ``INSERT ... ON CONFLICT``.
_INSERTS: dict[str, Callable[[Table], Any]] = {
    "sqlite": sqlite.insert,
    "postgresql": postgresql.insert,
}


def _starts(column: Any, prefix: str) -> Any:
    """``column`` starts with *prefix*, taken literally (``%`` and ``_`` included)."""
    if not prefix:
        return column.is_not(None)
    return func.substr(column, 1, len(prefix)) == prefix
