"""Reading a try's spans back (docs/judging.md, "Traces"): when they have all arrived, and which
are its own.

Spans come after a turn ends, in batches. A read is taken as complete when the count of AI spans has
held still across two reads more than ``traces.settle_seconds`` apart (the default is above
OpenTelemetry's 5 s batch) and every turn that finished has AI spans of its own. That second part is
what catches one batch much later than the rest, which a still count can't.

A read that is plainly whole doesn't wait that long (:func:`whole`): every trace in it has its root
span, and every span's parent is in it. Spans are sent when they end, so a root that has come has
ended, and so has everything under it. Then nothing new for :data:`QUIET_SECONDS` is enough, with
every finished turn covered as before. A root that can't be told (it hasn't come, or it hangs from a
span of the caller's that is never sent) falls back to ``settle_seconds``. Either still not so at
:data:`TRACE_WAIT`, or a read cut by the span cap, and the read is partial. A read costs the whole
window, since a late batch carries old start times, so the reads space out up to ``settle_seconds``.

Which spans are a try's is told by ids (:func:`own_spans`); the time window only bounds the read,
and runs on to the moment of each read, since an agent may go on after its reply. Traces are found
by what carries this try's ``evalution.run`` and ``evalution.try`` when the app copies them onto its
spans, by the adapter naming them, and by its session's trace values. A trace marked as another
try's isn't this one's, whatever its session says. Without marks, a value an earlier try also played
on (``reset`` may keep it) counts only from the try's first message, so nothing of the try before is
read as this one's.

A found trace may hold other people's work too, so a span in another session counts only when it is
linked: its parent is one of the try's, or its session names one of the try's as its parent
(``PARENT_SESSION_ATTRIBUTES``), and then the rest of that child session, wherever it is. A span
saying no session takes the side of the span above it. Another session in the same trace or window
that links to none of these is not the try's, however close in time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace

import httpx

from evalution.core.evidence import TraceStatus
from evalution.traces.base import (
    Span,
    TraceSource,
    ancestors,
    by_trace,
    get,
    is_ai,
    oldest,
    parent_session_of,
    session_of,
)
from evalution.traces.view import sessions_started_by_tools

_logger = logging.getLogger(__name__)

TRACE_WAIT = 20.0
_POLL = 2.0
#: Seconds a turn's first span may seem to start outside the turn: clocks differ a little.
_TURN_SKEW = 1.0
#: Seconds with nothing new that end the wait on a read that is plainly whole.
QUIET_SECONDS = 1.5
#: Spans that, with nothing above them, show no turn that ended.
_NOT_A_TURN = frozenset({"LLM", "TOOL"})


@dataclass(frozen=True)
class Collected:
    spans: list[Span]
    status: TraceStatus
    note: str | None = None


@dataclass(frozen=True)
class Wanted:
    """Which spans a try is after.

    :param values: Its session's trace values.
    :param reused: Those an earlier try played on too.
    :param windows: ``(started, ended)`` of each turn that finished.
    :param marks: The ``evalution.run`` and ``evalution.try`` its spans carry, if the app copies
        them onto its spans.
    :param not_before: Nothing that started before this is the try's, whatever it carries: a second
        go keeps the first's session and marks.
    """

    attribute: str
    values: Sequence[str]
    reused: Collection[str]
    trace_ids: Sequence[str]
    t0: float
    end: float
    windows: Sequence[tuple[float, float]]
    marks: tuple[str, str] | None = None
    not_before: float = float("-inf")


def _is_marked(span: Span, marks: tuple[str, str] | None) -> bool | None:
    """Whether *span* says it is this try's; ``None`` when it doesn't say whose it is."""
    run, n = get(span.attrs, "evalution.run"), get(span.attrs, "evalution.try")
    if marks is None or run is None or n is None:
        return None
    return (str(run), str(n)) == marks


def _session_above(span: Span, by_id: Mapping[str, Span], attribute: str) -> str | None:
    """The session *span* is in: its own, or that of the nearest span above it that says."""
    for at in (span, *ancestors(span, by_id)):
        if (found := session_of(at, attribute)) is not None:
            return str(found)
    return None


@dataclass
class _Reading:
    """What is known of a try while its window is read: its spans so far, the sessions those are
    explicitly tied to it by, and every span of the window."""

    wanted: Wanted
    kept: dict[str, Span]
    known: set[str]
    by_id: Mapping[str, Span]

    def _is_ours(self, span: Span) -> bool:
        """Whether *span* is the try's: it says so, it is in or says nothing beyond a session the
        try is known by, or it starts another from a span or a session of the try's. A trace may
        hold several people's work, so being found in one isn't enough."""
        attribute, known = self.wanted.attribute, self.known
        if _is_marked(span, self.wanted.marks):
            return True
        if session_of(span, attribute) is None:
            above = _session_above(span, self.by_id, attribute)
            return above is None or above in known
        if str(session_of(span, attribute)) in known or parent_session_of(span) in known:
            return True
        parent = self.kept.get(span.parent or "")
        return parent is not None and (
            bool(_is_marked(parent, self.wanted.marks))
            or _session_above(parent, self.by_id, attribute) in known
        )

    def keep(self, group: Sequence[Span], floor: float) -> None:
        """Those of *group* that are the try's: not from before *floor*, or marked as another's."""
        floor = max(floor, self.wanted.not_before)
        rest = [
            s for s in group if s.start >= floor and _is_marked(s, self.wanted.marks) is not False
        ]
        while True:
            found = [s for s in rest if s.id not in self.kept and self._is_ours(s)]
            if not found:
                return
            for span in found:
                self.kept[span.id] = span
                if (session := session_of(span, self.wanted.attribute)) is not None:
                    self.known.add(str(session))

    def named(self, group: Sequence[Span]) -> None:
        """A trace the adapter named, of an app with session ids of its own: all that ties it to
        the try is that naming, so the session it began in is the try's."""
        says = [str(v) for s in group if (v := session_of(s, self.wanted.attribute)) is not None]
        if says and not self.known.intersection(says):
            self.known.add(says[0])


