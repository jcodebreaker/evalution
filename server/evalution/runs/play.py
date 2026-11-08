"""Playing one try through a target's adapter, then reading what happened from its traces
(docs/architecture.md, "The Target protocol").

Turns play in order, each returning when the adapter says it is done. The case's ``seconds``
limit, or :data:`DEFAULT_SECONDS`, caps the whole try. A filler turn sends N messages written
by the set's filler model. A case with a ``fresh`` turn gets a new token in
``Session.extra["fresh"]`` each try, for the adapter to carry where the person cannot see it;
the message itself is never changed. A state check is recorded as it was made: ``context_*`` keys
are decided later from the model calls after it; any other key is the adapter's (its
``state_checks``). A ``{"break": "conversation"}`` turn asks the adapter for a new conversation for
the same person (``conversation_break``). What comes of it all is a
:class:`~evalution.core.evidence.TryEvidence`.

Before the first message, an adapter that declares ``probe`` reads the case's ``*_contains`` state
checks. One that already holds was left by an earlier try: this one isn't played, and can't be
judged (:mod:`evalution.runs.leftover`).

Work a try leaves going (a turn given up on, out of time, or stopped to wait) is cancelled when the
adapter can, so the next try doesn't start on top of it. :func:`play_with_retry` gives a try that
couldn't reach the app at its very first message, or found it busy, one more go, when what it met
shows the message wasn't taken: a connection error, a 429 or a 503.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from evalution.adapters.base import (
    NO_FILES,
    NO_STOP,
    UNREACHABLE_ERRORS,
    AppError,
    Session,
    StateResult,
    Target,
    TargetGone,
    Turn,
    failure_of,
    lost_connection,
    may_have_heard,
)
from evalution.core import net
from evalution.core.case import CONTEXT_CHECKS, Case, CaseTurn
from evalution.core.evidence import (
    Clipped,
    Failure,
    MemoryRecord,
    ModelCallRecord,
    PageRecord,
    StateRecord,
    ToolRecord,
    TraceStatus,
    TryEvidence,
    TurnEnd,
    TurnRecord,
)
from evalution.core.rules import NO_TRACES
from evalution.judging.rubric import FillerWriter
from evalution.judging.steps import EXTRA_OK, Step
from evalution.runs.collect import Wanted, collect, read_spans
from evalution.runs.leftover import leftover
from evalution.runs.page.map import PageMapBroken
from evalution.runs.page.target import PageFiles, PageTarget
from evalution.traces.base import Span, TraceSource
from evalution.traces.view import TraceView, answered_at, build

_logger = logging.getLogger(__name__)

DEFAULT_SECONDS = 300.0
_FILLER_TURNS = 6
DEFAULT_SETTLE = 6.0
#: Seconds between looks at a turn that hasn't come back.
WATCH_EVERY = 15.0
#: Seconds past its deadline a turn may take, for an adapter that is on its way back.
DEADLINE_GRACE = 30.0
#: Seconds after the agent's answer a turn can still go wrong and be the app's doing.
MISSED_AFTER = 30.0
#: Adapter failures in a row that end a run: one may be a misreading of that try alone.
ADAPTER_FAILURES = 2
#: Seconds before a try that couldn't reach the app at its first message goes again.
RETRY_SECONDS = 5.0
#: Seconds an adapter may take over anything but a turn, which has its own deadline.
ADAPTER_SECONDS = 120.0
#: Seconds a cancel may take: it never holds up what comes after.
CANCEL_SECONDS = 15.0
_sleep = asyncio.sleep
#: A turn's error when the adapter gave none, by how it ended.
_ENDED_WORDS = {
    TurnEnd.BLOCKED: "The app stopped to wait on something",
    TurnEnd.FAILED: "The app said its work failed",
}
#: Which side a turn's end is on, when it says.
_ENDED_FAILURE = {
    TurnEnd.BLOCKED: Failure.BLOCKED,
    TurnEnd.FAILED: Failure.APP,
}
#: What may leave work going in the app.
_LEAVES_WORK = (Failure.TIMED_OUT, Failure.ADAPTER, Failure.BLOCKED, Failure.TRANSIENT)
#: What an answer with an error status comes to, ahead of what the adapter says of it.
_FAILURE_WORDS = {
    Failure.REFUSED: "The app refused the credentials",
    Failure.TRANSIENT: "The app, or a gateway in front of it, was busy or down",
}
#: Told each message about to be sent of a try: which, and of how many.
OnTurn = Callable[[int, int], Awaitable[None]]
#: Told when a try moves on to another stage.
OnStage = Callable[[str], Awaitable[None]]
READING_TRACES = "reading traces"


@dataclass
class Played:
    """What one try did. ``t0`` is when the first message was sent; span times are from it."""

    turns: list[dict[str, str]] = field(default_factory=list)
    filler: list[dict[str, str]] = field(default_factory=list)
    reply_text: str = ""
    t0: float = 0.0
    seconds: float = 0.0
    trace_ids: list[str] = field(default_factory=list)
    error: str | None = None
    failure: Failure | None = None
    records: list[TurnRecord] = field(default_factory=list)
    states: list[StateRecord] = field(default_factory=list)
    memory: MemoryRecord | None = None
    view: TraceView = field(default_factory=TraceView)
    trace_url: str | None = None
    #: The session's trace values before a conversation break changed it, whose spans count too.
    earlier_values: list[str] = field(default_factory=list)
    #: What it all comes to; set when the try is over.
    evidence: TryEvidence | None = None
    #: Where a new conversation started: how many turns had been played by then.
    breaks: list[int] = field(default_factory=list)
    #: When the latest turn was sent, and what it said.
    sent_at: float = 0.0
    sent_text: str | None = None
    on_turn: OnTurn | None = None
    turns_planned: int = 0
    turns_sent: int = 0
    #: The adapter said the work in flight was stopped.
    cancelled: bool = False
    #: Why it took a second go: ``{what, why}``, as a try keeps it.
    retried: list[dict[str, str]] = field(default_factory=list)
    #: The adapter saw the app take a message.
    heard: bool = False
    #: The connection broke while the adapter waited: no answer from the app says it failed.
    lost: bool = False
    #: What the app's page showed, when the try played through it (ADR 0010).
    page: PageRecord | None = None

    @property
    def gone(self) -> bool:
        """No use going on: the app can't be reached or won't take the credentials. The run ends
        after this try. An adapter's failure takes two in a row (:data:`ADAPTER_FAILURES`)."""
        return self.failure in (Failure.UNREACHABLE, Failure.REFUSED)

    @property
    def worth_retrying(self) -> bool:
        """It couldn't reach the app at its first message or found it busy, and nothing shows the
        app heard it. A gateway's 502 or 504 may have passed it on: the adapter calls that heard."""
        evidence = self.evidence
        return (
            self.failure in (Failure.UNREACHABLE, Failure.TRANSIENT)
            and self.turns_sent == 1
            and not self.heard
            and evidence is not None
            and not evidence.called
            and not evidence.spans
        )


