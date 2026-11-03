"""From a try's evidence to the checks code decides (docs/judging.md): the state and context checks,
the reply's words, what tools returned, ``never``, the steps, the limits and how the try's turns
went. Pure: no model, no files, and what it
could not know it says, rather than failing or passing on it.

On a partial read of the traces only what was seen can decide. Not seeing a call, or a word in a
model call, decides nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalution.core.case import Case
from evalution.core.evidence import (
    Failure,
    ModelCallRecord,
    StateRecord,
    ToolRecord,
    TraceStatus,
    TryEvidence,
    TurnEnd,
)
from evalution.core.judgement import (
    CheckKind,
    CheckResult,
    Decider,
    EvidenceRef,
    Outcome,
    Role,
    Route,
    WhyUnjudged,
)
from evalution.core.rules import route_of, steps_bind, timeout_outcome
from evalution.judging.steps import EXTRA_OK, Step, arg_problem

_TRACE_WHY = {
    TraceStatus.NO_SOURCE: (WhyUnjudged.NO_TRACE_SOURCE, "this app has no traces to read"),
    TraceStatus.EMPTY: (WhyUnjudged.NO_TRACES, "no traces arrived"),
    TraceStatus.PARTIAL: (WhyUnjudged.PARTIAL_TRACE, "only part of the traces arrived"),
    TraceStatus.UNREAD: (WhyUnjudged.NO_TRACES, "the traces couldn't be read"),
}
#: A read with nothing in it to go on.
_NOTHING_READ = (TraceStatus.NO_SOURCE, TraceStatus.EMPTY, TraceStatus.UNREAD)
_FAILURE = {
    Failure.UNREACHABLE: WhyUnjudged.APP_UNREACHABLE,
    Failure.NOT_SUPPORTED: WhyUnjudged.NOT_SUPPORTED,
    Failure.OWN_MODEL: WhyUnjudged.JUDGE_FAILED,
    Failure.ADAPTER: WhyUnjudged.ADAPTER_MISREAD,
    Failure.BLOCKED: WhyUnjudged.BLOCKED,
    Failure.REFUSED: WhyUnjudged.CREDENTIALS_REFUSED,
    Failure.TRANSIENT: WhyUnjudged.APP_BUSY,
}
#: A try cut short by one of these is on the app. Cut short by anything else, what it didn't get to
#: do says nothing.
_THE_APPS_DOING = (None, Failure.APP, Failure.TIMED_OUT)
_MAX_REFS = 5
#: Seconds a model call may seem to end after the reply it gave: a span's clock is the app's, and it
#: may close only after its last word went out.
_CLOCK_SLACK = 1.0


@dataclass(frozen=True)
class CodeChecks:
    """What the code found of a try: its checks, the state checks as steps, and its route."""

    checks: list[CheckResult]
    states: list[Step]
    route: Route


def _code(
    id_: str,
    kind: CheckKind,
    outcome: Outcome,
    why: str,
    *,
    role: Role = Role.MUST,
    unjudged: WhyUnjudged | None = None,
    evidence: Sequence[EvidenceRef] = (),
    score: float | None = None,
) -> CheckResult:
    return CheckResult(
        id=id_,
        kind=kind,
        decided_by=Decider.CODE,
        role=role,
        outcome=outcome,
        why=why,
        unjudged=unjudged,
        evidence=tuple(evidence),
        score=score,
    )


def _no_read(id_: str, kind: CheckKind, status: TraceStatus, role: Role = Role.MUST) -> CheckResult:
    """A check the traces could not answer."""
    why, words = _TRACE_WHY[status]
    return _code(id_, kind, Outcome.UNJUDGED, words, role=role, unjudged=why)


def _state_check(state: StateRecord, evidence: TryEvidence) -> CheckResult:
    id_ = f"state:{state.index}:{state.key}"
    if state.ok is None:
        return _context_check(id_, state, evidence)
    if not state.supported:
        return _code(
            id_,
            CheckKind.STATE,
            Outcome.UNJUDGED,
            state.reason,
            unjudged=WhyUnjudged.NOT_SUPPORTED,
        )
    ref = [EvidenceRef(ref=f"state:{state.index}")]
    if not state.observed:
        why = state.reason or "the state couldn't be read"
        return _code(
            id_,
            CheckKind.STATE,
            Outcome.UNJUDGED,
            why,
            unjudged=WhyUnjudged.NOT_SUPPORTED,
            evidence=ref,
        )
    outcome = Outcome.PASSED if state.ok else Outcome.FAILED
    return _code(id_, CheckKind.STATE, outcome, state.reason, evidence=ref)


def _calls_read_by(state: StateRecord, evidence: TryEvidence) -> list[ModelCallRecord]:
    """The assistant's model calls a context check could be about: those from the check, ended by
    the reply of the next turn. One before it says nothing about it, a helper's isn't the
    Conversation's, and one after that reply belongs to what comes next."""
    t0 = evidence.t0
    ended = (t.ended - t0 for t in evidence.turns if round(t.started - t0, 2) >= state.at)
    until = next(ended, float("inf"))
    return [
        c
        for c in evidence.model_calls
        if c.helper is None
        and state.at <= c.t <= until
        and (c.ended or c.t) <= until + _CLOCK_SLACK
    ]


