"""What every trace source shares (docs/architecture.md, "Traces"): the neutral :class:`Span`,
the :class:`TraceSource` interface callers type against, and :class:`SpanReader`, which gives a
source everything but its own read of a page of spans.

A span's ``kind`` is neutral too: :func:`kind_of` reads it from OpenInference's span kind or the
OpenTelemetry GenAI conventions (``gen_ai.*``). A span with neither (a plain HTTP server span,
say) is ``UNKNOWN``: it stays readable, since it may carry the session or link a parent, but it
is not part of what the app's AI did and never counts against a read limit.

Span attributes come flat (``llm.model_name``) or nested (``{"llm": {"model_name": ...}}``)
depending on the source; :func:`nest` reads both the same way.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

_logger = logging.getLogger(__name__)

MAX_SPANS = 20_000
#: Spans of any kind read at most in one :meth:`SpanReader.spans`, however few are AI spans.
MAX_READ = 100_000
#: Seconds added around a try's window: clocks and span ends drift a little.
SLACK = 5.0

#: Span kinds that are the app's AI at work: OpenInference's. GenAI spans map onto them.
AI_KINDS = frozenset(
    {
        "AGENT",
        "CHAIN",
        "LLM",
        "TOOL",
        "RETRIEVER",
        "EMBEDDING",
        "RERANKER",
        "GUARDRAIL",
        "EVALUATOR",
        "PROMPT",
        "DECISION",
    }
)
#: ``gen_ai.operation.name``'s well-known values to span kinds. A custom value is told from the
#: other attributes, like a span that names no operation.
_OPERATIONS = {
    "chat": "LLM",
    "generate_content": "LLM",
    "text_completion": "LLM",
    "execute_tool": "TOOL",
    "invoke_agent": "AGENT",
    "embeddings": "EMBEDDING",
    "retrieval": "RETRIEVER",
    "search_memory": "RETRIEVER",
    "invoke_workflow": "CHAIN",
    "plan": "CHAIN",
    "create_agent": "CHAIN",
    "fetch_response": "CHAIN",
    "create_memory": "CHAIN",
    "create_memory_store": "CHAIN",
    "delete_memory": "CHAIN",
    "delete_memory_store": "CHAIN",
    "update_memory": "CHAIN",
    "upsert_memory": "CHAIN",
}
#: The ``gen_ai.*`` groups that say a span called a model.
_MODEL_GROUPS = {"usage", "request", "response", "input", "output"}
#: Where a conversation's id is, in either convention: asking for one finds the other.
SESSION_ATTRIBUTES = ("session.id", "gen_ai.conversation.id")
#: Where a child session names the session that started it.
PARENT_SESSION_ATTRIBUTES = ("session.parent_id", "gen_ai.conversation.parent_id")


@dataclass
class Span:
    id: str
    trace_id: str
    parent: str | None
    name: str
    kind: str  # AGENT, CHAIN, LLM, TOOL, ... (AI_KINDS), or UNKNOWN
    start: float
    end: float
    status: str
    attrs: dict[str, Any]


@dataclass
class SpanRead:
    """The spans of one session or trace, oldest first. ``cut``: there were more than the caps
    allow, and the newest went, since a try is read from its start."""

    spans: list[Span]
    cut: bool = False


def nest(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """*attrs* with dotted keys nested, and objects whose keys are all digits made lists."""
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        node = out
        parts = str(key).split(".")
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = node[part] = {}
            node = nxt
        last = parts[-1]
        if isinstance(value, dict) and isinstance(node.get(last), dict):
            node[last].update(nest(value))
        else:
            node[last] = nest(value) if isinstance(value, dict) else value
    return _lists(out)


def _lists(value: Any) -> Any:
    if isinstance(value, dict):
        value = {k: _lists(v) for k, v in value.items()}
        if value and all(k.isdecimal() for k in value):
            return [value[k] for k in sorted(value, key=int)]
        return value
    if isinstance(value, list):
        return [_lists(v) for v in value]
    return value


def get(attrs: Mapping[str, Any], path: str) -> Any:
    """The attribute at dotted *path* of nested *attrs*, ``None`` when absent."""
    node: Any = attrs
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def with_events(attrs: Mapping[str, Any], events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """*attrs* plus the ``gen_ai.*`` attributes of a span's *events* (each event's attributes):
    GenAI instrumentation may record content on an event instead. The span's own win."""
    found: dict[str, Any] = {}
    for event in events:
        found.update((k, v) for k, v in event.items() if str(k).startswith("gen_ai"))
    return {**found, **attrs} if found else dict(attrs)


