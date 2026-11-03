"""How good is the grader? Every seeded bug and every healthy agent, with a real model grading and
nothing else deciding: code checks are taken out of the tests, so what comes back is the grader's
alone. Not a test: it costs money, so it runs only when asked.

    EVALUTION_MODEL_KEY=... BENCH_TRIES=3 \\
    uv run --all-groups pytest tests/proof/bench_judge.py -o python_files='bench_*.py' -s

``BENCH_ROWS`` keeps to the first few, to see what it costs. ``BENCH_OUT`` writes the results as
JSON. ``BENCH_MODEL`` picks the model, by default the one in ``evalution.yaml``'s settings.
"""

import json
import os
from pathlib import Path

import pytest
from scripted_agent import LIBRARY, SET, StubJudge
from test_matrix import CODE_DECIDED, MODEL_JUDGED

from evalution.config import ModelConfig
from evalution.core.evidence import Clipped, ToolRecord, TraceStatus, TryEvidence, TurnRecord
from evalution.core.judgement import Outcome
from evalution.core.rules import current
from evalution.judging.rubric import INSTRUCTIONS as GRADER_INSTRUCTIONS
from evalution.judging.rubric import SCEPTIC_INSTRUCTIONS, grade_reply
from evalution.llm import LLM
from evalution.runs.runner import Runs
from evalution.suite.library import load_templates

MODEL = os.environ.get("BENCH_MODEL", "claude-sonnet-5-5")
TRIES = int(os.environ.get("BENCH_TRIES", "1"))
ROWS = int(os.environ.get("BENCH_ROWS", "0"))


class GraderOnly:
    """The runner's model: the real one for grading. Writing filler and reports costs nothing here,
    and has no say in a verdict."""

    def __init__(self, llm: LLM) -> None:
        self.real = llm.caller("strong")
        self.stub = StubJudge()
        self.llm = llm
        self.calls = 0

    def caller(self, key: str) -> "GraderOnly":
        return self

    def model_id(self, key: str) -> str:
        return MODEL

    async def __call__(self, *, instructions: str, input_text: str) -> str:
        if instructions in (GRADER_INSTRUCTIONS, SCEPTIC_INSTRUCTIONS):
            self.calls += 1
            return await self.real(instructions=instructions, input_text=input_text)
        return await self.stub(instructions=instructions, input_text=input_text)


def _graded_alone(rig, stem: str) -> None:
    """Leave only what the grader decides: the rubric."""
    path = rig.set_root / SET / f"{stem}.case.json"
    case = json.loads(path.read_text())
    case["turns"] = [t for t in case["turns"] if "expect_state" not in t]
    case.pop("tools", None)
    case["reply"] = {"rubric": case["reply"]["rubric"]}
    path.write_text(json.dumps(case))


async def _play(make_rig, llm, template: str, fault: str | None) -> dict:
    rig = make_rig([fault] if fault else [])
    grader = GraderOnly(llm)
    rig.runs = Runs(rig.targets, rig.files, grader)
    stem = rig.fill(template)
    _graded_alone(rig, stem)
    doc = await rig.run(tries=TRIES)
    tries = [t for c in doc["cases"].values() for t in c["tries"]]
    return {
        "template": template,
        "fault": fault,
        "outcomes": [current(t).outcome.value for t in tries],
        "replies": [t["reply_text"] for t in tries],
        "reasons": [
            [
                (r["line"], r["pass"], r.get("sure", True), r["reason"])
                for r in t.get("rubric") or []
            ]
            for t in tries
        ],
        "tools": [sorted(c["name"] for c in t.get("calls") or []) for t in tries],
        "calls": grader.calls,
    }


