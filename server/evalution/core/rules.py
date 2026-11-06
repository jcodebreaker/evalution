"""The one place the rules live (docs/judging.md): how a try and a test are called, from what was
checked. Pure functions over the types of :mod:`evalution.core.judgement`: no model and no files.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence, Set
from math import comb
from typing import Any

from evalution.core.case import Case
from evalution.core.evidence import TraceStatus, TryEvidence
from evalution.core.judgement import (
    Basis,
    Change,
    CheckKind,
    CheckResult,
    Decider,
    Judgement,
    JudgeStamp,
    Label,
    Outcome,
    ResultKey,
    Role,
    Route,
    Verdict,
    VerdictKind,
    WhyUnjudged,
)

#: Raised whenever a rule below changes what a stored try would read as.
#: How unlikely a move must be, by chance alone, before it's called a real one.
CHANCE = 0.05
RULES_VERSION = 2
#: Code checks that show the outcome itself. Binding steps count too (:func:`basis_of`).
_OUTCOME_CHECKS = frozenset(
    {CheckKind.STATE, CheckKind.CONTEXT, CheckKind.REPLY_TEXT, CheckKind.TOOL_RESULT}
)

NO_TRACES = "No traces arrived"
TARGET_GONE = "The app's session went away mid-try"
#: A run the server restarted under: interrupted, which says nothing of the app.
SERVER_RESTARTED = "The server restarted during the run"
#: A try that started already holding what it was to show: an earlier try's, left by a reset.
LEFTOVER_STATE = "Started with leftover state"
#: A failed run's ``failure``: the app was not there to play on, or something else stopped it.
APP_NOT_READY, ADAPTER_BROKEN, OTHER_FAILURE = "app_not_ready", "adapter_broken", "other"


#: Why a try couldn't be judged, as the one word a person reads.
_UNJUDGED_LABEL = {
    WhyUnjudged.ADAPTER_MISREAD: Label.ADAPTER_ERROR,
    WhyUnjudged.APP_UNREACHABLE: Label.SETUP_BLOCKED,
    WhyUnjudged.NOT_SUPPORTED: Label.SETUP_BLOCKED,
    WhyUnjudged.BLOCKED: Label.SETUP_BLOCKED,
    WhyUnjudged.NEVER_STARTED: Label.SETUP_BLOCKED,
    WhyUnjudged.CREDENTIALS_REFUSED: Label.SETUP_BLOCKED,
    WhyUnjudged.APP_BUSY: Label.SETUP_BLOCKED,
    WhyUnjudged.LEFTOVER_STATE: Label.SETUP_BLOCKED,
    WhyUnjudged.JUDGE_FAILED: Label.GRADER_UNAVAILABLE,
    WhyUnjudged.JUDGE_UNSURE: Label.GRADER_UNAVAILABLE,
    WhyUnjudged.GRADER_UNRELIABLE: Label.GRADER_UNRELIABLE,
    WhyUnjudged.NO_TRACE_SOURCE: Label.MISSING_EVIDENCE,
    WhyUnjudged.NO_TRACES: Label.MISSING_EVIDENCE,
    WhyUnjudged.PARTIAL_TRACE: Label.MISSING_EVIDENCE,
    WhyUnjudged.CONTENT_NOT_CAPTURED: Label.MISSING_EVIDENCE,
}
#: Which label speaks for a try or a test holding several: what the app did, then a pass, then what
#: stands most in the way of knowing.
_LABEL_ORDER = (
    Label.FAILED_CHECK,
    Label.TIMED_OUT,
    Label.PASSED,
    Label.ADAPTER_ERROR,
    Label.SETUP_BLOCKED,
    Label.MISSING_EVIDENCE,
    Label.GRADER_UNAVAILABLE,
    Label.GRADER_UNRELIABLE,
)
#: How a turn check stored before :attr:`CheckKind.TIMEOUT` said it ran out of time.
_OLD_TIMEOUT_WORDS = ("not done after", "did not finish in time", "not done in time")


def judged_tries(entry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """A test's tries that say something about the app."""
    return [t for t in entry.get("tries") or [] if current(t).outcome is not Outcome.UNJUDGED]


def judge_try(checks: Iterable[CheckResult]) -> Outcome:
    """Any MUST failed: failed, since a real failure beats a missing grader. Else any MUST
    unjudged: unjudged. Else passed; with no MUST at all nothing was judged."""
    musts = [c.outcome for c in checks if c.role is Role.MUST]
    if not musts:
        return Outcome.UNJUDGED
    if Outcome.FAILED in musts:
        return Outcome.FAILED
    return Outcome.UNJUDGED if Outcome.UNJUDGED in musts else Outcome.PASSED


