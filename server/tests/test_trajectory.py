"""How tool calls are graded (docs/judging.md, "The scores" and "When steps bind"): exact means
exact, extras off means none, and what a delegated agent did is kept apart."""

from typing import Any

import pytest

from evalution.core.evidence import Clipped, TraceStatus, TryEvidence, TurnRecord
from evalution.core.judgement import (
    CheckKind,
    CheckResult,
    Decider,
    Label,
    Outcome,
    Role,
    Route,
    WhyUnjudged,
)
from evalution.core.rules import RULES_VERSION, current, judgement, label_of
from evalution.core.trace import Call, tool_name, tool_server
from evalution.judging.checks import code_checks
from evalution.judging.steps import EXTRA_HELPER, EXTRA_OFF, grade_tools, strict_ok
from evalution.results.migrate import upcast
from evalution.results.record import SCHEMA, originally
from evalution.suite.cases import CaseError, parse_case
from evalution.traces.base import Span, nest
from evalution.traces.view import build

MARKS = {"tools": 0.8, "reply": 0.8}


def _call(id_: str, name: str, start: float, helper: str | None = None, **args: Any) -> Call:
    return Call(id_, name, args, start, start + 0.5, helper)


def _evidence() -> TryEvidence:
    said = TurnRecord(
        kind="message",
        message=Clipped(text="hi"),
        reply=Clipped(text="done"),
        started=0.0,
        ended=1.0,
    )
    return TryEvidence(
        case="c", n=1, t0=0.0, seconds=1.0, turns=(said,), trace=TraceStatus.COMPLETE
    )


def _steps_outcome(tools: dict[str, Any], calls: list[Call]) -> Outcome:
    case = parse_case({"name": "n", "turns": [{"message": "hi"}], "tools": tools}, "c")
    steps, score = grade_tools(case.tools, calls)
    found = code_checks(case, _evidence(), steps, score, MARKS).checks
    return next(c.outcome for c in found if c.id == "steps")


TEN = [{"name": f"t{i}"} for i in range(10)]


def _ten(*extra: Call) -> list[Call]:
    calls = [_call(str(i), f"t{i}", float(i)) for i in range(10)]
    return sorted([*calls, *extra], key=lambda c: c.start)


def test_exact_with_one_extra_call_fails_though_the_score_would_hold() -> None:
    tools = {"order": "exact", "expected": TEN}
    calls = _ten(_call("x", "peek", 4.5))
    steps, score = grade_tools(tools, calls)
    assert score >= MARKS["tools"]
    assert strict_ok(tools, steps) is False
    assert _steps_outcome(tools, calls) is Outcome.FAILED
    assert _steps_outcome(tools, _ten()) is Outcome.PASSED


def test_exact_with_a_reorder_fails() -> None:
    tools = {"order": "exact", "expected": TEN}
    calls = _ten()
    calls[3], calls[4] = (
        _call("4", "t4", 3.0),
        _call("3", "t3", 4.0),
    )
    steps, _ = grade_tools(tools, calls)
    assert strict_ok(tools, steps) is False
    assert _steps_outcome(tools, calls) is Outcome.FAILED


def test_extras_off_with_one_extra_fails() -> None:
    tools = {"extras": False, "expected": TEN}
    calls = _ten(_call("x", "peek", 9.5))
    steps, score = grade_tools(tools, calls)
    assert score == 0.9
    assert strict_ok(tools, steps) is False
    assert _steps_outcome(tools, calls) is Outcome.FAILED
    # With none extra, the score still decides what's missing.
    steps, _ = grade_tools(tools, _ten())
    assert strict_ok(tools, steps) is None
    assert _steps_outcome(tools, _ten()) is Outcome.PASSED


def test_in_order_with_extras_allowed_is_unchanged() -> None:
    tools = {"order": "in_order", "expected": TEN}
    calls = _ten(_call("x", "peek", 4.5), _call("y", "peek", 8.5))
    steps, score = grade_tools(tools, calls)
    assert score == 1.0 and strict_ok(tools, steps) is None
    assert _steps_outcome(tools, calls) is Outcome.PASSED
    # One of ten missing still holds by the mark: 0.9.
    assert _steps_outcome(tools, [c for c in calls if c.id != "3"]) is Outcome.PASSED