class _TryEnded(Exception):
    """Why is on the :class:`Played`."""


async def play_try(
    target: Target,
    session: Session,
    case: Case,
    case_dir: Path,
    *,
    filler: FillerWriter | None,
    source: TraceSource | None,
    n: int = 1,
    reused: set[str] | None = None,
    settle: float = DEFAULT_SETTLE,
    stuck: float = 0.0,
    trace_wait: float | None = None,
    on_turn: OnTurn | None = None,
    on_stage: OnStage | None = None,
    second_go: bool = False,
    unclean: str | None = None,
    page_files: PageFiles | None = None,
) -> Played:
    """Play *case* once in *session* and read its spans back. Never raises for a failed turn (that
    is ``Played.failure``). *reused* holds the trace values earlier tries of the run played on, and
    gets this one's. *stuck* is how long after the agent's answer the adapter may go on waiting.
    *second_go*: what started before this go is the go before's, whatever marks it carries.
    *unclean*: why the app can't be taken to start clean (a reset of evalution's copy that didn't
    hold); the try isn't played, as when it starts holding what an earlier one left.
    *page_files*: where a try played through the app's page keeps its screenshots and video."""
    left_over = unclean or await leftover(target, session, case)
    on_page = target if isinstance(target, PageTarget) else None
    if on_page is not None:
        on_page.begin(session, page_files)
    played = Played(t0=time.time())
    limit = float(case.limits.get("seconds") or DEFAULT_SECONDS)
    deadline = played.t0 + limit
    last_end = played.t0
    if target.capabilities.background:
        stuck = 0.0
    watch = _Watch(source if stuck else None, stuck, reused or set())
    played.on_turn = on_turn
    played.turns_planned = sum(t.filler or int(t.is_message) for t in case.turns)
    if any(turn.fresh for turn in case.turns):
        session.extra["fresh"] = secrets.token_hex(4)
    try:
        if left_over:
            _ended(played, Failure.LEFTOVER_STATE, left_over)
            raise _TryEnded
        if case.fold_at_tokens:
            await asyncio.wait_for(target.fold_at(session, case.fold_at_tokens), ADAPTER_SECONDS)
        for turn in case.turns:
            if turn.state is not None:
                await _check_state(target, session, played, turn.state)
            elif turn.memory_holds is not None:
                await _read_memory(target, session, played, turn.memory_holds)
            elif turn.new_conversation:
                await _new_conversation(target, session, played)
            elif turn.filler:
                last_end = await _play_filler(
                    target, session, played, turn, filler, deadline, watch
                )
            else:
                files = [case_dir / name for name in turn.files]
                said = await _send(
                    target,
                    session,
                    played,
                    turn.message,
                    files,
                    turn.stop_after,
                    deadline,
                    watch,
                    expect_blocked=turn.expect_blocked,
                )
                played.turns.append({"message": turn.message, "reply": said.reply})
                last_end = said.ended
    except _TryEnded:
        pass
    except TargetGone:
        _ended(played, Failure.UNREACHABLE, "the app's session went away")
    except UNREACHABLE_ERRORS as exc:
        _ended(played, Failure.UNREACHABLE, net.said(exc))
    except (httpx.TimeoutException, TimeoutError) as exc:
        _ended(played, Failure.TIMED_OUT, net.said(exc))
    except AppError as exc:
        _ended(played, Failure.APP, net.said(exc))
    except httpx.HTTPError as exc:
        failure = failure_of(exc)
        played.heard = played.heard or may_have_heard(exc)
        played.lost = lost_connection(exc)
        _ended(played, failure, _worded(failure, net.said(exc)))
    except PageMapBroken as exc:
        # The page target's: the page lacks what its map names, which is neither side's fault.
        _ended(played, Failure.PAGE_MAP, str(exc))
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        # Nothing the app answered: an adapter tells that as a turn's error, not by raising.
        _ended(played, Failure.ADAPTER, net.said(exc))
    # A try cut short ran until now: its spans up to here are its own.
    ended = time.time() if played.failure else max(last_end, played.t0)
    played.seconds = round(ended - played.t0, 1)
    waiting = bool(played.records) and played.records[-1].ended_as is TurnEnd.BLOCKED
    if (played.failure in _LEAVES_WORK or waiting) and target.capabilities.cancel:
        played.cancelled = await cancel_quietly(target, session)
    status, note = TraceStatus.NO_SOURCE, None
    if source is not None:
        if on_stage is not None:
            await on_stage(READING_TRACES)
        status, note = await _read_traces(
            target,
            session,
            played,
            source,
            reused if reused is not None else set(),
            settle,
            stuck,
            second_go,
            trace_wait,
        )
    if on_page is not None:
        cut_short = played.failure is Failure.TIMED_OUT or played.cancelled
        played.page = await on_page.record(session, played.t0, cut_short=cut_short)
    played.evidence = _evidence(case, n, played, status, note)
    return played


