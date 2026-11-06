"""The rules, one by one (docs/judging.md): pure functions over what was checked."""

import pytest

from evalution.core.judgement import (
    Basis,
    Change,
    CheckKind,
    CheckResult,
    Decider,
    Judgement,
    JudgeStamp,
    Outcome,
    ResultKey,
    Role,
    Route,
    Verdict,
    VerdictKind,
    WhyUnjudged,
)
from evalution.core.rules import (
    RULES_VERSION,
    basis_of,
    change_of,
    comparable,
    could_be_chance,
    first_reason,
    judge_try,
    judgement,
    label_of,
    line_outcome,
    route_of,
    rubric_outcome,
    tools_bind,
    verdict,
)
from evalution.runs.sampling import one_more

P, F, U = Outcome.PASSED, Outcome.FAILED, Outcome.UNJUDGED


def _check(
    outcome: Outcome,
    kind: CheckKind = CheckKind.RUBRIC,
    by: Decider = Decider.MODEL,
    role: Role = Role.MUST,
    why: str = "why",
) -> CheckResult:
    missing = WhyUnjudged.JUDGE_FAILED if outcome is U else None
    return CheckResult(
        id="c", kind=kind, decided_by=by, role=role, outcome=outcome, why=why, unjudged=missing
    )


@pytest.mark.parametrize(
    ("outcomes", "want"),
    [
        ([P, P], P),
        ([P, U], U),
        ([U, F], F),  # a real failure beats a missing grader
        ([], U),
    ],
)
def test_a_try(outcomes, want) -> None:
    assert judge_try(_check(o) for o in outcomes) == want


def _code(outcome: Outcome, kind: CheckKind, why: str, unjudged: WhyUnjudged | None = None):
    return CheckResult(
        id="c",
        kind=kind,
        decided_by=Decider.CODE,
        role=Role.MUST,
        outcome=outcome,
        why=why,
        unjudged=unjudged,
    )


def _no(why: WhyUnjudged) -> CheckResult:
    return _code(U, CheckKind.TURN, "why", why)


TIMED = _code(F, CheckKind.TIMEOUT, "timed out: the turn did not finish in time")


@pytest.mark.parametrize(
    ("checks", "want"),
    [
        ([_code(P, CheckKind.STATE, "ok")], "Passed"),
        ([_code(F, CheckKind.STATE, "no")], "Failed check"),
        ([_code(F, CheckKind.TURN, "the app answered 500")], "Failed check"),
        ([TIMED], "Timed out"),
        # The limit a timeout broke is the same fact; anything else failed on its own.
        ([TIMED, _code(F, CheckKind.LIMIT, "seconds 90 > 60")], "Timed out"),
        ([TIMED, _code(F, CheckKind.NEVER, "wipe was called")], "Failed check"),
        ([_code(F, CheckKind.LIMIT, "seconds 90 > 60")], "Failed check"),
        # A run stored before timeouts had a kind of their own.
        ([_code(F, CheckKind.TURN, "not done after 300 s")], "Timed out"),
        ([_code(F, CheckKind.TURN, "the turn did not finish in time")], "Timed out"),
        ([_no(WhyUnjudged.ADAPTER_MISREAD)], "Adapter error"),
        ([_no(WhyUnjudged.APP_UNREACHABLE)], "Setup blocked"),
        ([_no(WhyUnjudged.NOT_SUPPORTED)], "Setup blocked"),
        ([_no(WhyUnjudged.BLOCKED)], "Setup blocked"),
        ([_no(WhyUnjudged.NEVER_STARTED)], "Setup blocked"),
        ([_no(WhyUnjudged.LEFTOVER_STATE)], "Setup blocked"),
        ([_no(WhyUnjudged.JUDGE_FAILED)], "Grader unavailable"),
        ([_no(WhyUnjudged.JUDGE_UNSURE)], "Grader unavailable"),
        ([_no(WhyUnjudged.NO_TRACES)], "Missing evidence"),
        ([_no(WhyUnjudged.PARTIAL_TRACE)], "Missing evidence"),
        ([_no(WhyUnjudged.NO_TRACE_SOURCE)], "Missing evidence"),
        ([_no(WhyUnjudged.CONTENT_NOT_CAPTURED)], "Missing evidence"),
        # What stood most in the way speaks, and so does the reason beside it.
        ([_no(WhyUnjudged.PARTIAL_TRACE), _no(WhyUnjudged.BLOCKED)], "Setup blocked"),
        ([], "Missing evidence"),
    ],
)
def test_one_label_for_a_try(checks, want) -> None:
    assert label_of([judgement(checks, None, Route.UNKNOWN)]) == want