def _answering(
    state: StateRecord, calls: Sequence[ModelCallRecord], evidence: TryEvidence
) -> ModelCallRecord | None:
    """The call whose words are the next reply's; failing that, the last that said anything, as a
    call made on the side can end after the one that answered."""
    reply = next(
        (
            _words(t.reply.text)
            for t in evidence.turns
            if t.kind == "message" and t.started - evidence.t0 >= state.at
        ),
        "",
    )
    spoke = [c for c in reversed(calls) if c.said.text.strip()]
    return next((c for c in spoke if reply and _words(c.said.text) in reply), None) or next(
        iter(spoke), None
    )


def _context_check(id_: str, state: StateRecord, evidence: TryEvidence) -> CheckResult:
    """Decided on the answering call alone: the last of its calls that said something. A compaction
    or a side call before it decides nothing, and since a request carries the history, a fact still
    in the conversation shows on that call. Not seeing the word decides only when the call was read
    whole, and on a partial read the answering call may not have come."""
    kind = CheckKind.CONTEXT
    if evidence.trace in _NOTHING_READ or evidence.trace is TraceStatus.PARTIAL:
        return _no_read(id_, kind, evidence.trace)
    calls = _calls_read_by(state, evidence)
    if not calls:
        why = "no model call came after the check"
        return _code(id_, kind, Outcome.UNJUDGED, why, unjudged=WhyUnjudged.CONTENT_NOT_CAPTURED)
    answering = _answering(state, calls, evidence)
    if answering is None:
        why = "the traces show model calls after the check, but none that answered"
        return _code(
            id_,
            kind,
            Outcome.UNJUDGED,
            why,
            unjudged=WhyUnjudged.CONTENT_NOT_CAPTURED,
            evidence=_call_refs(calls),
        )
    needle = str(state.value)
    wanted = state.key == "context_contains"
    refs = _call_refs([answering])
    if needle.casefold() in answering.sent.text.casefold():
        outcome = Outcome.PASSED if wanted else Outcome.FAILED
        why = f"the answering model call {answering.n} contains {needle!r}"
        return _code(id_, kind, outcome, why, evidence=refs)
    if not answering.captured:
        why = f"the answering model call {answering.n} has no captured content"
        return _code(
            id_,
            kind,
            Outcome.UNJUDGED,
            why,
            unjudged=WhyUnjudged.CONTENT_NOT_CAPTURED,
            evidence=refs,
        )
    outcome = Outcome.FAILED if wanted else Outcome.PASSED
    why = f"the answering model call {answering.n} lacks {needle!r}"
    return _code(id_, kind, outcome, why, evidence=refs)


def _call_refs(calls: Sequence[ModelCallRecord]) -> list[EvidenceRef]:
    return [EvidenceRef(ref=f"span:{c.span}") for c in calls[:_MAX_REFS]]


def _never_check(name: str, evidence: TryEvidence) -> CheckResult:
    id_ = f"never:{name}"
    seen = [t for t in evidence.tools if t.name == name]
    if seen:
        refs = [EvidenceRef(ref=f"span:{t.id}") for t in seen[:_MAX_REFS]]
        why = f"{name} is never to be called"
        return _code(id_, CheckKind.NEVER, Outcome.FAILED, why, evidence=refs)
    if evidence.trace is not TraceStatus.COMPLETE:
        return _no_read(id_, CheckKind.NEVER, evidence.trace)
    return _code(id_, CheckKind.NEVER, Outcome.PASSED, f"{name} was not called")


def _words(text: str) -> str:
    return " ".join(text.split()).casefold()


def _reply_checks(case: Case, evidence: TryEvidence) -> list[CheckResult]:
    """The last reply's words. A try cut short has none to read: its turn check decides it. An empty
    reply fails both ways, since lacking a word says nothing when nothing was said."""
    if evidence.failure is not None or not (case.reply_contains or case.reply_lacks):
        return []
    reply = _words(
        next((t.reply.text for t in reversed(evidence.turns) if t.kind == "message"), "")
    )
    found = []
    for wanted, words, kind, name in (
        (True, case.reply_contains, CheckKind.REPLY_TEXT, "contains"),
        (False, case.reply_lacks, CheckKind.REPLY_LACKS, "lacks"),
    ):
        for i, word in enumerate(words):
            present = _words(word) in reply
            why = (
                f"the reply {'contains' if present else 'lacks'} {word!r}"
                if reply
                else "the reply is empty"
            )
            outcome = Outcome.PASSED if reply and present == wanted else Outcome.FAILED
            ref = [EvidenceRef(ref="reply")]
            found.append(_code(f"reply:{name}:{i}", kind, outcome, why, evidence=ref))
    return found