async def play_with_retry(
    target: Target,
    session: Session,
    case: Case,
    case_dir: Path,
    **options: Any,
) -> Played:
    """:func:`play_try`, and once more after a short wait when :attr:`Played.worth_retrying`. The
    second go is the try; ``retried`` says it took two. If it can't get going again, the first
    stands, a reset that ran out of time included."""
    played = await play_try(target, session, case, case_dir, **options)
    if not played.worth_retrying:
        return played
    retried = [{"what": "app", "why": played.error or ""}]
    await _sleep(RETRY_SECONDS)
    try:
        if target.capabilities.reset:
            await asyncio.wait_for(target.reset(session), ADAPTER_SECONDS)
        again = await play_try(target, session, case, case_dir, second_go=True, **options)
    except (TargetGone, httpx.HTTPError, OSError, RuntimeError, ValueError):
        played.retried = retried
        return played
    again.retried = retried
    return again


def _ended(played: Played, failure: Failure, error: str) -> None:
    played.failure, played.error = failure, error


def _worded(failure: Failure, error: str) -> str:
    words = _FAILURE_WORDS.get(failure)
    return f"{words}: {error}" if words else error


@dataclass(frozen=True)
class _Watch:
    """What a turn is watched by while it's out: the traces, when there are any."""

    source: TraceSource | None
    stuck: float
    reused: Sequence[str] | set[str]