def test_one_label_for_a_test() -> None:
    def one(*checks: CheckResult) -> Judgement:
        return judgement(checks, None, Route.UNKNOWN)

    good, bad = one(_code(P, CheckKind.STATE, "ok")), one(_code(F, CheckKind.STATE, "no"))
    lost = one(_no(WhyUnjudged.JUDGE_FAILED))
    assert label_of([]) is None
    assert label_of([good, good]) == "Passed"
    assert label_of([good, bad]) == "Failed check"
    assert label_of([good, one(TIMED)]) == "Timed out"
    assert label_of([good, lost]) == "Passed", "only judged tries count, as for the verdict"
    assert label_of([lost, one(_no(WhyUnjudged.ADAPTER_MISREAD))]) == "Adapter error"


def test_a_guide_never_decides() -> None:
    guide = _check(F, CheckKind.STEPS, Decider.CODE, Role.GUIDE)
    assert judge_try([guide, _check(P)]) is P
    assert judge_try([guide]) is U


@pytest.mark.parametrize(
    ("kind", "by", "role", "want"),
    [
        (CheckKind.STATE, Decider.CODE, Role.MUST, Basis.CHECKED),
        (CheckKind.CONTEXT, Decider.CODE, Role.MUST, Basis.CHECKED),
        (CheckKind.REPLY_TEXT, Decider.CODE, Role.MUST, Basis.CHECKED),
        (CheckKind.STEPS, Decider.CODE, Role.MUST, Basis.CHECKED),
        (CheckKind.STEPS, Decider.CODE, Role.GUIDE, Basis.MODEL_ONLY),
        (CheckKind.NEVER, Decider.CODE, Role.MUST, Basis.MODEL_ONLY),
        (CheckKind.LIMIT, Decider.CODE, Role.MUST, Basis.MODEL_ONLY),
    ],
)
def test_what_a_pass_rests_on(kind, by, role, want) -> None:
    assert basis_of([_check(P), _check(P, kind, by, role)]) is want


def test_a_pass_nothing_decided_rests_on_nothing() -> None:
    assert basis_of([_check(P, CheckKind.TURN, Decider.CODE)]) is Basis.NONE


def _tries(*outcomes: Outcome) -> list[Judgement]:
    return [
        judgement([_check(o, CheckKind.STATE, Decider.CODE)], None, Route.UNKNOWN) for o in outcomes
    ]


@pytest.mark.parametrize(
    ("outcomes", "must_hold", "kind", "counts"),
    [
        ([P, P, P], False, VerdictKind.PASS, (3, 3, 0)),
        ([P, F, F], False, VerdictKind.FLAKY, (1, 3, 0)),
        ([P, F, F], True, VerdictKind.FAIL, (1, 3, 0)),
        ([F, F], False, VerdictKind.FAIL, (0, 2, 0)),
        ([P, U], False, VerdictKind.PASS, (1, 1, 1)),
        ([F, U], False, VerdictKind.FAIL, (0, 1, 1)),
        ([U, U], False, VerdictKind.UNJUDGED, (0, 0, 2)),
        ([], False, VerdictKind.NOT_RUN, (0, 0, 0)),
    ],
)
def test_a_test(outcomes, must_hold, kind, counts) -> None:
    found = verdict(_tries(*outcomes), must_hold)
    assert found.kind is kind
    assert (found.passed, found.judged, found.unjudged) == counts


def test_a_test_rests_on_its_weakest_try() -> None:
    checked = judgement([_check(P, CheckKind.STATE, Decider.CODE)], None, Route.EXPECTED)
    read = judgement([_check(P)], None, Route.UNKNOWN)
    assert verdict([checked, checked]).basis is Basis.CHECKED
    assert verdict([checked, read]).basis is Basis.MODEL_ONLY
    assert verdict([read, judgement([_check(F)], None, Route.UNKNOWN)]).basis is Basis.MODEL_ONLY
    assert verdict([judgement([_check(F)], None, Route.UNKNOWN)]).basis is Basis.NONE


def test_only_a_pass_has_a_basis() -> None:
    failed = judgement(
        [_check(F), _check(P, CheckKind.STATE, Decider.CODE)], JudgeStamp(key="k"), Route.UNKNOWN
    )
    assert failed.basis is Basis.NONE and failed.rules == RULES_VERSION


def test_only_complete_traces_say_it_went_another_way() -> None:
    assert route_of(True, True) is Route.EXPECTED
    assert route_of(True, False) is Route.DIFFERENT
    assert route_of(False, False) is Route.UNKNOWN


@pytest.mark.parametrize(
    ("tools", "has_outcome", "binds"),
    [
        ({"expected": [{"name": "a"}]}, True, False),
        ({"expected": [{"name": "a"}]}, False, True),
        ({"expected": [{"name": "a"}], "strict": True}, True, True),
        ({"expected": [{"name": "a"}], "order": "exact"}, True, True),
        ({"expected": [{"name": "a"}], "extras": False}, True, True),
    ],
)
def test_when_steps_bind(tools, has_outcome, binds) -> None:
    assert tools_bind(tools, has_outcome) is binds


