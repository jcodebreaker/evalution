"""The test page: a case as plain steps (worded once by the light model, else mechanically) and
a stored try laid over it."""

import json

from evalution.core.judgement import (
    CheckKind,
    CheckResult,
    Decider,
    Judgement,
    JudgeStamp,
    Outcome,
    Role,
    WhyUnjudged,
)
from evalution.judging.rubric import parse_grade
from evalution.results.migrate import upcast
from evalution.results.views import case_row
from evalution.review.testplan import case_plan, compare, explain, peek, trace_words
from evalution.storage import Database
from evalution.suite.cases import parse_case
from evalution.suite.store import import_sets, read_file, sent_files

CASE = {
    "name": "Keeps the total after a long chat",
    "source": "scenario matrix",
    "turns": [
        {"message": "What's the North total in this file?", "files": ["orders.csv"]},
        {"filler": 40, "about": "planning a trip"},
        {"expect_state": {"checkpoint": True, "memory_contains": "North"}},
        {"message": "And what was that total again?"},
    ],
    "tools": {
        "order": "in_order",
        "extras": True,
        "expected": [{"name": "run_code", "args": {"code": {"contains": "North"}}}],
        "never": ["hand_off"],
    },
    "reply": {
        "reference": "Gives the North total, 4,828.13, worked out by code.",
        "rubric": ["States 4,828.13 exactly", "Worked it out with code"],
    },
    "limits": {"tries": 3, "seconds": 120},
}
LABELS = {"run_code": "Run code"}
PASS = {"tools": 0.8, "reply": 0.7}


def _case():
    return parse_case(CASE, "case", "total", "v1")


def _set(tmp_path):
    """The files the case sends, as a set folder imported brings them."""
    folder = tmp_path / "sets" / "bot" / "main"
    folder.mkdir(parents=True)
    (folder / "set.json").write_text(json.dumps({"name": "Main"}))
    rows = "\n".join(f"North,item{i},{i}.25" for i in range(240))
    (folder / "orders.csv").write_text(f"region,product,amount\n{rows}\n")
    (tmp_path / "sets" / "bot" / "secret.csv").write_text("no,peeking\n")
    (folder / "fixtures").mkdir()
    (folder / "fixtures" / "logo.png").write_bytes(b"\x89PNG\xff")
    # A file that leads outside the set folder never comes in.
    (folder / "link.csv").symlink_to(tmp_path / "sets" / "bot" / "secret.csv")
    docs = Database.open(tmp_path / "data").docs()
    import_sets(docs, tmp_path / "sets")
    assert read_file(docs, "bot", "main", "link.csv") is None
    assert read_file(docs, "bot", "main", "fixtures/logo.png") == b"\x89PNG\xff"
    return sent_files(docs, "bot", "main", _case())


async def test_a_case_as_plain_steps_worded_once_else_mechanically(tmp_path) -> None:
    files = _set(tmp_path)
    plan = await case_plan(_case(), files, LABELS, PASS, None, None)
    kinds = [s["kind"] for s in plan["steps"]]
    assert kinds == ["before", "say", "filler", "state", "say", "agent", "reply", "passes"]
    before = plan["steps"][0]
    assert before["files"][0]["rows"] == 240
    assert before["files"][0]["columns"] == ["region", "product", "amount"]
    assert before["files"][0]["peek"].startswith("region,product,amount\nNorth,item0")
    assert plan["steps"][2]["title"] == "40 ordinary messages about planning a trip"
    state = [i["text"] for i in plan["steps"][3]["items"]]
    assert state == ["A checkpoint was written", "Memory holds “North”"]
    agent = {i["id"]: i for i in plan["steps"][5]["items"]}
    assert agent["call-0"]["text"] == "Run code" and agent["call-0"]["judged"] == "exact"
    assert agent["never-hand_off"]["mode"] == "never"
    assert agent["rule"]["text"] == "In this order; other steps are fine"
    reply = plan["steps"][6]
    assert [i["text"] for i in reply["items"]] == CASE["reply"]["rubric"]
    assert plan["steps"][7]["note"].startswith("Any failed try makes it flaky")
    assert plan["limits"] == ["3 tries", "≤ 120 s each", "from scenario matrix"]

    # A file the set doesn't have, or that isn't text, is named only.
    assert peek("link.csv", None) == {"name": "link.csv"}
    assert peek("photo.csv", b"\xff\xd8") == {"name": "photo.csv"}

    asked: list[str] = []

    async def light(*, instructions: str, input_text: str) -> str:
        asked.append(input_text)
        return json.dumps(
            {
                "titles": {"1": "Asks for a total", "5": "Work it out with code"},
                "items": {
                    "never-hand_off": {"text": "Never hands the work to a helper"},
                    "rubric-0": {"text": "rewritten"},
                },
                "tools": {"hand_off": "Start a helper"},
                "behaviour": ["rubric-1"],
            }
        )

    cache = tmp_path / "data" / "plans" / "total.json"
    worded = await case_plan(_case(), files, LABELS, PASS, light, cache)
    again = await case_plan(_case(), files, LABELS, PASS, light, cache)
    assert worded == again and len(asked) == 1  # one call per case version
    assert '"call-0"' not in asked[0]  # a labelled call is not asked about
    steps = worded["steps"]
    assert steps[1]["title"] == "Asks for a total"
    agent = {i["id"]: i for i in steps[5]["items"]}
    assert agent["never-hand_off"]["text"] == "Never hands the work to a helper"
    assert agent["rubric-1"]["judged"] == "steps"  # about what it does: judged from the steps
    assert [i["text"] for i in steps[6]["items"]] == ["States 4,828.13 exactly"]  # verbatim
    assert worded["names"]["hand_off"] == "Start a helper"

    async def broken(*, instructions: str, input_text: str) -> str:
        return "no json here"

    fallback = await case_plan(_case(), files, LABELS, PASS, broken, None)
    assert fallback == plan