async def _still_out(
    session: Session, played: Played, watch: _Watch, deadline: float, kind_of_wait: str
) -> str:
    """Wait until the turn has gone on too long, and say why: well past its deadline, or long after
    the traces show the agent answered. Never returns while neither holds."""
    while True:
        await _sleep(WATCH_EVERY)
        now = time.time()
        if now > deadline + DEADLINE_GRACE:
            return (
                f"The adapter hadn't come back {now - played.t0:.0f} s in, past the "
                f"{deadline - played.t0:.0f} s limit"
            )
        if watch.source is None:
            continue
        values = [*played.earlier_values, session.trace_value]
        wanted = Wanted(
            session.trace_attribute,
            values,
            watch.reused,
            (),
            played.t0,
            now,
            (),
            _marks(session),
        )
        try:
            spans, _ = await read_spans(watch.source, wanted)
        except (httpx.HTTPError, ValueError):
            continue
        at = answered_at(spans, played.sent_at, asked=played.sent_text)
        if at is not None and now - at >= watch.stuck:
            return _kept_waiting(played, at, now, kind_of_wait)


def _kept_waiting(played: Played, at: float, now: float, what: str) -> str:
    return (
        f"The agent finished at {at - played.t0:.0f} s, but the adapter was still {what} at "
        f"{now - played.t0:.0f} s. Check how it reads a finished turn. If the app works on after "
        "it answers, set background on the target."
    )


