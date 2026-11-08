"""Each stateful library test, filled for the scripted agent through the real library path and
played end to end by the real runner.

The judge passes every rubric line, on the healthy agent and on the broken one alike. So:

* **Code-decided** rows have a check the code decides. A failure there came from that check, with
  the judge saying the opposite: this is detection.
* **Model-judged** rows have no such check. Their verdict is whatever the judge was scripted to
  say, here a pass, broken or not: *not provable here*. All they can show is that the seeded bug
  is visible in what a real judge would read. No judge is scripted to fake a catch.
"""

from dataclasses import dataclass

import pytest
from scripted_agent import (
    EFFECT_WORDS,
    LIBRARY,
    StubJudge,
    final_reply,
)

from evalution.adapters import infos
from evalution.core.case import CONTEXT_CHECKS
from evalution.core.judgement import CheckKind, Outcome
from evalution.core.rules import current, layers
from evalution.results.views import _run_test
from evalution.suite.library import load_templates


@dataclass(frozen=True)
class Caught:
    """A code-decided library test against a seeded bug."""

    template: str
    fault: str
    #: The state checks that must miss on the broken agent, and hold on the healthy one.
    missed: tuple[str, ...] = ()
    #: Other code checks that must fail on the broken agent, by id.
    failed: tuple[str, ...] = ()


@dataclass(frozen=True)
class NotProvable:
    """A model-judged library test: what the seeded bug looks like in the judge's material."""

    template: str
    fault: str
    #: In the final reply of the healthy agent and not the broken one, or the other way round.
    healthy_says: str | None = None
    broken_says: str | None = None


CODE_DECIDED = (
    Caught(
        "compaction-keeps-early-decision",
        "summary_drops_early_facts",
        ("state:context_contains",),
    ),
    Caught(
        "context-lacks-withdrawn-fact",
        "withdrawn_fact_still_sent",
        ("state:context_lacks",),
    ),
    Caught(
        "forget-on-request",
        "forget_says_done_only",
        ("state:memory_lacks", "state:context_lacks"),
    ),
    # The same test, with memory cleared but the fact still sent: only the trace check can see it.
    Caught("forget-on-request", "withdrawn_fact_still_sent", ("state:context_lacks",)),
    Caught("memory-recalls-fact", "forgets_to_store", ("state:memory_contains",)),
    # The right word in the reply is the code's; the rest of what it says is the judge's.
    Caught("correction-replaces-fact", "correction_keeps_old_fact", failed=("reply:contains:0",)),
    Caught("long-chat-keeps-name-role", "summary_drops_early_facts", failed=("reply:contains:0",)),
    Caught("recall-right-conversation", "forgets_to_store", failed=("reply:contains:0",)),
)
MODEL_JUDGED = (
    NotProvable("preference-across-conversations", "forgets_to_store", healthy_says=EFFECT_WORDS),
    NotProvable("task-resumes-new-conversation", "forgets_to_store", healthy_says="three-day trip"),
    NotProvable("withdrawn-instruction", "withdrawn_fact_still_sent", broken_says=EFFECT_WORDS),
)
ALL_TEMPLATES = sorted({r.template for r in CODE_DECIDED} | {r.template for r in MODEL_JUDGED})
SHAPES = ("openinference", "genai")


def _steps(doc: dict, name: str) -> list[dict]:
    return [
        s
        for c in doc["cases"].values()
        for t in c["tries"]
        for s in t["steps"]
        if s["expected"] == name
    ]


def _verdict(doc: dict) -> str:
    ((stem, entry),) = doc["cases"].items()
    return _run_test(doc, stem, entry)["verdict"]


def _sure_the_judge_passed_everything(doc: dict) -> None:
    lines = [li for c in doc["cases"].values() for t in c["tries"] for li in t["rubric"]]
    assert lines and all(line["pass"] for line in lines)