def _judgement(*checks, **fields) -> Judgement:
    return Judgement(outcome=Outcome.FAILED, checks=checks, rules=3, **fields)


def _check(id_, kind, outcome, decider=Decider.CODE, role=Role.MUST, **fields) -> CheckResult:
    fields.setdefault("why", id_)
    if outcome is Outcome.UNJUDGED:
        fields.setdefault("unjudged", WhyUnjudged.JUDGE_UNSURE)
    return CheckResult(id=id_, kind=kind, decided_by=decider, role=role, outcome=outcome, **fields)


def test_every_score_says_what_it_counted_and_who_decided() -> None:
    p, f, u = Outcome.PASSED, Outcome.FAILED, Outcome.UNJUDGED
    judged = _judgement(
        _check("state:0:memory", CheckKind.STATE, p),
        _check("never:x", CheckKind.NEVER, f),
        _check("steps", CheckKind.STEPS, f, score=0.5),
        _check("limit", CheckKind.LIMIT, p, role=Role.GUIDE),
        _check(
            "rubric", CheckKind.RUBRIC, p, Decider.MODEL, why="2 of 3 lines held, 2 needed", cut=9
        ),
        _check("rubric:0", CheckKind.RUBRIC, p, Decider.MODEL, role=Role.GUIDE),
        _check("rubric:1", CheckKind.RUBRIC, p, Decider.MODEL, role=Role.GUIDE),
        _check("rubric:2", CheckKind.RUBRIC, u, Decider.MODEL, role=Role.GUIDE),
        judge=JudgeStamp(key="builder", model="fake-model"),
    )
    steps = [{"status": "matched"}, {"status": "missing"}, {"status": "extra"}]
    told = explain(judged, {"steps": steps}, 2, {"tools": 0.8})
    # A guide isn't in the count, and a model's line isn't code's.
    assert (told["code"]["held"], told["code"]["total"]) == (1, 3)
    assert {c["id"] for c in told["code"]["checks"]} == {
        "state:0:memory",
        "never:x",
        "steps",
        "limit",
    }
    assert next(c for c in told["code"]["checks"] if c["id"] == "limit")["guide"]
    text = next(c["text"] for c in told["code"]["checks"] if c["id"] == "steps")
    assert text == "1 of 2 expected steps done · steps score 0.50, needs 0.80"
    model = told["model"]
    assert (model["held"], model["total"], model["unjudged"]) == (2, 3, 1)
    assert model["summary"] == "2 of 3 lines held, 2 needed" and model["cut"] == 9
    assert (told["judge"], told["rules"]) == ("fake-model", 3)
    # With no model in it, nothing says one graded it.
    bare = explain(_judgement(_check("steps", CheckKind.STEPS, p)), {}, 0, {})
    assert bare["judge"] is None and bare["model"]["total"] == 0


def test_a_try_says_plainly_when_its_traces_fell_short() -> None:
    clean = _judgement(_check("x", CheckKind.STATE, Outcome.FAILED))
    assert trace_words({"trace": "complete"}, clean) is None
    assert trace_words({}, clean) is None, "an old try with nothing against it says nothing"
    partial = {"trace": "partial", "trace_note": "the read hit the span cap"}
    assert "span cap" in trace_words(partial, clean) and "part" in trace_words(partial, clean)
    assert "No traces" in trace_words({"trace": "empty"}, clean)
    assert "no traces to read" in trace_words({"trace": "no_source"}, clean)
    # A try from before this says so through the check it couldn't make.
    blind = _check("never:x", CheckKind.NEVER, Outcome.UNJUDGED, unjudged=WhyUnjudged.NO_TRACES)
    assert "No traces" in trace_words({}, _judgement(blind))


def test_a_score_with_nothing_to_count_is_no_score() -> None:
    tries = [{"tools": 1.0, "reply": 1.0, "seconds": 1, "tokens": 1, "usd": 0.0, "n": 1}]
    done = {"judgements": [{"outcome": "passed", "checks": [], "rules": 1}]}
    entry = {"expected": {}, "reply_rubric": [], "case": {}, "tries": [{**tries[0], **done}]}
    row = case_row("c", "C", entry)
    assert row["tools"] is None and row["reply"] is None
    # The controls: something to count, or a stored test without its copy.
    entry |= {"expected": {"never": ["x"]}, "reply_rubric": ["a line"]}
    row = case_row("c", "C", entry)
    assert (row["tools"], row["reply"]) == (1.0, 1.0)
    del entry["reply_rubric"], entry["case"], entry["expected"]
    row = case_row("c", "C", entry)
    assert (row["tools"], row["reply"]) == (1.0, 1.0)


