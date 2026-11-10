"""A try whose traces are plainly whole stops waiting early; one that can't be told waits the full
settle (runs/collect.py)."""

import time

from evalution.core.evidence import TraceStatus
from evalution.runs import collect as collect_module
from evalution.runs.collect import Wanted, collect, whole
from evalution.traces.base import Span, SpanReader, nest

SETTLE = 0.6


class _Spans(SpanReader):
    def __init__(self, spans) -> None:
        self.held = spans

    async def own_pages(self, start, end, trace_id):
        yield [s for s in self.held if start <= s.start <= end]

    def trace_url(self, trace_id):
        return None


def _span(sid, trace, start, kind="AGENT", parent=None):
    return Span(sid, trace, parent, sid, kind, start, start + 0.5, "OK", nest({"session.id": "s1"}))


def _wanted(t0: float) -> Wanted:
    return Wanted("session.id", ["s1"], (), (), t0, t0 + 2, [(t0, t0 + 2)])


async def _timed(spans, t0: float, monkeypatch) -> tuple[float, TraceStatus]:
    monkeypatch.setattr(collect_module, "_POLL", 0.02)
    monkeypatch.setattr(collect_module, "QUIET_SECONDS", 0.05)
    began = time.time()
    found = await collect(_Spans(spans), _wanted(t0), settle=SETTLE)
    return time.time() - began, found.status


def test_whole_means_every_trace_has_its_root_and_every_parent() -> None:
    t0 = 0.0
    closed = [_span("root", "t", t0), _span("llm", "t", t0 + 0.1, "LLM", "root")]
    assert whole(closed)
    # The root hasn't come: the turn is still open, or the app's root hangs from a span of the
    # caller's that is never sent. Either way it can't be told.
    assert not whole(closed[1:])
    # Delegated work in a trace of its own, hanging from a span of the try's: whole.
    kid = _span("kid", "t2", t0 + 0.2, "AGENT", "llm")
    assert whole([*closed, kid])
    assert not whole([*closed, _span("kid", "t2", t0, "AGENT", "gone")])
    assert not whole([])
    # A model call or a tool call with nothing above it shows no turn that ended.
    assert not whole([_span("llm", "t", t0, "LLM")])
    assert not whole([*closed, _span("late", "t3", t0 + 4, "TOOL")])


async def test_a_closed_trace_stops_early(monkeypatch) -> None:
    t0 = time.time()
    spans = [_span("root", "t", t0), _span("llm", "t", t0 + 0.1, "LLM", "root")]
    took, status = await _timed(spans, t0, monkeypatch)
    assert status is TraceStatus.COMPLETE
    assert took < SETTLE / 2, f"waited {took:.2f} s on a trace plainly whole"


async def test_an_open_trace_waits_the_settle(monkeypatch) -> None:
    t0 = time.time()
    spans = [_span("llm", "t", t0 + 0.1, "LLM", "root-not-yet")]
    took, status = await _timed(spans, t0, monkeypatch)
    assert status is TraceStatus.COMPLETE
    assert took >= SETTLE, "a root not seen falls back to the settle rule"