@pytest.mark.skipif("EVALUTION_MODEL_KEY" not in os.environ, reason="spends: asked for only")
async def test_how_good_is_the_grader(make_rig, monkeypatch) -> None:
    async def nothing(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(Runs, "_analyze", nothing)
    config = ModelConfig(
        kind="anthropic",
        base_url="https://api.anthropic.com",
        api_key_env="EVALUTION_MODEL_KEY",
        models={"strong": MODEL, "light": MODEL},
    )
    llm = LLM(config)
    templates = sorted(t.id for t in load_templates(LIBRARY) if not t.kind)
    bugs = [(r.template, r.fault) for r in (*CODE_DECIDED, *MODEL_JUDGED)]
    plays = [*((t, None) for t in templates), *bugs]
    if ROWS:
        plays = plays[:ROWS]
    results = [await _play(make_rig, llm, template, fault) for template, fault in plays]

    healthy = {r["template"]: r for r in results if r["fault"] is None}
    table = {"caught": 0, "missed": 0, "unsure_on_bug": 0, "hidden": 0}
    table |= {"right": 0, "false_alarm": 0, "unsure_on_healthy": 0}
    for row in results:
        if row["fault"] is None:
            table["right"] += row["outcomes"].count(Outcome.PASSED.value)
            table["false_alarm"] += row["outcomes"].count(Outcome.FAILED.value)
            table["unsure_on_healthy"] += row["outcomes"].count(Outcome.UNJUDGED.value)
            continue
        base = healthy.get(row["template"])
        # A bug that changes nothing in the reply or the tools isn't there for a grader to see.
        same = (
            base and set(row["replies"]) == set(base["replies"]) and row["tools"] == base["tools"]
        )
        row["hidden"] = bool(same)
        if same:
            table["hidden"] += len(row["outcomes"])
            continue
        table["caught"] += row["outcomes"].count(Outcome.FAILED.value)
        table["missed"] += row["outcomes"].count(Outcome.PASSED.value)
        table["unsure_on_bug"] += row["outcomes"].count(Outcome.UNJUDGED.value)

    print(f"\nmodel {MODEL}, {TRIES} tries each, {sum(r['calls'] for r in results)} grading calls")
    print(f"spent ${llm.spent_usd:.4f}")
    print(json.dumps(table))
    for row in results:
        print(
            row["template"],
            row["fault"] or "healthy",
            row["outcomes"],
            "hidden" if row.get("hidden") else "",
        )
    if out := os.environ.get("BENCH_OUT"):
        Path(out).write_text(  # noqa: ASYNC240
            json.dumps({"model": MODEL, "tries": TRIES, "table": table, "rows": results}, indent=1)
        )


CASES = Path(__file__).with_name("judge_cases.json")


@pytest.mark.skipif("EVALUTION_MODEL_KEY" not in os.environ, reason="spends: asked for only")
async def test_how_good_is_the_grader_on_the_hard_ones() -> None:
    """Labelled replies to one rubric line each, written to be hard: a right answer in other words,
    half an answer, a confident lie, a mention that isn't an answer, a claim tools don't back."""
    llm = LLM(
        ModelConfig(
            kind="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="EVALUTION_MODEL_KEY",
            models={"strong": MODEL, "light": MODEL},
        )
    )
    caller = llm.caller("strong")
    rows = []
    for case in json.loads(CASES.read_text()):
        turn = TurnRecord(
            kind="message",
            message=Clipped(text="Please go ahead."),
            reply=Clipped(text=case["reply"]),
            started=0.0,
            ended=1.0,
        )
        tools = tuple(
            ToolRecord(
                id=f"t{i}",
                name=t["name"],
                args=Clipped(text="{}"),
                result=Clipped(text=t["result"]),
                start=0.1,
                end=0.2,
            )
            for i, t in enumerate(case.get("tools") or [])
        )
        evidence = TryEvidence(
            case="c",
            n=1,
            t0=0.0,
            seconds=1.0,
            turns=(turn,),
            tools=tools,
            trace=TraceStatus.COMPLETE,
        )
        said = []
        for _ in range(TRIES):
            graded = await grade_reply(caller, evidence, "", [case["line"]])
            said.append(graded.checks[0].outcome.value)
            reason = graded.lines[0]["reason"]
        wanted = Outcome.PASSED.value if case["holds"] else Outcome.FAILED.value
        rows.append({**case, "said": said, "wanted": wanted, "last_reason": reason})

    tally: dict[str, dict[str, int]] = {}
    for row in rows:
        for group in (row["group"], "all"):
            found = tally.setdefault(group, {"right": 0, "wrong": 0, "unsure": 0})
            for one in row["said"]:
                key = (
                    "right" if one == row["wanted"] else "unsure" if one == "unjudged" else "wrong"
                )
                found[key] += 1
    false_fail = sum(row["said"].count("failed") for row in rows if row["wanted"] == "passed")
    false_pass = sum(row["said"].count("passed") for row in rows if row["wanted"] == "failed")
    print(f"\nmodel {MODEL}, {TRIES} tries each, {len(rows)} cases, spent ${llm.spent_usd:.4f}")
    print(f"false passes {false_pass}, false fails {false_fail}")
    for group, found in tally.items():
        print(group, found)
    for row in rows:
        if set(row["said"]) != {row["wanted"]}:
            print("MISS", row["group"], "|", row["line"], "|", row["reply"][:60], "|", row["said"])
            print("   ", row["last_reason"][:160])
    if out := os.environ.get("BENCH_HARD_OUT"):
        found = {"model": MODEL, "tries": TRIES, "tally": tally, "rows": rows}
        Path(out).write_text(json.dumps(found, indent=1))  # noqa: ASYNC240