async def _come_back(
    target: Target,
    session: Session,
    played: Played,
    text: str,
    files: list[Path],
    stop_after: float | None,
    deadline: float,
    watch: _Watch,
) -> Turn | str:
    """The turn, or why it was given up on. An adapter is trusted to keep to *deadline* and to read
    a finished turn, but nothing here depends on it."""
    sending = asyncio.ensure_future(
        target.send(session, text, files=files, stop_after=stop_after, deadline=deadline)
    )
    watching = None
    try:
        await asyncio.sleep(0)
        if not sending.done():
            watching = asyncio.ensure_future(
                _still_out(session, played, watch, deadline, "waiting")
            )
            await asyncio.wait({sending, watching}, return_when=asyncio.FIRST_COMPLETED)
        return sending.result() if sending.done() else watching.result()  # type: ignore[union-attr]
    finally:
        for task in (sending, watching):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def _send(
    target: Target,
    session: Session,
    played: Played,
    text: str,
    files: list[Path],
    stop_after: float | None,
    deadline: float,
    watch: _Watch,
    kind: str = "message",
    *,
    expect_blocked: bool = False,
) -> Turn:
    played.turns_sent += 1
    if played.on_turn is not None:
        await played.on_turn(played.turns_sent, played.turns_planned)
    played.sent_at, played.sent_text = time.time(), text
    turn = await _come_back(target, session, played, text, files, stop_after, deadline, watch)
    if isinstance(turn, str):
        played.records.append(
            TurnRecord(
                kind=kind,
                message=Clipped(text=text),
                reply=Clipped(),
                started=played.sent_at,
                ended=time.time(),
                error=turn,
            )
        )
        _ended(played, Failure.ADAPTER, turn)
        raise _TryEnded
    played.trace_ids += [t for t in turn.trace_ids if t not in played.trace_ids]
    played.reply_text = turn.reply
    played.heard = played.heard or turn.heard
    played.lost = turn.lost
    expected = expect_blocked and turn.ended_as is TurnEnd.BLOCKED
    error = None if expected else turn.error or _ENDED_WORDS.get(turn.ended_as)
    failure = turn.failure or _ENDED_FAILURE.get(turn.ended_as) or Failure.APP
    error = _worded(failure, error) if error else error
    if not error and time.time() > deadline:
        error = f"not done after {deadline - played.t0:.0f} s"
        failure = Failure.TIMED_OUT
    played.records.append(
        TurnRecord(
            kind=kind,
            message=Clipped(text=text),
            reply=Clipped(text=turn.reply),
            started=turn.started,
            ended=turn.ended,
            error=error,
            ended_as=turn.ended_as,
            expected_blocked=expect_blocked,
            shown=None if turn.shown is None else Clipped(text=turn.shown),
        )
    )
    if error:
        _ended(played, Failure.NOT_SUPPORTED if error in (NO_FILES, NO_STOP) else failure, error)
        raise _TryEnded
    return turn


async def _play_filler(
    target: Target,
    session: Session,
    played: Played,
    turn: CaseTurn,
    filler: FillerWriter | None,
    deadline: float,
    watch: _Watch,
) -> float:
    """Send the turn's N generated messages, each written fresh from the exchanges so far. The
    filler model is evalution's own: when it fails, the app did nothing wrong."""
    if filler is None:
        _ended(played, Failure.OWN_MODEL, "no filler model to write the generated messages")
        raise _TryEnded
    ended = time.time()
    for _ in range(turn.filler):
        history = [*played.turns, *played.filler][-_FILLER_TURNS:]
        try:
            text = (await filler(turn.about, history)).strip().strip('"')
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            _ended(played, Failure.OWN_MODEL, f"the filler model failed: {exc}")
            raise _TryEnded from exc
        if not text:
            _ended(played, Failure.OWN_MODEL, "the filler model wrote an empty message")
            raise _TryEnded
        said = await _send(target, session, played, text, [], None, deadline, watch, "filler")
        played.filler.append({"message": text, "reply": said.reply})
        ended = said.ended
    return ended


async def _new_conversation(target: Target, session: Session, played: Played) -> None:
    """The same person starts a new conversation, keeping what the app remembers of them."""
    if not target.capabilities.conversation_break:
        said = "this target can't start a new conversation (no conversation_break)"
        _ended(played, Failure.NOT_SUPPORTED, said)
        raise _TryEnded
    before = session.trace_value
    await asyncio.wait_for(target.break_conversation(session), ADAPTER_SECONDS)
    played.breaks.append(len(played.records))
    if session.trace_value != before and before not in played.earlier_values:
        played.earlier_values.append(before)


async def _read_state(target: Target, session: Session, key: str, value: Any) -> StateResult:
    """The adapter's answer. A read that broke learned nothing, unless the app itself is gone."""
    try:
        return await asyncio.wait_for(target.state(session, {key: value}), ADAPTER_SECONDS)
    except (TargetGone, *UNREACHABLE_ERRORS):
        raise
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        said = f"the state couldn't be read: {net.said(exc)}"
        return StateResult(False, None, said, observed=False)