def _old_run(
    tools: dict[str, Any], calls: list[Call], *, rules: int = 2, held: bool | None = None
) -> dict[str, Any]:
    """A run stored under rules 2: its steps check decided by the score alone, or as *held*."""
    steps, score = grade_tools(tools, calls)
    held = score >= MARKS["tools"] if held is None else held
    check = CheckResult(
        id="steps",
        kind=CheckKind.STEPS,
        decided_by=Decider.CODE,
        role=Role.MUST,
        outcome=Outcome.PASSED if held else Outcome.FAILED,
        why="the expected calls were made",
        score=score,
    )
    found = judgement([check], None, Route.EXPECTED if held else Route.DIFFERENT)
    stored = {**found.model_dump(mode="json"), "rules": rules}
    attempt = {
        "n": 1,
        "tools": score,
        "judgements": [stored],
        "steps": [s.as_dict() for s in steps],
        "calls": [
            {
                "id": c.id,
                "name": c.name,
                "args": dict(c.args),
                "start": c.start,
                "end": c.end,
                "helper": c.helper,
            }
            for c in calls
        ],
        "automatic": [],
    }
    entry = {"name": "n", "expected": tools, "case": {"tools": tools}, "tries": [attempt]}
    return {"schema": SCHEMA, "rules": rules, "pass_marks": MARKS, "cases": {"c": entry}}


def test_an_old_exact_try_that_passed_with_an_extra_now_reads_failed_and_says_so() -> None:
    assert RULES_VERSION >= 3
    tools = {"order": "exact", "expected": TEN}
    doc = _old_run(tools, _ten(_call("x", "peek", 4.5)))
    new = upcast(doc)
    assert new is not doc, "a try that reads differently is a change to keep"
    entry = new["cases"]["c"]
    attempt = entry["tries"][0]
    assert len(attempt["judgements"]) == 2
    now = current(attempt)
    assert now.outcome is Outcome.FAILED and now.rules == RULES_VERSION and now.rules_from == 2
    assert now.route is Route.DIFFERENT
    assert "peek" in next(c.why for c in now.checks if c.id == "steps")
    assert originally(entry) == "pass"
    # Read again, it is already today's.
    assert upcast(new) is new
    # The stored run itself was left as it was.
    assert len(doc["cases"]["c"]["tries"][0]["judgements"]) == 1


def _helped(id_: str, name: str, start: float, agent: str = "researcher") -> Call:
    """A call a delegated agent made, under its AGENT span ``h1``."""
    return Call(id_, name, {}, start, start + 0.5, "h1", agent)


def test_a_helpers_own_calls_dont_fail_extras_off_or_exact() -> None:
    helped = [_helped("h-a", "web_search", 2.5), _helped("h-b", "fetch", 2.7)]
    for tools in ({"extras": False, "expected": TEN}, {"order": "exact", "expected": TEN}):
        steps, score = grade_tools(tools, _ten(*helped))
        assert score == 1.0 and strict_ok(tools, steps) is not False
        assert _steps_outcome(tools, _ten(*helped)) is Outcome.PASSED
        extra = [s for s in steps if s.status == "extra"]
        assert [s.actor for s in extra] == ["researcher", "researcher"]
        assert all(s.reason == EXTRA_HELPER for s in extra)
    # The main agent's own extra still fails.
    tools = {"extras": False, "expected": TEN}
    assert _steps_outcome(tools, _ten(*helped, _call("x", "peek", 9.5))) is Outcome.FAILED


def test_in_says_whose_call_a_step_is() -> None:
    main_only = {"expected": [{"name": "search", "in": "main"}]}
    steps, score = grade_tools(main_only, [_helped("h", "search", 1.0)])
    assert [s.status for s in steps][:1] == ["missing"] and score == 0.0
    steps, _ = grade_tools(main_only, [_helped("h", "search", 1.0), _call("m", "search", 2.0)])
    assert steps[0].status == "matched" and steps[0].span == "m" and steps[0].actor == "main"
    # By default any agent's call may do, as before.
    steps, _ = grade_tools({"expected": [{"name": "search"}]}, [_helped("h", "search", 1.0)])
    assert steps[0].status == "matched" and steps[0].actor == "researcher"
    # A helper by name: only its calls, and its extras count since the test names it.
    named = {"extras": False, "expected": [{"name": "search", "in": "researcher"}]}
    calls = [
        _helped("w", "search", 0.5, agent="writer"),
        _helped("r", "search", 1.0),
        _helped("r2", "fetch", 1.5),
    ]
    steps, _ = grade_tools(named, calls)
    assert steps[0].span == "r"
    counted = [s.span for s in steps if s.status == "extra" and s.reason == EXTRA_OFF]
    assert counted == ["r2"]
    assert strict_ok(named, steps) is False


def test_a_call_knows_the_delegated_agent_it_ran_under_by_name() -> None:
    def span(id_: str, kind: str, parent: str | None, at: float, **attrs: Any) -> Span:
        return Span(id_, "T", parent, id_, kind, at, at + 0.5, "OK", nest(attrs))

    spans = [
        span("root", "AGENT", None, 0.0),
        span("main-call", "TOOL", "root", 0.1, **{"tool.name": "mcp__crm__search"}),
        span("sub", "AGENT", "main-call", 0.2, **{"gen_ai.agent.name": "researcher"}),
        span("sub-call", "TOOL", "sub", 0.3, **{"tool.name": "fetch"}),
    ]
    calls = {c.id: c for c in build(spans, 0.0).calls}
    assert calls["main-call"].actor == "main"
    assert calls["sub-call"].actor == "researcher" and calls["sub-call"].helper == "sub"