def kind_of(attrs: Mapping[str, Any], given: Any = None) -> str:
    """The neutral kind of a span with nested *attrs*, *given* the kind its source says."""
    kind = str(given or get(attrs, "openinference.span.kind") or "").upper()
    if kind in AI_KINDS:
        return kind
    group = attrs.get("gen_ai")
    # A conversation id alone is not AI work: apps may put it on any span.
    if not isinstance(group, dict) or not set(group) - {"conversation"}:
        return "UNKNOWN"
    if found := _OPERATIONS.get(str(get(group, "operation.name"))):
        return found
    if get(group, "tool.name"):
        return "TOOL"
    return "LLM" if set(group) & _MODEL_GROUPS else "CHAIN"


def is_ai(span: Span) -> bool:
    return span.kind in AI_KINDS


def session_of(span: Span, attribute: str) -> Any:
    """The session *span* carries in *attribute* (either convention's name for a conversation
    id finds both), ``None`` when it carries none."""
    names = SESSION_ATTRIBUTES if attribute in SESSION_ATTRIBUTES else (attribute,)
    return next((v for n in names if (v := get(span.attrs, n)) not in (None, "")), None)


def parent_session_of(span: Span) -> str | None:
    """The session that started the one *span* is in, when it says."""
    found = next((v for n in PARENT_SESSION_ATTRIBUTES if (v := get(span.attrs, n))), None)
    return None if found is None else str(found)


def ancestors(span: Span, by_id: Mapping[str, Span]) -> Iterator[Span]:
    """The spans above *span*, nearest first. Parents come from the app, so a loop just ends."""
    seen = {span.id}
    at = by_id.get(span.parent or "")
    while at is not None and at.id not in seen:
        yield at
        seen.add(at.id)
        at = by_id.get(at.parent or "")


def by_trace(spans: Iterable[Span]) -> dict[str, list[Span]]:
    """*spans* grouped by trace, each oldest first, traces in order of their first span."""
    groups: dict[str, list[Span]] = {}
    for span in sorted(spans, key=lambda s: s.start):
        groups.setdefault(span.trace_id, []).append(span)
    return groups


def root_of(spans: Sequence[Span]) -> Span:
    """The span of a trace with no parent in it (the earliest when several)."""
    ids = {s.id for s in spans}
    roots = [s for s in spans if not s.parent or s.parent not in ids]
    return min(roots or spans, key=lambda s: s.start)


class TraceError(httpx.HTTPError):
    """A source could not be read for a reason of its own. An ``httpx.HTTPError``, so whoever
    handles a source that doesn't answer handles this too."""


class TraceSource(Protocol):
    """Where a target's spans are read from."""

    def pages(
        self, start: float, end: float, *, trace_id: str | None = None
    ) -> AsyncIterator[list[Span]]:
        """The spans that started in ``[start, end]`` (of trace *trace_id*), a page at a time."""
        ...

    async def spans(
        self, start: float, end: float, *, trace_id: str | None = None, max_spans: int = MAX_SPANS
    ) -> list[Span]:
        """Spans that started in ``[start, end]``, oldest first, up to *max_spans* AI spans."""
        ...

    async def session(self, attribute: str, value: str, start: float, end: float) -> SpanRead:
        """Every span of the traces that carry ``attribute == value`` in the window, up to
        :data:`MAX_SPANS` AI spans of theirs."""
        ...

    async def trace(self, trace_id: str, start: float, end: float) -> SpanRead: ...

    async def window(self, start: float, end: float) -> SpanRead:
        """Every span in ``[start, end]``, with a little slack for drifting clocks. ``cut``: there
        were more than :data:`MAX_READ`."""
        ...

    def trace_url(self, trace_id: str | None) -> str | None:
        """The browser link to a trace, when the source has a page for one."""
        ...

    async def project_exists(self) -> bool:
        """Whether the source knows this target's spans at all.

        :raises httpx.HTTPError: The source did not answer, or refused the key.
        """
        ...

    async def close(self) -> None: ...