def _result_problem(spec: Mapping[str, Any], tool: ToolRecord) -> str | None:
    """Why *tool*'s result doesn't hold what *spec* wants; ``None`` when it does.

    :raises ValueError: *spec* names a path and the result isn't JSON.
    """
    path, want = spec.get("path"), spec["want"]
    if path is None:
        return arg_problem("the result", want, {"the result": tool.result.text})
    found: Any = json.loads(tool.result.text)
    for key in path.split("."):
        if isinstance(found, dict) and key in found:
            found = found[key]
        elif isinstance(found, list) and key.isdigit() and int(key) < len(found):
            found = found[int(key)]
        else:
            return f"{path} missing"
    return arg_problem(path, want, {path: found})


def _result_check(index: int, spec: Mapping[str, Any], evidence: TryEvidence) -> CheckResult:
    """Whether a call to the tool returned what the test wants: any call will do, so only complete
    traces can say none did."""
    name = spec["name"]
    id_ = f"result:{index}:{name}"
    kind = CheckKind.TOOL_RESULT
    calls = [t for t in evidence.tools if t.name == name]
    misses = []
    for tool in calls:
        try:
            problem = _result_problem(spec, tool)
        except ValueError:
            continue
        if problem is None:
            why = f"{name} returned what was wanted at {spec.get('path') or 'its result'}"
            return _code(
                id_, kind, Outcome.PASSED, why, evidence=[EvidenceRef(ref=f"span:{tool.id}")]
            )
        misses.append(problem)
    if evidence.trace is not TraceStatus.COMPLETE:
        return _no_read(id_, kind, evidence.trace)
    if not calls:
        return _code(id_, kind, Outcome.FAILED, f"{name} was not called")
    refs = [EvidenceRef(ref=f"span:{t.id}") for t in calls[:_MAX_REFS]]
    if not misses:
        why = f"{name}'s result can't be read as JSON"
        return _code(
            id_, kind, Outcome.UNJUDGED, why, unjudged=WhyUnjudged.NOT_SUPPORTED, evidence=refs
        )
    return _code(id_, kind, Outcome.FAILED, f"{name}: {misses[0]}", evidence=refs)


def _first_miss(steps: Sequence[Step]) -> str:
    for step in steps:
        if step.status == "matched" or step.reason == EXTRA_OK or step.status == "never":
            continue
        label = step.expected or step.actual
        return f"{label}: {step.reason}" if label else step.reason
    return "the expected calls were made"


def _steps_check(
    case: Case, evidence: TryEvidence, steps: Sequence[Step], score: float, held: bool
) -> CheckResult | None:
    tools = case.tools
    if not (tools.get("expected") or tools.get("extras") is False or tools.get("order") == "exact"):
        return None
    role = Role.MUST if steps_bind(case) else Role.GUIDE
    if not held and evidence.failure not in _THE_APPS_DOING:
        return None
    if evidence.trace in _NOTHING_READ:
        return _no_read("steps", CheckKind.STEPS, evidence.trace, role)
    # On a partial read a call not seen may yet have been made: an extra one, or one in the way of
    # the exact order. Only expected calls that were seen can hold.
    whole_route = tools.get("extras") is False or tools.get("order") == "exact"
    if evidence.trace is TraceStatus.PARTIAL and (not held or whole_route):
        return _no_read("steps", CheckKind.STEPS, evidence.trace, role)
    refs = [
        EvidenceRef(ref=f"span:{s.span}")
        for s in steps
        if s.span and s.status not in ("matched", "extra", "missing")
    ]
    return _code(
        "steps",
        CheckKind.STEPS,
        Outcome.PASSED if held else Outcome.FAILED,
        _first_miss(steps),
        role=role,
        evidence=refs[:_MAX_REFS],
        score=score,
    )