def _served(id_: str, raw: str, start: float) -> Call:
    return Call(id_, tool_name(raw), {}, start, start + 0.5, server=tool_server(raw))


def test_a_tool_keeps_its_server() -> None:
    assert (tool_name("mcp__crm__search"), tool_server("mcp__crm__search")) == ("search", "crm")
    assert (tool_name("crm__search"), tool_server("crm__search")) == ("search", "crm")
    assert tool_name("search") == "search"
    assert tool_server("search") is None and tool_server("mcp__search") is None


def test_a_test_may_name_the_server() -> None:
    calls = [_served("d", "mcp__docs__search", 0.0), _served("c", "mcp__crm__search", 1.0)]
    steps, score = grade_tools({"expected": [{"name": "crm__search"}]}, calls)
    assert steps[0].status == "matched" and steps[0].span == "c" and score == 1.0
    # One server only: the bare name reads as before.
    steps, score = grade_tools({"expected": [{"name": "search"}]}, calls[1:])
    assert steps[0].status == "matched" and score == 1.0


def test_two_tools_with_one_name_and_a_bare_step_is_not_judged() -> None:
    calls = [_served("d", "mcp__docs__search", 0.0), _served("c", "mcp__crm__search", 1.0)]
    tools = {"extras": False, "expected": [{"name": "search"}]}
    steps, _ = grade_tools(tools, calls)
    assert steps[0].status == "unjudged"
    assert steps[0].reason == "two tools are named search: say which"
    assert not [s for s in steps if s.status == "extra"]
    case = parse_case({"name": "n", "turns": [{"message": "hi"}], "tools": tools}, "c")
    steps, score = grade_tools(case.tools, calls)
    code = code_checks(case, _evidence(), steps, score, MARKS)
    check = next(c for c in code.checks if c.id == "steps")
    assert check.outcome is Outcome.UNJUDGED and "say which" in check.why
    assert check.unjudged is WhyUnjudged.AMBIGUOUS_TOOL
    assert label_of([judgement([check], None, Route.UNKNOWN)]) is Label.SETUP_BLOCKED
    assert code.route is Route.UNKNOWN


def test_an_old_try_not_judged_for_two_tools_of_one_name_says_so() -> None:
    check = CheckResult(
        id="steps",
        kind=CheckKind.STEPS,
        decided_by=Decider.CODE,
        role=Role.MUST,
        outcome=Outcome.UNJUDGED,
        why="search: two tools are named search: say which",
        unjudged=WhyUnjudged.NOT_SUPPORTED,
    )
    stored = {**judgement([check], None, Route.UNKNOWN).model_dump(mode="json"), "rules": 4}
    entry = {"name": "n", "expected": {}, "tries": [{"n": 1, "judgements": [stored]}]}
    doc = {"schema": SCHEMA, "rules": 4, "pass_marks": MARKS, "cases": {"c": entry}}
    found = current(upcast(doc)["cases"]["c"]["tries"][0])
    assert found.checks[0].unjudged is WhyUnjudged.AMBIGUOUS_TOOL and found.rules_from == 4


def test_a_case_may_say_in() -> None:
    raw = {"name": "n", "turns": [{"message": "hi"}]}
    case = parse_case({**raw, "tools": {"expected": [{"name": "a", "in": "main"}]}}, "c")
    assert case.tools["expected"][0]["in"] == "main"
    parse_case(
        {**raw, "tools": {"expected": [{"parallel": [{"name": "a", "in": "x"}, {"name": "b"}]}]}},
        "c",
    )
    with pytest.raises(CaseError, match=r"expected\[0\]\.in"):
        parse_case({**raw, "tools": {"expected": [{"name": "a", "in": 3}]}}, "c")


def test_an_old_exact_try_failed_by_a_helpers_call_now_passes() -> None:
    tools = {"order": "exact", "expected": TEN}
    # Stored before the agent's name was kept: a helper's call reads as "helper".
    helped = [Call(f"h{i}", "web_search", {}, 2.5 + i / 10, 3.0, "h1") for i in range(3)]
    # Rules 2 counted them: 1.0 - 3 x 0.1 fell below the mark.
    doc = _old_run(tools, _ten(*helped), held=False)
    assert current(doc["cases"]["c"]["tries"][0]).outcome is Outcome.FAILED
    entry = upcast(doc)["cases"]["c"]
    assert current(entry["tries"][0]).outcome is Outcome.PASSED
    assert originally(entry) == "fail"


def test_an_old_try_that_reads_the_same_is_left_alone() -> None:
    for tools, calls in (
        ({"order": "exact", "expected": TEN}, _ten()),
        ({"order": "in_order", "expected": TEN}, _ten(_call("x", "peek", 4.5))),
    ):
        doc = _old_run(tools, calls)
        assert upcast(doc) is doc
