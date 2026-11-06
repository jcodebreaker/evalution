"""The built-in trace source (docs/architecture.md, "Traces"): spans kept by evalution itself, in
its database (:mod:`evalution.storage.spans`), each workspace's apart. No vendor, nothing to run.
They arrive over OTLP (:mod:`evalution.traces.otlp`) and are read back through
:class:`evalution.traces.base.TraceSource`; :func:`reader` adds the app's own Phoenix when it has
one.

Retention is by age alone: :func:`prune` drops spans received more than ``traces.keep_days`` ago,
once a day, from ``serve``. Age is by when a span arrived, not by the time it claims.

Span contents may include prompts, so nothing here logs them.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import SQLAlchemyError

from evalution.config import PhoenixConfig
from evalution.storage.database import Database, upsert
from evalution.storage.spans import spans
from evalution.traces.base import Span, SpanReader, TraceError, TraceSource, kind_of, nest
from evalution.traces.phoenix import Phoenix

PAGE = 1_000
DAY = 86_400.0
#: The resource attribute that names a span's target, the one Phoenix's tooling sets (a project,
#: which may carry the prefix), and what spans naming neither are kept under.
TARGET_ATTRIBUTE = "evalution.target"
PROJECT_ATTRIBUTE = "openinference.project.name"
TARGET_PREFIX = "evalution-"
DEFAULT_TARGET = "default"
_T = TypeVar("_T")
_COLUMNS = (
    spans.c.id,
    spans.c.trace_id,
    spans.c.span_id,
    spans.c.parent,
    spans.c.name,
    spans.c.started,
    spans.c.ended,
    spans.c.status,
    spans.c.attrs,
)
#: What identifies a span: sent again, it replaces itself (exporters retry).
_SAME = ("workspace", "trace_id", "span_id")


def project(target: str) -> str:
    """The project name an app traced with Phoenix's ``register()`` is told to use; the intake
    takes the prefix off again (:mod:`evalution.traces.otlp`)."""
    return f"{TARGET_PREFIX}{target}"


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

    def row(self, workspace: str, now: float) -> dict[str, Any]:
        return {
            "workspace": workspace,
            "target": self.target,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent": self.parent,
            "name": self.name,
            "started": self.start,
            "ended": self.end,
            "status": self.status,
            "attrs": json.dumps(self.attrs, ensure_ascii=False),
            "ai": kind_of(nest(self.attrs)) != "UNKNOWN",
            "received": now,
        }


class SpanStore:
    """One workspace's spans. Every method blocks: call it on a worker thread."""

    def __init__(self, database: Database, workspace: str) -> None:
        self.database = database
        self.workspace = workspace

    def add(self, received: Iterable[Received], now: float | None = None) -> None:
        """Keep *received*, a span sent again replacing itself."""
        at = time.time() if now is None else now
        rows = [s.row(self.workspace, at) for s in received]
        if not rows:
            return
        new = upsert(self.database, spans)
        again = {c.name: new.excluded[c.name] for c in spans.c if c.name not in ("id", *_SAME)}
        with self.database.engine.begin() as db:
            db.execute(new.on_conflict_do_update(index_elements=list(_SAME), set_=again), rows)

    def page(
        self,
        target: str,
        start: float,
        end: float,
        trace_id: str | None,
        before: tuple[float, int] | None,
    ) -> list[Any]:
        """Up to :data:`PAGE` rows of *target*, newest first, older than *before* (a row's
        ``(started, id)``)."""
        c = spans.c
        where = [c.workspace == self.workspace, c.target == target, c.started.between(start, end)]
        if trace_id:
            where.append(c.trace_id == trace_id)
        if before:
            at, last = before
            where.append(or_(c.started < at, and_(c.started == at, c.id < last)))
        query = select(*_COLUMNS).where(*where).order_by(c.started.desc(), c.id.desc()).limit(PAGE)
        with self.database.engine.begin() as db:
            return list(db.execute(query).all())

    def has(self, target: str) -> bool:
        c = spans.c
        query = select(c.id).where(c.workspace == self.workspace, c.target == target).limit(1)
        with self.database.engine.begin() as db:
            return db.scalar(query) is not None


def prune(database: Database, keep_days: int, now: float | None = None) -> int:
    """Drop every workspace's spans received more than *keep_days* ago; how many went."""
    cutoff = (time.time() if now is None else now) - keep_days * DAY
    with database.engine.begin() as db:
        return int(db.execute(delete(spans).where(spans.c.received < cutoff)).rowcount or 0)


def _span(row: Any) -> Span:
    attrs = nest(json.loads(row.attrs))
    return Span(
        id=row.span_id,
        trace_id=row.trace_id,
        parent=row.parent,
        name=row.name,
        kind=kind_of(attrs),
        start=row.started,
        end=row.ended,
        status=row.status,
        attrs=attrs,
    )


class Builtin(SpanReader):
    """One target's spans in the store, and the *also* sources read with it. Spans have no page
    of their own here, so a trace's link is the first *also* source's that has one."""

    def __init__(self, store: SpanStore, target: str, also: Sequence[SpanReader] = ()) -> None:
        self.store = store
        self.target = target
        self.also = list(also)

    async def _read(self, call: Callable[..., _T], *args: Any) -> _T:
        try:
            return await asyncio.to_thread(call, *args)
        except SQLAlchemyError as exc:
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
            before = (rows[-1].started, rows[-1].id)

    async def project_exists(self) -> bool:
        return await self._read(self.store.has, self.target)

    def trace_url(self, trace_id: str | None) -> str | None:
        return next((url for s in self.also if (url := s.trace_url(trace_id))), None)


def reader(
    target: str, own: PhoenixConfig | None, store: SpanStore, api_key: str | None = None
) -> TraceSource:
    """Where a target's traces are read: its spans in *store*, then the ``default`` ones (from an
    app that can't name its target), then the app's own Phoenix when the target has one, which
    is also where a trace's link goes."""
    also: list[SpanReader] = [Builtin(store, DEFAULT_TARGET)]
    if own:
        also.append(Phoenix(own, api_key=api_key))
    return Builtin(store, target, also=also)
