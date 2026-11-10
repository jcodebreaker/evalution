"""One list draws a try's "Step by step": results/timeline.py."""

from typing import Any

from evalution.core.evidence import (
    Clipped,
    PageRecord,
    PageTurn,
    StateRecord,
    ToolRecord,
    TraceStatus,
    TryEvidence,
    TurnRecord,
)
from evalution.core.judgement import (
    CheckKind,
    CheckResult,
    Decider,
    EvidenceRef,
    Outcome,
    Role,
    Route,
)
from evalution.core.rules import judgement
from evalution.results.timeline import timeline


def _check(id_: str, kind: CheckKind, outcome: Outcome, why: str, **more: Any) -> CheckResult:
    by = Decider.MODEL if kind is CheckKind.RUBRIC else Decider.CODE
    return CheckResult(
        id=id_, kind=kind, decided_by=by, role=Role.MUST, outcome=outcome, why=why, **more
    )


def _turn(message: str, reply: str, started: float, ended: float, shown: str = "") -> TurnRecord:
    return TurnRecord(
        kind="message",
        message=Clipped(text=message),
        reply=Clipped(text=reply),
        started=started,
        ended=ended,
        shown=Clipped(text=shown),
    )


def _tool(id_: str, name: str, start: float) -> ToolRecord:
    return ToolRecord(
        id=id_,
        name=name,
        args=Clipped(text="{}"),
        result=Clipped(text="ok"),
        start=start,
        end=start + 0.3,
    )


def _shoe_size() -> tuple[dict[str, Any], TryEvidence]:
    """The mock-up's test: a size given once, used in a new conversation."""
    evidence = TryEvidence(
        case="c",
        n=1,
        t0=0.0,
        seconds=5.8,
        turns=(
            _turn("I wear a size 43, keep that in mind.", "Noted!", 0.0, 1.9, "Noted!"),
            _turn("Find me running shoes.", "What size do you wear?", 2.4, 5.8, "Here are"),
        ),
        tools=(_tool("s1", "add_note", 1.4), _tool("s2", "search_products", 3.6)),
        states=(StateRecord(index=0, key="profile_contains", value="43", at=2.0, ok=False),),
        breaks=(1,),
        trace=TraceStatus.COMPLETE,
        page=PageRecord(
            turns=(PageTurn(shown_at=0.3, shot="t1.png"), PageTurn(shown_at=None, shot="t2.png"))
        ),
    )
    checks = [
        _check("state:0:profile_contains", CheckKind.STATE, Outcome.FAILED, "Profile is empty"),
        _check("never:add_note", CheckKind.NEVER, Outcome.FAILED, "add_note is never to be called"),
        _check("steps", CheckKind.STEPS, Outcome.FAILED, "save_profile: no save_profile call"),
        _check("rubric:0", CheckKind.RUBRIC, Outcome.FAILED, "Only suggests shoes in size 43"),
        _check("rubric:1", CheckKind.RUBRIC, Outcome.FAILED, "Doesn't ask the size again"),
        _check("page_health", CheckKind.PAGE_HEALTH, Outcome.PASSED, "no console errors"),
        _check(
            "shown:1",
            CheckKind.SHOWN,
            Outcome.PASSED,
            "shown",
            evidence=(EvidenceRef(ref="page:1"),),
        ),
        _check(
            "shown:2",
            CheckKind.SHOWN,
            Outcome.FAILED,
            "not shown",
            evidence=(EvidenceRef(ref="page:2"),),
        ),
    ]
    found = judgement(checks, None, Route.DIFFERENT)
    attempt = {
        "n": 1,
        "judgements": [found.model_dump(mode="json")],
        "steps": [
            {
                "status": "missing",
                "expected": "save_profile(size=43)",
                "actual": None,
                "reason": "no save_profile call",
                "span": None,
                "actor": "main",
            },
            {
                "status": "wrong_args",
                "expected": "search_products(size=43)",
                "actual": 'search_products(q="running shoes")',
                "reason": "size missing",
                "span": "s2",
                "actor": "main",
            },
            {
                "status": "never",
                "expected": None,
                "actual": "add_note()",
                "reason": "add_note is never to be called",
                "span": "s1",
                "actor": "main",
            },
            {
                "status": "missing",
                "expected": "state:profile_contains",
                "actual": None,
                "reason": "Profile is empty",
                "span": None,
            },
        ],
        "calls": [
            {"id": "s1", "name": "add_note", "args": {}, "start": 1.4, "end": 1.7, "helper": None},
            {
                "id": "s2",
                "name": "search_products",
                "args": {},
                "start": 3.6,
                "end": 3.9,
                "helper": None,
            },
        ],
        "rubric": [
            {"line": "Only suggests shoes in size 43", "pass": False, "reason": "sizes 38 to 46"},
            {"line": "Doesn't ask the size again", "pass": False, "reason": "it asks"},
        ],
        "reply_text": "What size do you wear?",
        "isolation": {"how": "proven", "restore_seconds": 2.1, "checked": ["db"], "problem": None},
    }
    return attempt, evidence