def oldest(spans: Iterable[Span], cut: bool = False) -> SpanRead:
    """*spans* oldest first, up to :data:`MAX_SPANS` AI spans."""
    kept: list[Span] = []
    ai = 0
    for span in sorted(spans, key=lambda s: s.start):
        if ai >= MAX_SPANS:
            return SpanRead(kept, True)
        kept.append(span)
        ai += is_ai(span)
    return SpanRead(kept, cut)


class SpanReader:
    """A :class:`TraceSource` from its ``own_pages``, and the *also* sources read with it. Spans
    from all of them come back together, deduped by span id; an *also* source that can't be read
    is skipped."""

    also: Sequence[SpanReader] = ()

    def own_pages(
        self, start: float, end: float, trace_id: str | None
    ) -> AsyncIterator[list[Span]]:
        """This source's spans only."""
        raise NotImplementedError

    async def pages(
        self, start: float, end: float, *, trace_id: str | None = None
    ) -> AsyncIterator[list[Span]]:
        """The spans that started in ``[start, end]`` (of trace *trace_id*), a page at a time."""
        seen: set[str] = set()
        sources = [self, *self.also]
        failed: list[httpx.HTTPError] = []
        for source in sources:
            try:
                async with contextlib.aclosing(source.own_pages(start, end, trace_id)) as pages:
                    async for page in pages:
                        fresh = [s for s in page if s.id not in seen]
                        seen.update(s.id for s in fresh)
                        yield fresh
            except httpx.HTTPError as exc:
                failed.append(exc)
                _logger.info("traces: %s not read", type(source).__name__, exc_info=True)
        # One gone is no reason to lose the others; only with none read is there nothing to say.
        if len(failed) == len(sources):
            raise failed[0]

    async def spans(
        self, start: float, end: float, *, trace_id: str | None = None, max_spans: int = MAX_SPANS
    ) -> list[Span]:
        """Spans that started in ``[start, end]`` (of trace *trace_id*), oldest first. Reading
        stops at *max_spans* AI spans; other spans come along but don't count, so a flood of
        them can't crowd out the app's AI (:data:`MAX_READ` still bounds the read)."""
        found: list[Span] = []
        ai = 0
        async with contextlib.aclosing(self.pages(start, end, trace_id=trace_id)) as pages:
            async for page in pages:
                found += page
                ai += sum(map(is_ai, page))
                if ai >= max_spans or len(found) >= MAX_READ:
                    break
        return sorted(found, key=lambda s: s.start)

    async def _scan(
        self, start: float, end: float, trace_id: str | None
    ) -> tuple[list[Span], bool]:
        """Everything in the window; whether :data:`MAX_READ` stopped it first."""
        found: list[Span] = []
        async with contextlib.aclosing(self.pages(start, end, trace_id=trace_id)) as pages:
            async for page in pages:
                found += page
                if len(found) >= MAX_READ:
                    return found, True
        return found, False

    async def session(self, attribute: str, value: str, start: float, end: float) -> SpanRead:
        """Every span of the traces that carry ``attribute == value`` in the window. The cap is on
        this session's AI spans, not on all the traffic around them."""
        window, cut = await self._scan(start - SLACK, end + SLACK, None)
        traces = {s.trace_id for s in window if str(session_of(s, attribute)) == value}
        return oldest([s for s in window if s.trace_id in traces], cut)

    async def trace(self, trace_id: str, start: float, end: float) -> SpanRead:
        return oldest(*await self._scan(start, end, trace_id))

    async def window(self, start: float, end: float) -> SpanRead:
        return SpanRead(*await self._scan(start - SLACK, end + SLACK, None))

    async def close(self) -> None:
        for other in self.also:
            await other.close()