def _turn_check(evidence: TryEvidence) -> CheckResult | None:
    """A try cut short: the app's doing fails it; anything else leaves it unjudged."""
    if evidence.failure is None:
        return None
    id_ = f"turn:{len(evidence.turns)}"
    ref = [EvidenceRef(ref=id_)]
    why = evidence.error or "the try stopped"
    if evidence.failure is Failure.APP:
        return _code(id_, CheckKind.TURN, Outcome.FAILED, why, evidence=ref)
    if evidence.failure is Failure.TIMED_OUT:
        if timeout_outcome(evidence) is Outcome.FAILED:
            return _code(id_, CheckKind.TIMEOUT, Outcome.FAILED, f"timed out: {why}", evidence=ref)
        unread = evidence.trace is TraceStatus.UNREAD
        said = "the traces couldn't be read" if unread else "no sign of the agent working"
        return _code(
            id_,
            CheckKind.TIMEOUT,
            Outcome.UNJUDGED,
            f"timed out, and {said}: {why}",
            unjudged=WhyUnjudged.NO_TRACES if unread else WhyUnjudged.NEVER_STARTED,
            evidence=ref,
        )
    return _code(
        id_,
        CheckKind.TURN,
        Outcome.UNJUDGED,
        f"{why.rstrip('.')}.{_what_was_seen(evidence)}",
        unjudged=_FAILURE[evidence.failure],
        evidence=ref,
    )


def _blocked_checks(evidence: TryEvidence) -> list[CheckResult]:
    """Each turn the test wants to stop and wait: whether it did."""
    found = []
    for i, turn in enumerate(evidence.turns):
        if not turn.expected_blocked or turn.error:
            continue
        waited = turn.ended_as is TurnEnd.BLOCKED
        why = "the app stopped to wait, as expected" if waited else "the app didn't stop to wait"
        outcome = Outcome.PASSED if waited else Outcome.FAILED
        ref = [EvidenceRef(ref=f"turn:{i + 1}")]
        found.append(_code(f"blocked:{i + 1}", CheckKind.STATE, outcome, why, evidence=ref))
    return found


def _what_was_seen(evidence: TryEvidence) -> str:
    """What the traces show of a try no one could judge: no verdict, but it did happen."""
    names = list(dict.fromkeys(t.name for t in evidence.tools))
    return f" The traces show it called {', '.join(names)}." if names else ""


def _limit_check(case: Case, evidence: TryEvidence) -> CheckResult | None:
    limit = case.limits.get("seconds")
    # The limit is the deadline: past it, the turn check has already said so.
    if limit is None or evidence.failure not in (None, Failure.APP):
        return None
    broke = evidence.seconds > limit
    why = f"took {evidence.seconds:.0f} s, limit {limit:.0f} s"
    return _code("limit:seconds", CheckKind.LIMIT, Outcome.FAILED if broke else Outcome.PASSED, why)


def _state_step(state: StateRecord, check: CheckResult, evidence: TryEvidence) -> Step:
    """A state check as the steps show it, and as the grader reads it."""
    status = {Outcome.PASSED: "matched", Outcome.FAILED: "missing"}.get(check.outcome, "unjudged")
    span = next((r.ref[len("span:") :] for r in check.evidence if r.ref.startswith("span:")), None)
    call = next((c for c in evidence.model_calls if c.span == span), None)
    actual = f"model call {call.n}" if call else state.actual
    return Step(status, f"state:{state.key}", actual, check.why, span)


def code_checks(
    case: Case,
    evidence: TryEvidence,
    steps: Sequence[Step],
    score: float,
    marks: Mapping[str, float],
) -> CodeChecks:
    """What the code decides of a try. *steps* and *score* are its aligned tool calls; *marks* the
    pass marks it is held to."""
    held = score >= marks["tools"]
    states = [(state, _state_check(state, evidence)) for state in evidence.states]
    found: list[CheckResult] = [c for _, c in states]
    found += _reply_checks(case, evidence)
    found += _blocked_checks(evidence)
    if evidence.failure is None:
        found += [
            _result_check(i, spec, evidence)
            for i, spec in enumerate(case.tools.get("results") or [])
        ]
    found += [_never_check(name, evidence) for name in case.tools.get("never") or []]
    aligned = _steps_check(case, evidence, steps, score, held)
    found += [aligned] if aligned else []
    turn = _turn_check(evidence)
    found += [turn] if turn else []
    if not found and not case.rubric and not case.reference:
        # Nothing to check: the try itself still has to have been seen.
        found.append(_seen_check(evidence))
    limit = _limit_check(case, evidence)
    found += [limit] if limit else []
    return CodeChecks(
        found,
        [_state_step(state, check, evidence) for state, check in states],
        route_of(evidence.trace is TraceStatus.COMPLETE, held),
    )


def _seen_check(evidence: TryEvidence) -> CheckResult:
    if evidence.trace in _NOTHING_READ:
        return _no_read("turn:seen", CheckKind.TURN, evidence.trace)
    return _code("turn:seen", CheckKind.TURN, Outcome.PASSED, "the try ran and its traces arrived")
