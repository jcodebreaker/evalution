"""The built-in trace source (docs/architecture.md, "Traces"): spans kept by evalution itself in
one small SQLite file, ``<data_dir>/traces/spans.db``. No vendor, nothing to run. They arrive over
OTLP (:mod:`evalution.traces.otlp`) and are read back through
:class:`evalution.traces.base.TraceSource`.

Retention, applied on every write: spans received more than ``keep_days`` ago go, and past
``keep_mb`` the oldest received go, plain spans (not AI) before AI spans so that a flood of them
can't push out what the app's AI did. Age is by when a span arrived, not by the time it claims.

Each operation opens its own connection, on a worker thread: the file is also read by
``evalution run`` in another process. Span contents may include prompts, so the folder and file
are owner-only and nothing here logs them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from evalution.traces.base import Span, SpanReader, TraceError, kind_of, nest

PAGE = 1_000
_PRUNE_ROWS = 500
#: The resource attribute that names a span's target, the one Phoenix's tooling sets (a project,
#: which may carry the prefix), and what spans naming neither are kept under.
TARGET_ATTRIBUTE = "evalution.target"
PROJECT_ATTRIBUTE = "openinference.project.name"
TARGET_PREFIX = "evalution-"
DEFAULT_TARGET = "default"
_T = TypeVar("_T")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    parent TEXT,
    name TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    status TEXT NOT NULL,
    attrs TEXT NOT NULL,
    ai INTEGER NOT NULL,
    received REAL NOT NULL,
    UNIQUE (trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS spans_window ON spans (target, start, id);
CREATE INDEX IF NOT EXISTS spans_received ON spans (received);
CREATE INDEX IF NOT EXISTS spans_plain ON spans (ai, id);
"""


@dataclass(frozen=True)
class Received:
    """One span as it arrived. ``attrs`` is flat, as sent; ``start`` and ``end`` are seconds."""

    target: str
    trace_id: str
    span_id: str
    parent: str | None
    name: str
    start: float
    end: float
    status: str
    attrs: dict[str, Any]

    def row(self, now: float) -> tuple[Any, ...]:
        ai = kind_of(nest(self.attrs)) != "UNKNOWN"
        text = json.dumps(self.attrs, ensure_ascii=False)
        return (
            self.target,
            self.trace_id,
            self.span_id,
            self.parent,
            self.name,
            self.start,
            self.end,
            self.status,
            text,
            ai,
            now,
        )


class SpanStore:
    """The SQLite file. Every method blocks: call it on a worker thread."""

    def __init__(self, path: Path, keep_mb: int = 500, keep_days: int = 14) -> None:
        self.path = path
        self.keep_bytes = keep_mb * 1024 * 1024
        self.keep_seconds = keep_days * 86_400.0
        self._ready = False

    @contextlib.contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        if not self._ready:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.touch(mode=0o600, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10.0)
        try:
            db.execute("PRAGMA synchronous = NORMAL")
            db.execute("PRAGMA journal_size_limit = 8388608")
            if not self._ready:
                # Before any table: it can't be turned on afterwards without a VACUUM.
                db.execute("PRAGMA auto_vacuum = INCREMENTAL")
                db.execute("PRAGMA journal_mode = WAL")
                db.executescript(_SCHEMA)
                self._ready = True
            with db:
                yield db
        finally:
            db.close()

    def add(self, spans: Iterable[Received], now: float | None = None) -> None:
        """Keep *spans* (a span sent again replaces itself: exporters retry), then apply the
        retention."""
        at = time.time() if now is None else now
        with self._open() as db:
            db.executemany(
                "INSERT OR REPLACE INTO spans (target, trace_id, span_id, parent, name, start, "
                "end, status, attrs, ai, received) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (s.row(at) for s in spans),
            )
            self._retain(db, at)

    def _used(self, db: sqlite3.Connection) -> int:
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        return (pages - free) * db.execute("PRAGMA page_size").fetchone()[0]

    def _retain(self, db: sqlite3.Connection, now: float) -> None:
        cutoff = now - self.keep_seconds
        gone = db.execute("DELETE FROM spans WHERE received < ?", (cutoff,)).rowcount
        while self._used(db) > self.keep_bytes:
            removed = (
                db.execute(
                    "DELETE FROM spans WHERE id IN "
                    "(SELECT id FROM spans WHERE ai = 0 ORDER BY id LIMIT ?)",
                    (_PRUNE_ROWS,),
                ).rowcount
                or db.execute(
                    "DELETE FROM spans WHERE id IN (SELECT id FROM spans ORDER BY id LIMIT ?)",
                    (_PRUNE_ROWS,),
                ).rowcount
            )
            if not removed:
                break
            gone += removed
        if gone:
            db.execute("PRAGMA incremental_vacuum").fetchall()

    def page(
        self,
        target: str,
        start: float,
        end: float,
        trace_id: str | None,
        before: tuple[float, int] | None,
    ) -> list[tuple[Any, ...]]:
        """Up to :data:`PAGE` rows of *target*, newest first, older than *before* (a row's
        ``(start, id)``)."""
        at, last = before if before else (end, 2**63 - 1)
        with self._open() as db:
            return db.execute(
                "SELECT id, trace_id, span_id, parent, name, start, end, status, attrs FROM spans "
                "WHERE target = ? AND start >= ? AND start <= ? AND (start, id) < (?, ?) "
                "AND (? IS NULL OR trace_id = ?) ORDER BY start DESC, id DESC LIMIT ?",
                (target, start, end, at, last, trace_id, trace_id, PAGE),
            ).fetchall()

    def has(self, target: str) -> bool:
        with self._open() as db:
            found = "SELECT 1 FROM spans WHERE target = ? LIMIT 1"
            return db.execute(found, (target,)).fetchone() is not None


def _span(row: Sequence[Any]) -> Span:
    attrs = nest(json.loads(row[8]))
    return Span(
        id=row[2],
        trace_id=row[1],
        parent=row[3],
        name=row[4],
        kind=kind_of(attrs),
        start=row[5],
        end=row[6],
        status=row[7],
        attrs=attrs,
    )


class Builtin(SpanReader):
    """One target's spans in the store, and the *also* sources read with it. Spans have no page
    of their own here, so there is no trace link."""

    def __init__(self, store: SpanStore, target: str, also: Sequence[SpanReader] = ()) -> None:
        self.store = store
        self.target = target
        self.also = list(also)

    async def _read(self, call: Callable[..., _T], *args: Any) -> _T:
        try:
            return await asyncio.to_thread(call, *args)
        except (sqlite3.Error, OSError) as exc:
            raise TraceError(f"the trace store can't be read: {type(exc).__name__}") from exc

    async def own_pages(
        self, start: float, end: float, trace_id: str | None
    ) -> AsyncIterator[list[Span]]:
        before = None
        while True:
            rows = await self._read(self.store.page, self.target, start, end, trace_id, before)
            if rows:
                yield [_span(row) for row in rows]
            if len(rows) < PAGE:
                return
            before = (rows[-1][5], rows[-1][0])

    async def project_exists(self) -> bool:
        return await self._read(self.store.has, self.target)

    def trace_url(self, trace_id: str | None) -> str | None:
        return None