def timeout_outcome(evidence: TryEvidence) -> Outcome:
    """A try that ran out of time. When the traces show no sign of the agent working and it said
    nothing, or the source that would show it failed, no one can say: unjudged. Anything else is the
    app's: failed. With no traces to read at all, nothing would ever show it, so that's failed too.
    """
    unseen = evidence.trace in (TraceStatus.EMPTY, TraceStatus.UNREAD)
    return Outcome.UNJUDGED if unseen and not evidence.called else Outcome.FAILED


def basis_of(checks: Iterable[CheckResult]) -> Basis:
    """``checked`` when a passing MUST came from a code check of the outcome (state, context, reply
    text or binding steps); else ``model_only`` when one came from a model; else ``none``."""
    held = [c for c in checks if c.role is Role.MUST and c.outcome is Outcome.PASSED]
    if any(
        c.decided_by is Decider.CODE and (c.kind in _OUTCOME_CHECKS or c.kind is CheckKind.STEPS)
        for c in held
    ):
        return Basis.CHECKED
    return Basis.MODEL_ONLY if any(c.decided_by is Decider.MODEL for c in held) else Basis.NONE


def route_of(traces_complete: bool, steps_held: bool) -> Route:
    """Which way the agent went: only complete traces can say it went another way."""
    if not traces_complete:
        return Route.UNKNOWN
    return Route.EXPECTED if steps_held else Route.DIFFERENT


def judgement(
    checks: Iterable[CheckResult],
    judge: JudgeStamp | None,
    route: Route,
    rules_from: int | None = None,
) -> Judgement:
    """*checks* judged under today's rules."""
    found = tuple(checks)
    outcome = judge_try(found)
    return Judgement(
        outcome=outcome,
        checks=found,
        rules=RULES_VERSION,
        judge=judge,
        basis=basis_of(found) if outcome is Outcome.PASSED else Basis.NONE,
        route=route,
        rules_from=rules_from,
    )


def verdict(judgements: Sequence[Judgement], must_hold: bool = False) -> Verdict:
    """Judged tries only. ``fail`` if none passed, ``flaky`` if some, ``pass`` if all; ``must_hold``
    turns flaky into fail: an agent varies, so one that got there at all is not called broken unless
    the test says it must hold. None judged: ``unjudged``. No tries: ``not_run``."""
    judged = [j for j in judgements if j.outcome is not Outcome.UNJUDGED]
    wins = [j for j in judged if j.outcome is Outcome.PASSED]
    if not judgements:
        kind = VerdictKind.NOT_RUN
    elif not judged:
        kind = VerdictKind.UNJUDGED
    elif not wins:
        kind = VerdictKind.FAIL
    elif len(wins) == len(judged):
        kind = VerdictKind.PASS
    else:
        kind = VerdictKind.FAIL if must_hold else VerdictKind.FLAKY
    return Verdict(
        kind=kind,
        passed=len(wins),
        judged=len(judged),
        unjudged=len(judgements) - len(judged),
        basis=_weakest(wins) if kind in (VerdictKind.PASS, VerdictKind.FLAKY) else Basis.NONE,
    )


def verdict_note(judgements: Sequence[Judgement]) -> str | None:
    """What a test's verdict leaves out: tries no one could judge count for nothing either way, so a
    verdict over the rest must say they were there."""
    unjudged = sum(j.outcome is Outcome.UNJUDGED for j in judgements)
    if not unjudged or unjudged == len(judgements):
        return None
    tries = "try" if len(judgements) == 1 else "tries"
    return f"{unjudged} of {len(judgements)} {tries} couldn't be judged"


def _weakest(wins: Iterable[Judgement]) -> Basis:
    """A test's pass rests on its weakest try."""
    found = {j.basis for j in wins}
    return next(b for b in (Basis.MODEL_ONLY, Basis.NONE, Basis.CHECKED) if b in found)


def tools_bind(tools: Mapping[str, Any], has_outcome: bool) -> bool:
    """Whether the steps themselves decide a try: the test says so, asks for an exact path or no
    extra calls, or has nothing else to decide it. Otherwise they are a guide: the agent may get to
    a good result another way, which is noted and does not fail it."""
    return (
        bool(tools.get("strict"))
        or tools.get("order") == "exact"
        or tools.get("extras") is False
        or not has_outcome
    )