async def test_a_stored_try_laid_over_the_plan(tmp_path) -> None:
    plan = await case_plan(_case(), _set(tmp_path), LABELS, PASS, None, None)
    reply = "The North region totals 4,828.10 across 96 orders."
    graded = parse_grade(
        json.dumps(
            {
                "lines": [
                    {
                        "id": 1,
                        "pass": False,
                        "reason": "said 4,828.10",
                        "quotes": ["4,828.10", "absent"],
                    },
                    {"id": 2, "pass": True, "reason": "ran code", "quotes": []},
                ],
                "score": 0.4,
            }
        ),
        CASE["reply"]["rubric"],
    )
    failed = {
        "n": 2,
        "passed": False,
        "messages": 42,
        "reply_text": reply,
        "rubric": graded.lines,
        "steps": [
            {
                "status": "missing",
                "expected": "run_code(code contains North)",
                "actual": None,
                "reason": "no run_code call",
                "span": None,
            },
            {
                "status": "never",
                "expected": None,
                "actual": "hand_off(task=sum)",
                "reason": "hand_off is never to be called",
                "span": "c2",
            },
            {
                "status": "extra",
                "expected": None,
                "actual": "files_read(name=orders.csv)",
                "reason": "not in the case; allowed",
                "span": "c1",
            },
            {
                "status": "matched",
                "expected": "state:checkpoint",
                "actual": "yes",
                "reason": "a checkpoint was written",
                "span": None,
            },
            {
                "status": "missing",
                "expected": "state:memory_contains",
                "actual": None,
                "reason": "no memory holds North",
                "span": None,
            },
        ],
        "calls": [
            {"id": "c1", "name": "files_read", "start": 1.0},
            {"id": "c2", "name": "hand_off", "start": 2.0},
        ],
        "limits_broken": [],
    }
    passed = {
        "n": 1,
        "passed": True,
        "tools": 1.0,
        "messages": 42,
        "reply_text": "4,828.13",
        "rubric": [{"line": "x", "pass": True, "reason": ""}] * 2,
        "steps": [
            {
                "status": "matched",
                "expected": "run_code(...)",
                "actual": "run_code(code=x)",
                "reason": "called at 3 s",
                "span": "a",
            }
        ],
        "calls": [{"id": "a", "name": "run_code", "start": 3.0}],
    }
    entry = upcast({"cases": {"c": {"expected": CASE["tools"], "tries": [passed, failed]}}})
    entry = entry["cases"]["c"]
    shown = compare(plan, entry, None, {2: "No run_code call"}, None, ["The reply to it"])
    assert shown["try"] == 2 and not shown["passed"]  # the first failed try by default
    said = [s["actual"].get("reply") for s in shown["steps"] if s["kind"] == "say"]
    assert said[0] == "The reply to it" or not said[0]
    items = {i["id"]: i for s in shown["steps"] for i in s["items"]}
    assert items["call-0"]["actual"] == {"passed": False, "text": "Not done", "calls": []}
    assert items["never-hand_off"]["actual"]["calls"] == ["hand_off(task=sum)"]
    assert items["rule"]["actual"]["passed"] and items["rule"]["actual"]["calls"]
    assert items["state-0-checkpoint"]["actual"]["passed"]
    assert not items["state-0-memory_contains"]["actual"]["passed"]
    assert items["rubric-0"]["actual"]["text"] == "said 4,828.10"
    assert [q["text"] for q in items["rubric-0"]["actual"]["quotes"]] == ["4,828.10", "absent"]
    assert "quotes" not in items["rubric-1"]["actual"]
    assert all(s["actual"]["passed"] for s in shown["steps"] if s["kind"] in ("say", "filler"))
    assert shown["steps"][-1]["actual"]["text"] == "No run_code call"

    lanes = {lane["n"]: lane["steps"] for lane in shown["trajectory"]["tries"]}
    assert shown["trajectory"]["expected"] == ["Run code", "Reply"]
    assert [(s["label"], s["status"]) for s in lanes[2]] == [
        ("Run code", "missing"),
        ("Files read", "extra"),
        ("Hand off", "wrong"),
        ("Reply", "wrong"),
    ]
    assert [s["status"] for s in lanes[1]] == ["ok", "ok"]
    assert shown["output"]["highlights"] == [
        {"text": "4,828.10", "good": False, "item": "rubric-0"}
    ]
    assert shown["output"]["reference"] == CASE["reply"]["reference"]
    assert shown["output"]["must"] == CASE["reply"]["rubric"]

    # Expected next to what it did, a row each, the state checks apart.
    rows = shown["tool_rows"]
    assert [(r["expected"], r["status"]) for r in rows] == [
        ("run_code(code contains North)", "missing"),
        (None, "never"),
        (None, "extra"),
    ]