async def _read_memory(target: Target, session: Session, played: Played, want: str) -> None:
    """Keep what the app's memory holds now. Only a measurement: a failed read fails nothing."""
    compacted = None
    if "checkpoint" in target.capabilities.state_checks:
        with contextlib.suppress(Exception):
            found = await asyncio.wait_for(
                target.state(session, {"checkpoint": True}), ADAPTER_SECONDS
            )
            compacted = found.ok if found.observed else None
    try:
        read = await asyncio.wait_for(target.read_memory(session), ADAPTER_SECONDS)
    except Exception as exc:  # the try goes on: this was only ever to be measured
        said = net.said(exc) if isinstance(exc, httpx.HTTPError) else type(exc).__name__
        played.memory = MemoryRecord(want=want, error=said, compacted=compacted)
        return
    if read is None:
        played.memory = MemoryRecord(want=want, supported=False, compacted=compacted)
        return
    written = None if read.written is None else Clipped(text=read.written).capped()
    played.memory = MemoryRecord(
        want=want,
        committed=Clipped(text=read.committed).capped(),
        written=written,
        compacted=compacted,
    )


async def _check_state(
    target: Target, session: Session, played: Played, checks: dict[str, Any]
) -> None:
    for key, value in checks.items():
        fields: dict[str, Any] = {
            "index": len(played.states),
            "key": key,
            "value": value,
            "at": round(time.time() - played.t0, 2),
        }
        if key in CONTEXT_CHECKS:
            played.states.append(StateRecord(**fields))
        elif key not in target.capabilities.state_checks:
            said = "this target has no such state check"
            played.states.append(StateRecord(**fields, ok=False, supported=False, reason=said))
        else:
            found = await _read_state(target, session, key, value)
            played.states.append(
                StateRecord(
                    **fields,
                    ok=found.ok,
                    observed=found.observed,
                    actual=found.actual,
                    reason=found.reason,
                )
            )


def _marks(session: Session) -> tuple[str, str] | None:
    """The ``evalution.run`` and ``evalution.try`` an app copying them would put on its spans."""
    run, n = session.extra.get("run"), session.extra.get("try")
    return None if run is None or n is None else (str(run), str(n))


async def _read_traces(
    target: Target,
    session: Session,
    played: Played,
    source: TraceSource,
    reused: set[str],
    settle: float,
    stuck: float,
    second_go: bool = False,
    trace_wait: float | None = None,
) -> tuple[TraceStatus, str | None]:
    """The try's spans (:mod:`evalution.runs.collect`) as a view, and how far they could be read."""
    values = [*played.earlier_values, session.trace_value]
    finished = [(r.started, r.ended) for r in played.records if not r.error]
    wanted = Wanted(
        session.trace_attribute,
        values,
        frozenset(reused),
        played.trace_ids,
        played.t0,
        played.t0 + played.seconds,
        finished,
        _marks(session),
        played.t0 if second_go else float("-inf"),
    )
    # Nothing sent, nothing to wait for.
    once = played.gone or not played.turns_sent
    found = await collect(source, wanted, settle, once=once, wait=trace_wait)
    reused.update(values)
    _check_the_adapter(played, found.spans, stuck)
    if found.status in (TraceStatus.EMPTY, TraceStatus.UNREAD):
        played.error = played.error or found.note or NO_TRACES
    sectioner = target.sections if target.capabilities.sections else None
    played.view = build(found.spans, played.t0, sectioner=sectioner)
    first = (played.trace_ids or played.view.trace_ids or [None])[0]
    played.trace_url = source.trace_url(first)
    return found.status, found.note