def steps_bind(case: Case) -> bool:
    """:func:`tools_bind` for *case*: a deterministic outcome check or a rubric makes them a
    guide."""
    has_outcome = bool(
        case.rubric or case.reply_contains or case.reply_lacks or case.tools.get("results")
    ) or any(turn.state is not None for turn in case.turns)
    return tools_bind(case.tools, has_outcome)


def _deciding(
    first: Mapping[str, Any], second: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    """The grading that decides a rubric line: a sure one, the first before the second."""
    if first.get("sure", True):
        return first
    return second if second is not None and second.get("sure", True) else None


def line_outcome(first: Mapping[str, Any], second: Mapping[str, Any] | None) -> Outcome:
    """One rubric line. A sure grading stands. An unsure one is graded again: a sure second grading
    decides it; both unsure is unjudged, whether they agree or split."""
    said = _deciding(first, second)
    if said is None:
        return Outcome.UNJUDGED
    return Outcome.PASSED if said["pass"] else Outcome.FAILED


def reply_score(lines: Sequence[Outcome]) -> float | None:
    """The reply score: judged lines that held / judged lines. ``None`` with none judged."""
    judged = [line for line in lines if line is not Outcome.UNJUDGED]
    if not judged:
        return None
    return round(sum(line is Outcome.PASSED for line in judged) / len(judged), 3)


def line_reason(line: Mapping[str, Any]) -> str:
    """Why a stored rubric line came out as it did: the grading that decided it, never one it
    replaced; with neither sure, the later."""
    second = line.get("second")
    said = _deciding(line, second) or second or line
    return str(said.get("reason") or "")


def rubric_outcome(lines: Sequence[Outcome], need: int) -> Outcome:
    """A rubric that needs *need* of its lines: passed once that many held, failed once they can't,
    unjudged while the lines nobody could judge would decide it."""
    held = lines.count(Outcome.PASSED)
    if held >= need:
        return Outcome.PASSED
    return Outcome.UNJUDGED if held + lines.count(Outcome.UNJUDGED) >= need else Outcome.FAILED


def line_key(text: str) -> str:
    """A rubric line's identity across runs and regrades. A line has no id of its own (its check's
    ``rubric:N`` is only where it sits, which an edit moves), so it is the hash of its words,
    spacing and case aside: a line reworded is another line."""
    return hashlib.sha256(" ".join(text.split()).casefold().encode()).hexdigest()[:16]


def _is_line(check: CheckResult) -> bool:
    return check.kind is CheckKind.RUBRIC and check.id != "rubric"


def _trusted(check: CheckResult, unreliable: Set[str]) -> CheckResult:
    """A rubric line's pass taken as earned or not: unjudged while its key is in *unreliable*, and
    a pass again once it no longer is. Only a pass is held back: a fail stands, a real failure
    beating a doubtful grader as it beats a missing one."""
    if not _is_line(check):
        return check
    doubted = line_key(check.why) in unreliable
    if doubted and check.outcome is Outcome.PASSED:
        return check.model_copy(
            update={"outcome": Outcome.UNJUDGED, "unjudged": WhyUnjudged.GRADER_UNRELIABLE}
        )
    if not doubted and check.unjudged is WhyUnjudged.GRADER_UNRELIABLE:
        return check.model_copy(update={"outcome": Outcome.PASSED, "unjudged": None})
    return check


def distrusted(found: Judgement, unreliable: Set[str], need: int | None) -> Judgement | None:
    """*found* read again with the rubric lines (by :func:`line_key`) whose grader a person
    disagrees with too often: their passes are unjudged, ``grader_unreliable``. *need* is how many
    lines the test needs, when not all: its one check over them is worked out again. ``None`` when
    it reads the same."""
    checks = [_trusted(c, unreliable) for c in found.checks]
    if checks == list(found.checks):
        return None
    lines = [c.outcome for c in checks if _is_line(c)]
    doubted = any(c.unjudged is WhyUnjudged.GRADER_UNRELIABLE for c in checks if _is_line(c))
    for i, check in enumerate(checks):
        if check.id == "rubric" and need is not None:
            outcome = rubric_outcome(lines, need)
            why = WhyUnjudged.GRADER_UNRELIABLE if doubted else WhyUnjudged.JUDGE_UNSURE
            held = lines.count(Outcome.PASSED)
            checks[i] = check.model_copy(
                update={
                    "outcome": outcome,
                    "unjudged": why if outcome is Outcome.UNJUDGED else None,
                    "why": f"{held} of {len(lines)} lines held, {need} needed",
                }
            )
    was = found.rules_from if found.rules_from is not None else found.rules
    return judgement(checks, found.judge, found.route, None if was == RULES_VERSION else was)


def first_reason(found: Judgement) -> str | None:
    """Why a try did not pass, in one line: the first MUST that failed, else the first that could
    not be judged; ``None`` for a pass."""
    if found.outcome is Outcome.PASSED:
        return None
    musts = [c for c in found.checks if c.role is Role.MUST]
    for check in musts:
        if check.outcome is Outcome.FAILED:
            return check.why
    unjudged = [c for c in musts if c.outcome is Outcome.UNJUDGED]
    return min(unjudged, key=_unjudged_rank).why if unjudged else "nothing decided this try"


def _unjudged_rank(check: CheckResult) -> int:
    """The first of a try's checks no one could judge is what stands most in the way."""
    return _LABEL_ORDER.index(_UNJUDGED_LABEL.get(check.unjudged, Label.MISSING_EVIDENCE))


def _ran_out(check: CheckResult) -> bool:
    return check.kind is CheckKind.TIMEOUT or (
        check.kind is CheckKind.TURN and any(w in check.why for w in _OLD_TIMEOUT_WORDS)
    )


def _try_label(found: Judgement) -> Label:
    if found.outcome is Outcome.PASSED:
        return Label.PASSED
    musts = [c for c in found.checks if c.role is Role.MUST]
    failed = [c for c in musts if c.outcome is Outcome.FAILED]
    if failed:
        # The limit a timeout broke is the same fact; anything else failed on its own.
        others = [c for c in failed if not _ran_out(c) and c.kind is not CheckKind.LIMIT]
        return Label.FAILED_CHECK if others or not any(map(_ran_out, failed)) else Label.TIMED_OUT
    unjudged = [c for c in musts if c.outcome is Outcome.UNJUDGED]
    if not unjudged:
        return Label.MISSING_EVIDENCE
    return _UNJUDGED_LABEL.get(min(unjudged, key=_unjudged_rank).unjudged, Label.MISSING_EVIDENCE)


def label_of(judgements: Sequence[Judgement]) -> Label | None:
    """The one plain word for a try (one judgement) or a test (its tries'); ``None`` with no tries.
    Read from what was checked, so a run stored before there were labels reads the same way."""
    return min(map(_try_label, judgements), key=_LABEL_ORDER.index, default=None)


def called_of(attempt: Mapping[str, Any]) -> bool:
    """Whether a stored try reached the assistant; one from before it was kept says by what it
    holds."""
    if "called" in attempt:
        return bool(attempt["called"])
    return bool(attempt.get("context")) or bool(str(attempt.get("reply_text") or "").strip())


def comparable(a: ResultKey, b: ResultKey) -> bool:
    """Whether two results of a test can be set side by side: the same test, the same rules, the
    same model grading it and the same pass marks. A judge not on record compares with nothing."""
    return a == b and a.judge_model is not None and a.case_version is not None


def current(attempt: Mapping[str, Any]) -> Judgement:
    """A stored try's judgement: the newest it has."""
    return Judgement.model_validate(attempt["judgements"][-1])


def could_be_chance(before: Verdict, now: Verdict) -> bool:
    """Whether a move between two tests' tries is too small to tell from an agent varying: Fisher's
    exact test, two-sided, at 5%. Three tries each can never rule it out; four can, just."""
    a, b = now.passed, now.judged - now.passed
    c, d = before.passed, before.judged - before.passed
    total = a + b + c + d

    def chance_of(passed_now: int) -> float:
        passes, tries = a + c, a + b
        return (
            comb(passes, passed_now) * comb(total - passes, tries - passed_now) / comb(total, tries)
        )

    seen = chance_of(a)
    low, high = max(0, a + c - (c + d)), min(a + b, a + c)
    tail = sum(p for x in range(low, high + 1) if (p := chance_of(x)) <= seen * (1 + 1e-9))
    return tail > CHANCE


def change_of(before: Verdict, now: Verdict) -> Change:
    """How a test moved between two runs, by its share of judged tries that passed. A test no one
    could judge on either side says nothing."""
    if not before.judged or not now.judged:
        return Change.UNKNOWN
    was, is_now = before.passed * now.judged, now.passed * before.judged
    return Change.BETTER if is_now > was else Change.WORSE if is_now < was else Change.SAME
