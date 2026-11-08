# ruff: noqa: S608 - SQL is built from names checked plain at construction, then quoted.
"""A Postgres database in a container of evalution's own, run through ``docker exec``.

Nothing needs ``psql`` on this computer: every command runs inside the container, as the
container's own superuser over its local socket.

* **keep** makes a golden copy: connections to the database are closed (and new ones refused
  while it copies), then ``CREATE DATABASE eval_golden_<id> TEMPLATE <db>``. The golden copy
  refuses connections itself, so nothing can change it by accident. Its name goes in
  ``postgres.json`` in the folder kept.
* **put_back**, in this order:

  1. dump the data of the tables left out (``exclude``, the app's traces), so they survive;
  2. close every connection and drop the database;
  3. create it again from the golden copy, with its old owner;
  4. empty the left-out tables (they hold the golden copy's rows) and load the dump back.

* **digest** is each table's row count and the ``md5`` of its rows in a fixed order, tables in
  name order, left-out tables not counted.
* **holding** reads a data-only dump as it streams and names each ``db.schema.table`` holding the
  canary, left-out tables not read.

It refuses any container not named ``eval-...`` (evalution's own compose copy) and the
databases every server has (``postgres``, ``template0``, ``template1``).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

from evalution.isolation.drivers.base import DOCKER_SECONDS, DockerError, Refused, docker

#: Databases a driver never acts on, whatever it's told.
SYSTEM_DBS = frozenset({"postgres", "template0", "template1"})
#: Where the maintenance commands connect (never acted on itself).
_MAINTENANCE = "postgres"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]{0,62}$")
_TABLE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_$]*\.)?[A-Za-z_][A-Za-z0-9_$]*$")
_GOLDEN = re.compile(r"^eval_golden_[0-9a-f]{8,32}$")
_COPY = re.compile(r"^COPY ([\w.\"]+) ")


class DumpScanner:
    """Reads a data-only ``pg_dump`` line by line and notes each table holding a canary."""

    def __init__(self, db: str, canary: str) -> None:
        self.db = db
        self.canary = canary
        self.tables: list[str] = []
        self._table = ""

    def feed(self, line: str) -> None:
        if (m := _COPY.match(line)) is not None:
            self._table = m.group(1).replace('"', "")
        elif self.canary in line and self._table:
            where = f"{self.db}.{self._table}"
            if where not in self.tables:
                self.tables.append(where)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _qualified(table: str) -> str:
    """``schema.table`` with ``public`` filled in, as the plan names a left-out table."""
    return table if "." in table else f"public.{table}"


def _pattern(table: str) -> str:
    """A ``schema.table`` as a ``pg_dump`` pattern that matches it and nothing else."""
    schema, name = table.split(".", 1)
    return f"{_quote(schema)}.{_quote(name)}"


class PostgresStore:
    """One database in a Postgres container evalution started.

    :param container: The container's name; must start with ``eval-``.
    :param db: The database; never one of :data:`SYSTEM_DBS`.
    :param user: The role commands run as (the container's ``POSTGRES_USER``).
    :param exclude: Tables (``schema.table``, or ``table`` in ``public``) left out of the digest
        and the canary search, whose rows survive :meth:`put_back`: the app's traces.
    :raises Refused: For anything not evalution's own, before any command runs.
    """

    def __init__(self, container: str, db: str, user: str, exclude: Sequence[str] = ()) -> None:
        if not container.startswith("eval-") or not _NAME.match(container):
            raise Refused(f"container {container!r} isn't one of evalution's own (eval-...)")
        if db in SYSTEM_DBS or db.startswith("eval_golden_"):
            raise Refused(f"database {db!r} is never reset")
        if not _NAME.match(db) or not _NAME.match(user):
            raise Refused(f"{db!r} or {user!r} isn't a plain name")
        bad = [t for t in exclude if not _TABLE.match(t)]
        if bad:
            raise Refused(f"left-out tables aren't plain names: {', '.join(bad)}")
        self.container = container
        self.db = db
        self.user = user
        self.exclude = sorted({_qualified(t) for t in exclude})
        self.name = f"postgres:{container}/{db}"

    # Commands --------------------------------------------------------------------------------

    def _psql(self, *sql: str, db: str | None = None) -> str:
        """Run each statement on its own (``CREATE DATABASE`` can't share a transaction)."""
        args = ["psql", "-U", self.user, "-d", db or _MAINTENANCE, "-X", "-q", "-A", "-t"]
        args += ["-v", "ON_ERROR_STOP=1"]
        for statement in sql:
            args += ["-c", statement]
        return docker("exec", self.container, *args)

    def _close(self, db: str) -> str:
        return (
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            f"WHERE datname = {_literal(db)} AND pid <> pg_backend_pid()"
        )

    def _exists(self) -> bool:
        out = self._psql(f"SELECT 1 FROM pg_database WHERE datname = {_literal(self.db)}")
        return out.strip() == "1"

    # The protocol ----------------------------------------------------------------------------

    def covers(self, path: str) -> bool:
        """Whether *path*, a place the copy's canary search names as ``container:db.schema.table``,
        is in this database (a left-out table included: it is never a leak)."""
        container, _, where = path.partition(":")
        return container == self.container and where.startswith(f"{self.db}.")

    def tables(self) -> list[str]:
        """``schema.table`` of every table of the app's, in name order."""
        out = self._psql(
            "SELECT schemaname || '.' || tablename FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY schemaname, tablename",
            db=self.db,
        )
        return [line for line in out.splitlines() if line]

    def digest(self) -> str:
        counted = [t for t in self.tables() if t not in self.exclude]
        h = hashlib.sha256()
        if counted:
            parts = []
            for t in counted:
                parts.append(
                    f"SELECT {_literal(t)}, count(*), "
                    "md5(coalesce(string_agg(r::text, '' ORDER BY r::text), '')) "
                    f"FROM {_pattern(t)} r"
                )
            h.update(self._psql(" UNION ALL ".join(parts), db=self.db).encode())
        return h.hexdigest()

    def keep(self, into: Path) -> None:
        golden = f"eval_golden_{secrets.token_hex(6)}"
        sql = "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = "
        owner = self._psql(sql + _literal(self.db)).strip()
        if not owner:
            raise DockerError(f"database {self.db!r} isn't in {self.container}")
        db = _quote(self.db)
        try:
            self._psql(
                f"ALTER DATABASE {db} ALLOW_CONNECTIONS false",
                self._close(self.db),
                f"CREATE DATABASE {_quote(golden)} TEMPLATE {db}",
                f"ALTER DATABASE {_quote(golden)} ALLOW_CONNECTIONS false",
            )
        finally:
            self._psql(f"ALTER DATABASE {db} ALLOW_CONNECTIONS true")
        into.mkdir(parents=True, exist_ok=True)
        (into / "postgres.json").write_text(
            json.dumps(
                {"container": self.container, "db": self.db, "golden": golden, "owner": owner}
            )
        )

    def _golden(self, kept: Path) -> tuple[str, str]:
        saved = json.loads((kept / "postgres.json").read_text())
        golden, owner = saved.get("golden", ""), saved.get("owner", "")
        if saved.get("container") != self.container or saved.get("db") != self.db:
            raise Refused(f"{kept} was kept from another database")
        if not _GOLDEN.match(golden) or not _NAME.match(owner):
            raise Refused(f"{kept}/postgres.json doesn't name a golden copy of evalution's own")
        return golden, owner

    def put_back(self, kept: Path) -> None:
        """Recreate the database from the golden copy, the left-out tables' rows carried over
        (the order is in the module's docstring)."""
        golden, owner = self._golden(kept)
        dump = kept / "left-out.sql"
        carried = bool(self.exclude) and self._exists()
        if carried:
            present = set(self.tables())
            carry = [t for t in self.exclude if t in present]
            carried = bool(carry)
            if carried:
                args = ["exec", self.container, "pg_dump", "-U", self.user, "-d", self.db]
                args += ["--data-only", "--no-owner"]
                for t in carry:
                    args += ["-t", _pattern(t)]
                with dump.open("wb") as out:
                    docker(*args, stdout=out)
        db = _quote(self.db)
        self._psql(
            f"DROP DATABASE IF EXISTS {db} WITH (FORCE)",
            f"CREATE DATABASE {db} OWNER {_quote(owner)} TEMPLATE {_quote(golden)}",
        )
        if carried:
            listed = ", ".join(_pattern(t) for t in carry)
            self._psql(f"TRUNCATE {listed}", db=self.db)
            with dump.open("rb") as source:
                docker(
                    "exec", "-i", self.container,
                    "psql", "-U", self.user, "-d", self.db, "-X", "-q", "-v", "ON_ERROR_STOP=1",
                    stdin=source,
                )  # fmt: skip
            dump.unlink()

    def drop_golden(self, kept: Path) -> None:
        """Remove the golden copy :meth:`keep` made into *kept*."""
        golden, _ = self._golden(kept)
        self._psql(f"DROP DATABASE IF EXISTS {_quote(golden)}")

    def holding(self, canary: str) -> list[str]:
        exe = shutil.which("docker")
        if exe is None:
            raise DockerError("docker isn't installed")
        args = [exe, "exec", self.container, "pg_dump", "-U", self.user, "-d", self.db]
        args += ["--data-only"]
        for t in self.exclude:
            args += [f"--exclude-table-data={_pattern(t)}"]
        scanner = DumpScanner(self.db, canary)
        proc = subprocess.Popen(  # noqa: S603 - a fixed program, arguments passed as a list
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        timer = threading.Timer(DOCKER_SECONDS, proc.kill)
        timer.start()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:  # a file's lines have no length limit
                scanner.feed(raw.decode(errors="replace"))
        finally:
            timer.cancel()
            proc.wait()
        if proc.returncode != 0:
            raise DockerError(f"pg_dump of {self.db} in {self.container} failed")
        return scanner.tables