def _check_the_adapter(played: Played, spans: Sequence[Span], stuck: float) -> None:
    """Hold what the adapter said against the traces: an empty reply to a turn the agent answered,
    or a wait that never came back well after the agent had finished (it timed out or lost the
    connection), is the adapter's, not the app's. A reply it did read, and then an error, and any
    status the app really returned, are the app's to answer for."""
    if played.failure is None:
        last = next((r for r in reversed(played.records) if r.kind == "message"), None)
        if last is None or last.reply.text.strip() or last.ended_as is TurnEnd.BLOCKED:
            return
        # The turn is over, by the adapter's word: only what the model said matters here.
        at = answered_at(spans, played.sent_at, enclosed=False)
        if at is not None:
            _ended(
                played,
                Failure.ADAPTER,
                f"The agent answered at {at - played.t0:.0f} s, but the adapter read an empty "
                "reply. Check where it reads replies from.",
            )
    elif (
        stuck
        and (played.failure in (Failure.UNREACHABLE, Failure.TIMED_OUT) or played.lost)
        and not _reply_read(played)
        and (at := answered_at(spans, played.sent_at, asked=played.sent_text)) is not None
        and played.t0 + played.seconds - at >= MISSED_AFTER
    ):
        said = _kept_waiting(played, at, played.t0 + played.seconds, "waiting")
        _ended(played, Failure.ADAPTER, f"{said} Then: {played.error}")


def _reply_read(played: Played) -> bool:
    """Whether the adapter read a reply to the latest message; a turn that raised left no record."""
    return len(played.records) == played.turns_sent and bool(played.reply_text.strip())


def _evidence(
    case: Case, n: int, played: Played, status: TraceStatus, note: str | None
) -> TryEvidence:
    view = played.view
    tools = [
        ToolRecord(
            id=call.id,
            name=call.name,
            args=Clipped(text=json.dumps(dict(call.args), ensure_ascii=False)),
            result=Clipped(text=view.results.get(call.id, "")),
            start=call.start,
            end=call.end,
            helper=call.helper,
        )
        for call in view.calls
    ]
    calls = [
        ModelCallRecord(
            n=call["n"],
            span=call["span"],
            t=call["t"],
            model=call["model"],
            sent=Clipped(text=call["text"]),
            said=Clipped(text=call["said"]),
            helper=call["helper"],
            ended=call["ended"],
        )
        for call in view.context
    ]
    return TryEvidence(
        case=case.case,
        n=n,
        t0=played.t0,
        seconds=played.seconds,
        turns=tuple(played.records),
        tools=tuple(tools),
        model_calls=tuple(calls),
        states=tuple(played.states),
        memory=played.memory,
        breaks=tuple(played.breaks),
        spans=tuple(row["id"] for row in view.timeline),
        trace=status,
        trace_note=note,
        failure=played.failure,
        error=played.error,
        page=played.page,
    )


def timeline(played: Played, steps: Sequence[Step]) -> list[dict[str, Any]]:
    """The try's ``TimelineSpan[]``: its spans, a tool span whose Step did not match (an
    allowed extra aside) carrying the reason as ``check``, and a ``missing`` span for each
    expected call that never came (its Step's ``span`` set to it)."""
    checks = {
        s.span: s.reason for s in steps if s.span and s.status != "matched" and s.reason != EXTRA_OK
    }
    spans = [
        {**row, "check": checks[row["id"]]} if row["id"] in checks else dict(row)
        for row in played.view.timeline
    ]
    first = next((s["id"] for s in spans if s["parent"] is None), None)
    by_id = {c.id: c for c in played.view.calls}
    cursor = 0.0
    for i, step in enumerate(steps):
        if step.span in by_id:
            cursor = by_id[step.span].start
        elif step.status == "missing":
            step.span = f"missing-{i}"
            spans.append(
                {
                    "id": step.span,
                    "parent": first,
                    "kind": "missing",
                    "name": step.expected or "",
                    "start": round(cursor, 2),
                    "seconds": 0.0,
                    "check": step.reason,
                }
            )
    return spans


async def cancel_quietly(target: Target, session: Session) -> bool:
    """Stop *session*'s work in flight, if the adapter can: whether it said so. A failure is logged,
    never raised."""
    if not target.capabilities.cancel:
        return False
    try:
        await asyncio.wait_for(target.cancel(session), CANCEL_SECONDS)
    except Exception:
        _logger.warning("evals: session %s not cancelled", session.id, exc_info=True)
        return False
    return True


async def close_quietly(target: Target, session: Session) -> None:
    """End *session*; a failure is logged, never raised."""
    try:
        await target.close(session)
    except Exception:
        _logger.warning("evals: session %s not closed", session.id, exc_info=True)