def _follow(
    traces: Mapping[str, Sequence[Span]],
    reading: _Reading,
    floors: Mapping[str, float],
    others: Collection[str],
) -> None:
    """Add the work delegated work left, until there is no more. A trace found by a session isn't
    read from before the try: that value's only tie to the try may be an old one."""
    attribute = reading.wanted.attribute
    sessions = {str(v) for g in traces.values() for s in g if (v := session_of(s, attribute))}
    while True:
        size = len(reading.kept)
        reading.known |= sessions_started_by_tools(reading.kept.values(), sessions - reading.known)
        for trace_id, group in traces.items():
            if trace_id in others:
                continue
            floor = floors.get(trace_id)
            if floor is None and any(s.parent in reading.kept for s in group):
                floor = float("-inf")
            elif floor is None and any(
                parent_session_of(s) in reading.known
                or str(session_of(s, attribute)) in reading.known
                for s in group
            ):
                floor = reading.wanted.t0
            if floor is not None:
                reading.keep(group, floor)
        if len(reading.kept) == size:
            return


def own_spans(window: Sequence[Span], wanted: Wanted, *, follow: bool = False) -> list[Span]:
    """The spans of *window* that are the try's, told by ids (the module's docstring)."""
    values, reused = set(wanted.values), set(wanted.reused)
    reading = _Reading(wanted, {}, set(values), {s.id: s for s in window})
    floors: dict[str, float] = {}
    others: set[str] = set()
    traces = by_trace(window)
    for trace_id, group in traces.items():
        marks = {_is_marked(s, wanted.marks) for s in group}
        found = {str(session_of(s, wanted.attribute)) for s in group} & values
        if True in marks or trace_id in wanted.trace_ids:
            if True not in marks:
                reading.named(group)
            floors[trace_id] = float("-inf")
        elif found and False not in marks:
            floors[trace_id] = wanted.t0 if found & reused else float("-inf")
        elif False in marks:
            others.add(trace_id)
    for trace_id, floor in floors.items():
        reading.keep(traces[trace_id], floor)
    if follow and reading.kept:
        _follow(traces, reading, floors, others)
    return list(reading.kept.values())