def test_the_matrix_covers_the_library_and_tells_the_two_kinds_by_the_case_itself() -> None:
    templates = {t.id: t for t in load_templates(LIBRARY) if t.kind is None}
    assert sorted(templates) == ALL_TEMPLATES
    decided = {
        t.id
        for t in templates.values()
        if t.state_keys
        or {"contains", "lacks"} & t.case["reply"].keys()
        or any(
            k in CONTEXT_CHECKS for turn in t.case["turns"] for k in turn.get("expect_state", {})
        )
    }
    assert decided == {r.template for r in CODE_DECIDED}
    assert len(decided) == 7 and len(templates) == 10


def test_it_is_found_like_any_adapter(make_rig) -> None:
    make_rig()
    assert "scripted_agent" in infos()


@pytest.mark.parametrize("template", ALL_TEMPLATES)
async def test_the_healthy_agent_passes(make_rig, template) -> None:
    rig = make_rig(judge=StubJudge())
    rig.fill(template)
    doc = await rig.run()
    assert doc["status"] == "done"
    assert _verdict(doc) == "pass"
    for entry in doc["cases"].values():
        for attempt in entry["tries"]:
            assert attempt["error"] is None
            assert attempt["trajectory"], "its spans came through the real read path"
            states = [s for s in attempt["steps"] if (s["expected"] or "").startswith("state:")]
            assert all(s["status"] == "matched" for s in states), states
            # Played through the API alone: the page has no say (ADR 0010).
            kinds = {c.kind for c in current(attempt).checks}
            assert not kinds & {CheckKind.SHOWN, CheckKind.PAGE_HEALTH}
            assert layers(current(attempt))["shown"] == "absent"


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("row", CODE_DECIDED, ids=lambda r: f"{r.template}-{r.fault}")
async def test_a_code_decided_test_catches_its_bug(make_rig, row, shape) -> None:
    """Failed by the code, with the judge saying every line held."""
    healthy = make_rig(shape=shape, judge=StubJudge())
    healthy.fill(row.template)
    doc = await healthy.run()
    assert _verdict(doc) == "pass"
    for name in row.missed:
        assert all(s["status"] == "matched" for s in _steps(doc, name))

    broken = make_rig([row.fault], shape=shape, judge=StubJudge())
    broken.fill(row.template)
    doc = await broken.run()
    _sure_the_judge_passed_everything(doc)
    assert _verdict(doc) == "fail"
    for name in row.missed:
        steps = _steps(doc, name)
        assert steps and all(s["status"] == "missing" for s in steps), steps
    for check_id in row.failed:
        for attempt in [t for c in doc["cases"].values() for t in c["tries"]]:
            found = {c.id: c.outcome for c in current(attempt).checks}
            assert found[check_id] is Outcome.FAILED, found


@pytest.mark.parametrize("row", MODEL_JUDGED, ids=lambda r: r.template)
async def test_a_model_judged_test_is_not_provable_here(make_rig, row) -> None:
    """The same verdict on the broken agent: nothing in the code tells them apart. The bug does
    show in what the judge reads, which is as far as this can go without a real model."""
    healthy_judge, broken_judge = StubJudge(), StubJudge()
    healthy = make_rig(judge=healthy_judge)
    healthy.fill(row.template)
    healthy_doc = await healthy.run()
    broken = make_rig([row.fault], judge=broken_judge)
    broken.fill(row.template)
    broken_doc = await broken.run()

    assert _verdict(healthy_doc) == "pass"
    assert _verdict(broken_doc) == "pass", "code-decided now? Move this row to CODE_DECIDED"

    was_shown = final_reply(healthy_judge.inputs[0]), final_reply(broken_judge.inputs[0])
    if row.healthy_says:
        assert row.healthy_says in was_shown[0] and row.healthy_says not in was_shown[1]
    if row.broken_says:
        assert row.broken_says in was_shown[1] and row.broken_says not in was_shown[0]