def test_the_mockups_step_by_step_from_one_list() -> None:
    attempt, evidence = _shoe_size()
    found = timeline(attempt, evidence)
    assert (found[0]["kind"], found[0]["status"], found[0]["seconds"]) == ("before", "passed", 2.1)
    assert (found[-1]["kind"], found[-1]["status"]) == ("health", "passed")
    found = found[1:-1]
    kinds = [(e["kind"], e["status"]) for e in found]
    assert kinds == [
        ("message", None),
        ("call", "failed"),  # save_profile never made: right after the start, before add_note
        ("call", "failed"),  # add_note, never to be called
        ("reply", None),
        ("frame", "passed"),
        ("state", "failed"),
        ("break", None),
        ("message", None),
        ("call", "failed"),  # search_products, wrong arguments
        ("reply", "failed"),
        ("frame", "failed"),
    ]
    for entry in found:
        assert {"kind", "status", "span", "at", "actor"} <= entry.keys()
    reply = found[-2]
    assert [line["text"] for line in reply["lines"]] == [
        "Only suggests shoes in size 43",
        "Doesn't ask the size again",
    ]
    assert reply["lines"][0]["reason"] == "sizes 38 to 46" and reply["lines"][0]["by"] == "model"
    wrong = found[8]
    assert (wrong["step"], wrong["span"], wrong["at"], wrong["actor"]) == (
        "wrong_args",
        "s2",
        3.6,
        "main",
    )
    assert found[0]["actor"] == "person" and found[4]["shot"] == "t1.png"
    assert [e["at"] for e in found] == sorted(e["at"] for e in found)


def test_turn_times_read_from_the_tries_start_as_the_calls_do() -> None:
    """Turns keep clock times; the list gives every entry in seconds from ``t0``."""
    attempt, evidence = _shoe_size()
    clock = 1_794_269_853.4
    evidence = evidence.model_copy(
        update={
            "t0": clock,
            "turns": tuple(
                t.model_copy(update={"started": clock + t.started, "ended": clock + t.ended})
                for t in evidence.turns
            ),
        }
    )
    found = timeline(attempt, evidence)
    at = {(e["kind"], e.get("turn")): e["at"] for e in found if e["kind"] != "call"}
    assert at[("message", 1)] == 0.0 and at[("reply", 1)] == 1.9 and at[("frame", 1)] == 1.9
    assert at[("break", None)] == 2.4 and at[("message", 2)] == 2.4 and at[("reply", 2)] == 5.8
    assert all(e["at"] is None or e["at"] < 10 for e in found)


def test_without_evidence_the_turns_and_calls_still_read() -> None:
    attempt, _ = _shoe_size()
    turns = [{"message": "I wear a size 43"}, {"break": "conversation"}, {"message": "Find shoes"}]
    found = timeline(attempt, None, turns)
    assert found[0]["kind"] == "before" and found[-1]["kind"] == "health"
    found = found[1:-1]
    kinds = [e["kind"] for e in found]
    assert kinds[:3] == ["message", "break", "message"]
    assert kinds.count("call") == 3 and kinds[-2:] == ["reply", "state"]
    assert found[-2]["text"] == "What size do you wear?"


def test_an_allowed_extra_decides_nothing_and_a_helpers_call_says_whose() -> None:
    attempt = {
        "steps": [
            {
                "status": "extra",
                "expected": None,
                "actual": "peek()",
                "reason": "not in the case; allowed",
                "span": "a",
                "actor": "main",
            },
            {
                "status": "extra",
                "expected": None,
                "actual": "fetch()",
                "reason": "a delegated agent's own call; not counted",
                "span": "b",
                "actor": "researcher",
            },
            {
                "status": "extra",
                "expected": None,
                "actual": "peek()",
                "reason": "not in the case; extras are off",
                "span": "c",
                "actor": "main",
            },
        ],
        "calls": [
            {"id": "a", "start": 1.0},
            {"id": "b", "start": 2.0},
            {"id": "c", "start": 3.0},
            {"id": "hook", "name": "recall", "start": 0.5},
        ],
        "automatic": ["hook"],
    }
    found = timeline(attempt)
    assert found[0]["automatic"] and found[0]["made"] == "recall()"
    assert [(e["status"], e["actor"]) for e in found[1:]] == [
        (None, "main"),
        (None, "researcher"),
        ("failed", "main"),
    ]
