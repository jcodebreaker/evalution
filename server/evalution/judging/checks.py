"""From a try's evidence to the checks code decides (docs/judging.md): the state and context checks,
the reply's words, what tools returned, ``never``, the steps, the limits, how the try's turns
went, and on a try played through the page, what it showed. Pure: no model, no files, and what it
could not know it says, rather than failing or passing on it.

On a partial read of the traces only what was seen can decide. Not seeing a call, or a word in a
model call, decides nothing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalution.core.case import Case
from evalution.core.evidence import (
    Failure,
    ModelCallRecord,
    PageRecord,
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
    Failure.LEFTOVER_STATE: WhyUnjudged.LEFTOVER_STATE,
    Failure.PAGE_MAP: WhyUnjudged.PAGE_MAP,
}
#: A try cut short by one of these is on the app. Cut short by anything else, what it didn't get to
#: do says nothing.
_THE_APPS_DOING = (None, Failure.APP, Failure.TIMED_OUT)
_MAX_REFS = 5
#: Seconds a model call may seem to end after the reply it gave: a span's clock is the app's, and it
#: may close only after its last word went out.
_CLOCK_SLACK = 1.0
#: What a page renders away of Markdown: its marks, a link's target, a list's numbers, a table's
#: bars.
_MARKS = re.compile(r"[*_`#>~-]")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
#: How much of what a page showed a check's reason quotes.
_SHOWN_CHARS = 160


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


def says(reply: str, word: str) -> bool:
    """Whether *reply* has *word* as ``reply.contains`` reads it: whatever the case and spacing."""
    return _words(word) in _words(reply)


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
            present = says(reply, word)
            why = (
                f"the reply {'contains' if present else 'lacks'} {word!r}"
                if reply
                else "the reply is empty"
            )
            outcome = Outcome.PASSED if reply and present == wanted else Outcome.FAILED
            ref = [EvidenceRef(ref="reply")]
            found.append(_code(f"reply:{name}:{i}", kind, outcome, why, evidence=ref))
    return found


def _plain(text: str) -> str:
    """Text as a page and a reply can both be read: Markdown's marks, links' targets and list
    numbers gone, spacing collapsed, case aside. The page renders what the reply writes out."""
    text = _LINK.sub(r"\1", text)
    text = _NUMBERED.sub(" ", text)
    return _words(_MARKS.sub("", text).replace("|", " "))


def _page_turn_refs(evidence: TryEvidence, at: int) -> list[EvidenceRef]:
    """Where turn *at* (from 1) shows on the page: what it showed, and its screenshot."""
    refs = [EvidenceRef(ref=f"page:{at}")]
    page = evidence.page
    if page is not None and at <= len(page.turns) and page.turns[at - 1].shot:
        refs.append(EvidenceRef(ref=f"shot:{page.turns[at - 1].shot}"))
    return refs


def _shown_check(evidence: TryEvidence, at: int, role: Role) -> CheckResult | None:
    """Whether turn *at* (from 1)'s reply showed on the page: its words among what the page's
    replies showed after the turn, read as :func:`_plain` reads them. ``None`` for a turn with no
    reply to look for."""
    turn = evidence.turns[at - 1]
    id_, kind, refs = f"shown:{at}", CheckKind.SHOWN, _page_turn_refs(evidence, at)
    if turn.error:
        why = f"turn {at} ended in an error, so there was no reply to show"
        return _code(
            id_,
            kind,
            Outcome.UNJUDGED,
            why,
            role=role,
            unjudged=WhyUnjudged.NO_REPLY,
            evidence=refs,
        )
    reply = _plain(turn.reply.text)
    if not reply:
        return None
    if turn.shown is None:
        page = evidence.page
        instead = page.turns[at - 1].instead.text if page and at <= len(page.turns) else ""
        said = f"; it showed {_cut(instead)!r} instead" if instead.strip() else ""
        why = f"turn {at}'s reply never showed on the page{said}"
        return _code(id_, kind, Outcome.FAILED, why, role=role, evidence=refs)
    if reply in _plain(turn.shown.text):
        why = f"turn {at}'s reply showed on the page"
        return _code(id_, kind, Outcome.PASSED, why, role=role, evidence=refs)
    why = f"the page showed {_cut(turn.shown.text)!r}, not turn {at}'s reply"
    return _code(id_, kind, Outcome.FAILED, why, role=role, evidence=refs)


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _SHOWN_CHARS else f"{text[:_SHOWN_CHARS]}…"


def _shown_words(case: Case, evidence: TryEvidence, role: Role) -> list[CheckResult]:
    """The test's own ``page.contains`` and ``page.lacks``, on what the page showed of the last
    reply. A try cut short has none to read, as for the reply's words; a reply that never showed
    fails both, as an empty reply does."""
    if evidence.failure is not None or not (case.shown_contains or case.shown_lacks):
        return []
    at = next(
        (i for i in range(len(evidence.turns), 0, -1) if evidence.turns[i - 1].kind == "message"),
        0,
    )
    shown = evidence.turns[at - 1].shown if at else None
    text = _plain(shown.text) if shown is not None else ""
    refs = _page_turn_refs(evidence, at) if at else []
    found = []
    for wanted, words, name in (
        (True, case.shown_contains, "contains"),
        (False, case.shown_lacks, "lacks"),
    ):
        for i, word in enumerate(words):
            present = bool(text) and _plain(word) in text
            why = (
                f"the page {'shows' if present else 'lacks'} {word!r}"
                if text
                else "the reply never showed on the page"
            )
            outcome = Outcome.PASSED if text and present == wanted else Outcome.FAILED
            found.append(
                _code(f"shown:{name}:{i}", CheckKind.SHOWN, outcome, why, role=role, evidence=refs)
            )
    return found


def _page_health(page: PageRecord, role: Role) -> CheckResult:
    """No console errors and no failed requests to the copy, over the whole try. A warning is
    kept and decides nothing."""
    errors = [(i, c) for i, c in enumerate(page.console, 1) if c.level == "error"]
    failed = list(enumerate(page.failed_requests, 1))
    refs = [EvidenceRef(ref=f"console:{i}") for i, _ in errors[:_MAX_REFS]]
    refs += [EvidenceRef(ref=f"request:{i}") for i, _ in failed[:_MAX_REFS]]
    if not errors and not failed:
        why = "no console errors, and no failed requests to the copy"
        return _code("page_health", CheckKind.PAGE_HEALTH, Outcome.PASSED, why, role=role)
    said = []
    if errors:
        said.append(f"{len(errors)} console error(s), first {_cut(errors[0][1].text.text)!r}")
    if failed:
        first = failed[0][1]
        status = first.status if first.status is not None else "no answer"
        said.append(f"{len(failed)} failed request(s), first {first.method} {first.path}: {status}")
    return _code(
        "page_health",
        CheckKind.PAGE_HEALTH,
        Outcome.FAILED,
        "; ".join(said),
        role=role,
        evidence=refs,
    )


def page_checks(case: Case, evidence: TryEvidence) -> list[CheckResult]:
    """What the page showed, on a try played through it (ADR 0010): each message turn's reply
    showed, the test's own words, and the page's health. A guide unless the test says the page
    decides (``page.decides``). A try played through the API alone has none."""
    if evidence.page is None:
        return []
    role = Role.MUST if case.page_decides else Role.GUIDE
    found = [
        check
        for at, turn in enumerate(evidence.turns, 1)
        if turn.kind == "message" and (check := _shown_check(evidence, at, role))
    ]
    found += _shown_words(case, evidence, role)
    found.append(_page_health(evidence.page, role))
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
    page = page_checks(case, evidence)
    if case.page_decides:
        found += page
    if not found and not case.rubric and not case.reference:
        # Nothing to check: the try itself still has to have been seen.
        found.append(_seen_check(evidence))
    if not case.page_decides:
        # Page checks that only guide leave a test with nothing else to check as it was.
        found += page
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