async def read_spans(
    source: TraceSource, wanted: Wanted, *, follow: bool = False
) -> tuple[list[Span], bool]:
    """One read of the try's spans, its delegated work too with *follow*; whether it was cut."""
    read = await source.window(wanted.t0, wanted.end)
    own = oldest(own_spans(read.spans, wanted, follow=follow), read.cut)
    return own.spans, own.cut


def _covered(spans: Sequence[Span], windows: Sequence[tuple[float, float]]) -> bool:
    """Whether every finished turn has an AI span that started in it."""
    starts = [s.start for s in spans if is_ai(s)]
    return all(
        any(begin - _TURN_SKEW <= at <= end + _TURN_SKEW for at in starts) for begin, end in windows
    )


def whole(spans: Sequence[Span]) -> bool:
    """Whether *spans* are plainly all there: each span's parent is among them, so every span hangs
    from a root that has come (a span with no parent; delegated work in a trace of its own hangs
    from the try's), and every root encloses a turn: no model call or tool call stands alone, since
    one of those shows no turn that ended, and the agent may go on after it. A span comes only once
    it ends, so this holds only when every root, and everything under it, has ended."""
    ids = {s.id for s in spans}
    roots = [s for s in spans if s.parent is None]
    return (
        bool(roots)
        and all(s.kind not in _NOT_A_TURN for s in roots)
        and all(s.parent is None or s.parent in ids for s in spans)
    )


async def collect(
    source: TraceSource,
    wanted: Wanted,
    settle: float,
    *,
    once: bool = False,
    wait: float | None = None,
) -> Collected:
    """Read until the spans have settled, or :data:`TRACE_WAIT`, or *wait* when nothing is expected
    (an earlier try of the run waited it out and found none). With *once*, one read: the app is
    gone, so nothing more is coming. Never raises for a source that doesn't answer."""
    began = time.time()
    stop = began + (max(TRACE_WAIT, settle + 2 * _POLL) if wait is None else wait)
    spans: list[Span] = []
    count, since = -1, time.time()
    seen, quiet_since = -1, since
    settled = False
    wait = _POLL
    while True:
        try:
            # Up to now: what the agent does after its reply is still this try's.
            reading = replace(wanted, end=max(wanted.end, time.time()))
            spans, cut = await read_spans(source, reading, follow=True)
        except (httpx.HTTPError, ValueError) as exc:
            _logger.warning("evals: spans not read", exc_info=True)
            status = TraceStatus.PARTIAL if count > 0 else TraceStatus.UNREAD
            return Collected(spans, status, f"reading the traces: {exc}")
        now = time.time()
        ai = sum(map(is_ai, spans))
        if ai != count:
            count, since = ai, now
        if len(spans) != seen:
            seen, quiet_since = len(spans), now
        covered = ai > 0 and _covered(spans, wanted.windows)
        # Plainly whole and quiet a moment, or else still for the whole settle.
        quick = now - quiet_since > min(QUIET_SECONDS, settle) and whole(spans)
        settled = covered and (quick or now - since > settle)
        if cut:
            return Collected(spans, TraceStatus.PARTIAL, "the read hit the span cap")
        if settled or once or now >= stop:
            break
        await asyncio.sleep(min(wait, stop - now))
        wait = min(wait * 2, max(_POLL, settle))
    if not count:
        return Collected(spans, TraceStatus.EMPTY)
    if settled:
        return Collected(spans, TraceStatus.COMPLETE)
    note = "the try ended early" if once else f"still arriving after {now - began:.0f} s"
    return Collected(spans, TraceStatus.PARTIAL, note)