YES, NO = {"pass": True, "sure": True}, {"pass": False, "sure": True}
MAYBE_YES, MAYBE_NO = {"pass": True, "sure": False}, {"pass": False, "sure": False}


@pytest.mark.parametrize(
    ("first", "second", "want"),
    [
        (YES, None, P),
        (NO, None, F),
        (MAYBE_YES, None, U),
        (MAYBE_YES, MAYBE_YES, U),  # both unsure, agreeing
        (MAYBE_YES, MAYBE_NO, U),  # a split neither is sure of
        (MAYBE_YES, NO, F),  # a split one is sure of follows that one
        (MAYBE_NO, YES, P),
    ],
)
def test_one_rubric_line(first, second, want) -> None:
    assert line_outcome(first, second) is want


@pytest.mark.parametrize(
    ("lines", "need", "want"),
    [
        ([P, P, F], 2, P),
        ([P, P, U], 2, P),
        ([P, U, F], 2, U),
        ([P, F, F], 2, F),
    ],
)
def test_some_lines_of_a_rubric(lines, need, want) -> None:
    assert rubric_outcome(lines, need) is want


def test_the_first_reason_is_a_failure_before_a_gap() -> None:
    gap = _check(U, why="the grader failed")
    miss = _check(F, CheckKind.STATE, Decider.CODE, why="no memory claim")
    assert first_reason(judgement([gap, miss], None, Route.UNKNOWN)) == "no memory claim"
    assert first_reason(judgement([gap], None, Route.UNKNOWN)) == "the grader failed"
    assert first_reason(judgement([_check(P)], None, Route.UNKNOWN)) is None


def _key(**changed) -> ResultKey:
    fields = {
        "case_version": "v1",
        "rules": 1,
        "rules_from": 1,
        "judge_model": "m",
        "pass_marks": (("reply", 0.7), ("tools", 0.8)),
    }
    return ResultKey(**{**fields, **changed})


@pytest.mark.parametrize(
    "other",
    [
        {"case_version": "v2"},
        {"rules": 2},
        {"rules_from": 0},
        {"judge_model": "n"},
        {"pass_marks": (("reply", 0.9), ("tools", 0.8))},
    ],
)
def test_different_tests_or_rules_do_not_compare(other) -> None:
    assert comparable(_key(), _key())
    assert not comparable(_key(), _key(**other))


def test_a_judge_not_on_record_compares_with_nothing() -> None:
    blind = _key(judge_model=None)
    assert not comparable(blind, blind)


def _said(passed: int, judged: int) -> Verdict:
    kind = (
        VerdictKind.PASS
        if passed == judged
        else VerdictKind.FAIL
        if not passed
        else VerdictKind.FLAKY
    )
    return Verdict(kind=kind, passed=passed, judged=judged)


def test_a_move_between_runs_says_which_way_and_if_it_could_be_chance_till_tries_tell() -> None:
    assert change_of(_said(3, 3), _said(0, 3)) is Change.WORSE
    assert change_of(_said(0, 3), _said(3, 3)) is Change.BETTER
    assert change_of(_said(2, 2), _said(4, 4)) is Change.SAME, "by share, whatever the tries"
    assert change_of(_said(0, 0), _said(3, 3)) is Change.UNKNOWN, "no one could judge one side"
    # Three tries each can never tell; all of four against none of four just can.
    assert could_be_chance(_said(3, 3), _said(0, 3))
    assert not could_be_chance(_said(4, 4), _said(0, 4))
    assert could_be_chance(_said(4, 4), _said(3, 4))
    assert not could_be_chance(_said(20, 20), _said(10, 20))
    # How many tries a test gets with no count asked for: its own three, then on while they
    # disagree, until each outcome showed three times, never past twenty.
    assert one_more(_said(2, 2), 3)
    assert not one_more(_said(3, 3), 3) and not one_more(_said(0, 3), 3), "they agree"
    assert not one_more(Verdict(kind=VerdictKind.UNJUDGED, unjudged=3), 3), "nothing to read"
    assert one_more(_said(2, 3), 3) and one_more(_said(17, 19), 3)
    assert not one_more(_said(3, 6), 3) and not one_more(_said(18, 20), 3)
    # Against the run it is compared with, a move plays on until it is real, or can't become so.
    assert one_more(_said(0, 3), 3, base=_said(3, 3)), "three can never tell"
    assert not one_more(_said(0, 4), 3, base=_said(4, 4)), "real"
    assert not one_more(_said(3, 3), 3, base=_said(3, 3)), "it didn't move"
    assert not one_more(_said(3, 3), 3, base=_said(3, 4)), "chance: even twenty passes can't tell"
