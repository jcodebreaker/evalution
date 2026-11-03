"""The tool-call grade: alignment, parallel, never and extras."""

from evalution.core.trace import Call
from evalution.judging.steps import grade_tools


def _call(id_: str, name: str, start: float, end: float, **args: object) -> Call:
    return Call(id_, name, args, start, end)


def test_in_order_with_extras_and_matchers() -> None:
    tools = {
        "order": "in_order",
        "extras": True,
        "expected": [
            {"name": "search", "args": {"q": {"contains": "Churn"}}},
            {"name": "write", "times": 2},
        ],
    }
    calls = [
        _call("a", "search", 0, 1, q="q3 churn numbers"),
        _call("b", "read", 1, 2),
        _call("c", "write", 2, 3),
        _call("d", "write", 3, 4),
    ]
    steps, score = grade_tools(tools, calls)
    assert score == 1.0
    assert [s.status for s in steps] == ["matched", "matched", "matched", "extra"]


def test_parallel_late_never_and_extras_off() -> None:
    tools = {
        "extras": False,
        "expected": [{"parallel": [{"name": "start"}, {"name": "start"}]}],
        "never": ["delete"],
    }
    calls = [
        _call("a", "start", 0, 5),
        _call("b", "start", 36, 40),
        _call("c", "delete", 41, 42),
        _call("d", "peek", 43, 44),
    ]
    steps, score = grade_tools(tools, calls)
    statuses = [s.status for s in steps]
    assert statuses == ["matched", "late", "never", "extra"]
    assert "31 s" in steps[1].reason
    assert score == round(0.5 - 0.25 - 0.1, 3)


def test_wrong_args_and_missing() -> None:
    tools = {
        "order": "exact",
        "expected": [{"name": "save", "args": {"path": {"re": r"\.md$"}}}, {"name": "notify"}],
    }
    steps, score = grade_tools(tools, [_call("a", "save", 0, 1, path="notes.txt")])
    assert [s.status for s in steps] == ["wrong_args", "missing"]
    assert score == 0.0
