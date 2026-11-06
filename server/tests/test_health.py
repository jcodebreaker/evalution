"""The Home page's health: tiles, areas, what needs attention, and the empty state, from
fake run files."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load
from evalution.core.rules import RULES_VERSION, steps_bind
from evalution.judging.claims import EvidenceIndex
from evalution.results import baseline
from evalution.results.compare import comparison, default_base
from evalution.results.markdown import comparison_markdown
from evalution.results.migrate import upcast
from evalution.results.record import regrade_rules
from evalution.results.views import detail, summary
from evalution.review import health
from evalution.review.findings import parse_report, write_report
from evalution.suite import library
from evalution.suite.cases import parse_case

DAY = 24 * 3600
NOW = 100 * DAY
TEMPLATES = library.load_templates(Path(__file__).parents[2] / "library")


#: The words a run of that age used for a try without traces.
NO_TRACES = "No traces arrived, so this try could not be judged"


def _on_record(run):
    """A stored run as if played today: comparable with others like it."""
    done = upcast(run)
    done["judge"]["model"] = "m"
    done["case_versions"] = {stem: "v1" for stem in done["cases"]}
    return done


def _try(n, passed, seconds=10.0, usd=0.1, analysis=None):
    return {
        "n": n,
        "passed": passed,
        "seconds": seconds,
        "tokens": 10,
        "usd": usd,
        "tools": 1.0 if passed else 0.0,
        "reply": 1.0,
        "error": None if passed else "boom",
        "analysis": analysis,
    }


def _run(run, at, results, *, prompt="p1", scope="all", **extra):
    """A finished run of set ``main``; *results* maps a test to its tries' outcomes."""
    return {
        "run": run,
        "target": "bot",
        "set": "main",
        "scope": scope,
        "status": "done",
        "started_at": at,
        "prompt": prompt,
        "cases": {
            stem: {
                "name": stem.title(),
                "source": None,
                "expected": {},
                "tries": [_try(i, p) for i, p in enumerate(passed, 1)],
            }
            for stem, passed in results.items()
        },
        **extra,
    }


def _case(stem, source=None, tools=None):
    data = {"name": stem.title(), "turns": [{"message": "hi"}], "tools": tools or {}}
    if source:
        data["source"] = source
    return parse_case(data, stem, stem)


def _health(runs, cases=(), doc=None, found=None, period="7d"):
    return health.health(
        sorted(map(_on_record, runs), key=lambda r: r["started_at"], reverse=True),
        {"main": list(cases)},
        {"main": "Main"},
        doc,
        [],
        found or {"changes": []},
        TEMPLATES,
        period,
        NOW,
    )


def test_home_counts_what_passed_and_adds_no_score() -> None:
    steady = _health([_run("r1", NOW - DAY, {"a": [True], "b": [True]})])
    assert steady["tiles"]["passing"]["value"] == 2
    assert not {"score", "verdict", "change"} & steady.keys()

    runs = [
        _run("r1", NOW - 3 * DAY, {"a": [True], "b": [True]}),
        _run("r2", NOW - DAY, {"a": [False, False], "b": [True, False]}, prompt="p2"),
    ]
    found = _health(runs)
    # 0 of 2 pass; b was flaky (1 of 2 consistent); the share fell from 1 to 0.
    assert found["tiles"]["passing"] == {
        "value": 0,
        "total": 2,
        "delta": -2,
        "model_only": 0,
        "unjudged": 0,
    }
    assert found["tiles"]["consistent"] == {"value": 1, "total": 2}
    assert found["trend"][1]["marker"] == "Prompt changed"
    assert [r["results"] for r in found["recent"]] == [["fail", "flaky"], ["pass", "pass"]]
    # A run outside the period is not in its trend.
    old = _run("r0", NOW - 20 * DAY, {"a": [True]})
    assert len(_health([old, *runs])["trend"]) == 2
    assert len(_health([old, *runs], period="30d")["trend"]) == 3
    # A run whose traces never arrived was not judged, so it says nothing about the app.
    blind = _run("r3", NOW - 60, {"a": [False]})
    blind["cases"]["a"]["tries"][0].update(error=NO_TRACES, usd=0.0)
    assert _health([blind, *runs]) == found
    assert summary(upcast(blind))["tests"] == [
        {
            "case": "a",
            "name": "A",
            "verdict": "unjudged",
            "label": "Missing evidence",
            "basis": "none",
            "reason": None,
            "unjudged": ["no_traces"],
            "originally": None,
            "passed": 0,
            "different": 0,
            "tries": 0,
            "usd": 0.0,
        }
    ]
    # Runs judged another way don't make a change between runs: the rules moved, not the app.
    older = _run("r0", NOW - 6.9 * DAY, {"a": [True], "b": [True]})
    mixed = [_on_record(runs[1]), upcast(older)]
    seen = health.health(
        mixed, {"main": []}, {"main": "Main"}, None, [], {"changes": []}, TEMPLATES, "7d", NOW
    )
    assert seen["tiles"]["passing"]["delta"] is None
    crashed = _run("r4", NOW, {"a": []}, status="failed", error="AppError: answered 409")
    assert summary(crashed)["tests"][0]["reason"] == "the app wasn't ready"


def test_areas_follow_priorities_then_library_families_then_sets() -> None:
    cases = [
        _case("recall", "library: memory-recalls-fact"),
        _case("compact", "library: compaction-keeps-early-decision"),
        _case("greets"),
    ]
    doc = {
        "priorities": [{"id": "remember", "title": "Remembering what you tell it"}],
        "starters": [{"id": "s1", "library": "memory-recalls-fact", "priority": "remember"}],
    }
    run = _run("r1", NOW - DAY, {"recall": [True], "compact": [False], "greets": [True]})
    found = _health([run], cases, doc)
    assert found["areas"] == [
        {"name": "Greets", "pct": 100, "level": "strong"},
        {"name": "Remembering what you tell it", "pct": 100, "level": "strong"},
        {"name": "Long chats", "pct": 0, "level": "weak"},
    ]


def test_attention_is_ranked_and_capped() -> None:
    gone = {"id": "d1", "kind": "tool_removed", "detail": "tool search is gone", "tool": "search"}
    finding = {"summary": "Never searched", "cause": "c", "fix": "f", "kind": "assistant"}
    runs = [
        _run("r1", NOW - 3 * DAY, {"broken": [False], "wobbly": [True]}),
        _run("r2", NOW - 2 * DAY, {"broken": [False], "wobbly": [True]}),
        _run(
            "r3",
            NOW - DAY,
            {"broken": [False], "wobbly": [True, False, True], "new": [False]},
            analysis_error="Part of the analysis didn't finish. Try again.",
        ),
    ]
    runs[2]["cases"]["broken"]["tries"][0]["analysis"] = finding
    cases = [_case("searches", tools={"expected": [{"name": "search"}]})]
    found = _health(runs, cases, found={"changes": [gone]})["attention"]
    assert [(a["action"], a.get("case")) for a in found] == [
        ("see_why", "broken"),
        ("open_test", "wobbly"),
        ("review", "searches"),
        ("review", None),
        ("see_why", "new"),
    ]
    assert found[0]["title"] == "Broken keeps failing" and found[0]["detail"] == "boom"
    assert found[1]["detail"] == "2 of 3 tries passed"
    assert found[4]["title"] == "New fails"
    wobbly = _run("x", NOW - DAY, {f"t{i}": [True, True, False] for i in range(6)})
    assert len(_health([wobbly])["attention"]) == health.ATTENTION


def test_a_test_a_commit_broke_is_named_with_the_commit() -> None:
    older = "aaaaaaa1111"
    newer = "bbbbbbb2222"
    runs = [
        _run("r1", NOW - 3 * DAY, {"a": [True], "b": [False], "c": [True]}, commit=older),
        # No commit: an earlier run says nothing about what broke.
        _run("r2", NOW - 2 * DAY, {"d": [True]}),
        _run(
            "r3", NOW - DAY, {"a": [False], "b": [False], "c": [True], "d": [False]}, commit=newer
        ),
    ]
    found = _health(runs)["attention"]
    assert found[0]["title"] == "A passed on aaaaaaa, fails on bbbbbbb"
    assert (found[0]["action"], found[0]["case"], found[0]["run"]) == ("see_why", "a", "r3")
    # Once only, and a test failing on both commits, or with no commit before, is no regression.
    assert [a["case"] for a in found if "passed on" in a["title"]] == ["a"]
    assert [a["case"] for a in found].count("a") == 1
    assert {a["case"] for a in found} == {"a", "b", "d"}


def test_an_old_run_rereading_differently_is_marked_and_breaks_nothing() -> None:
    """A try that failed only on a line neither grading was sure of: unjudged today. Its pass on a
    later commit is no improvement, and no later failure against it is a regression."""
    old = _run("r1", NOW - 2 * DAY, {"a": [False], "c": [True]}, commit="a" * 40)
    old["cases"]["a"]["tries"][0].update(
        error=None,
        rubric=[
            {
                "line": "L",
                "pass": False,
                "sure": False,
                "second": {"pass": False, "sure": False, "reason": ""},
            }
        ],
    )
    old["cases"]["a"]["reply_rubric"] = ["L"]
    read = upcast(old)
    assert summary(read)["tests"][0]["originally"] == "fail"
    assert summary(read)["tests"][0]["verdict"] == "unjudged"
    assert summary(read)["tests"][1]["originally"] is None
    played = _run("r2", NOW - DAY, {"a": [True], "b": [True]}, commit="b" * 40)
    now = _on_record(played)
    was_passing = upcast(_run("r0", NOW - 3 * DAY, {"b": [True]}, commit="c" * 40))
    failing = _on_record(_run("r3", NOW - 60, {"b": [False]}, commit="d" * 40))
    assert health.regressions([failing, was_passing]) == []
    assert health.regressions([failing, now])[0]["case"] == "b"
    found = health.health(
        [now, read], {"main": []}, {"main": "Main"}, None, [], {"changes": []}, TEMPLATES, "7d", NOW
    )
    assert found["trend"][0]["marker"] == "Rules changed"
    assert found["tiles"]["passing"]["delta"] is None


def test_read_again_under_todays_rules_a_run_stays_apart_from_todays_checks() -> None:
    old = _on_record(_run("r0", NOW - 3 * DAY, {"b": [True]}, commit="c" * 40))
    old["rules"] = RULES_VERSION - 1
    new = _on_record(_run("r3", NOW - 60, {"b": [False]}, commit="d" * 40))
    new["rules"] = RULES_VERSION
    regrade_rules(old)
    assert old["rules"] == new["rules"] == RULES_VERSION
    assert health.regressions([new, old]) == [], "its checks were made under the old rules"
    args = ({"main": []}, {"main": "Main"}, None, [], {"changes": []}, TEMPLATES, "7d", NOW)
    found = health.health([new, old], *args)
    assert found["trend"][-1]["marker"] == "Rules changed"
    # Control: both played under today's rules compare, regress, and carry no marker.
    same = _on_record(_run("r1", NOW - 2 * DAY, {"b": [True]}, commit="e" * 40))
    same["rules"] = RULES_VERSION
    assert health.regressions([new, same])[0]["case"] == "b"
    found = health.health([new, same], *args)
    assert found["trend"][-1].get("marker") != "Rules changed"


def test_an_old_run_missing_fields_still_reads() -> None:
    """Fields a run from before they were kept lacks, or holds as null, read as what the page
    parses: it plays all of its set, with no prompt on record."""
    whole = upcast(_run("r0", NOW - DAY, {"a": [True]}))
    for changed in ({"scope": None, "prompt": None}, {}):
        run = {**whole, **changed}
        if not changed:
            del run["scope"], run["prompt"]
        found = summary(run)
        assert found["scope"] == "all" and found["prompt"] == ""
        assert detail(run, [run], None)["history"][0]["prompt"] == ""
    # Control: what it has, it keeps.
    some = {**whole, "scope": "some", "prompt": "p9"}
    assert summary(some)["scope"] == "some" and summary(some)["prompt"] == "p9"
    assert detail(some, [some], None)["history"][0]["prompt"] == "p9"


def test_an_app_without_runs_reads_empty(tmp_path) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        found = client.get("/v1/targets/bot/health").json()
        assert client.get("/v1/targets/bot/health?period=1y").status_code == 400
    assert found.pop("knowledge")["out_of_date"] == 0
    assert found == {
        "report": None,
        "report_pending": False,
        "tiles": {
            "passing": {
                "value": 0,
                "total": 0,
                "delta": None,
                "model_only": 0,
                "unjudged": 0,
            },
            "consistent": {"value": 0, "total": 0},
            "reply_seconds": {"value": None, "delta": None},
            "cost": {"per_question": None, "period_total": 0},
        },
        "areas": [],
        "trend": [],
        "attention": [],
        "recent": [],
        "code_change": None,
        "checking": False,
    }


def test_a_report_keeps_to_real_tests_and_says_nothing_without_a_judged_try() -> None:
    answer = json.dumps(
        {
            "causes": [
                {"text": "It guessed.", "case": "a", "refs": ["turn:1"]},
                {"text": "Odd.", "case": "made-up", "refs": ["turn:1"]},
            ],
            "next": ["Look at the prompt."],
        }
    )
    found = parse_report(answer, ["a"])
    assert [w["case"] for w in found["reading"]] == ["a", None]
    assert not any(w["verified"] for w in found["reading"]), "nothing to check them against"
    known = EvidenceIndex(frozenset({"turn:1"}), "")
    checked = parse_report(answer, ["a"], {"a": known})["reading"]
    assert [w["verified"] for w in checked] == [True, False]
    # What went well is held to the same evidence, and the facts name what passed.
    well = json.dumps({"well": [{"case": "a", "text": "It held.", "refs": ["turn:9"]}]})
    assert parse_report(well, ["a"], {"a": known})["well"][0]["verified"] is False

    calls = []

    async def model(*, instructions, input_text):
        calls.append(input_text)
        return answer

    run = upcast(_run("r1", NOW, {"a": [True, False]}))
    asyncio.run(write_report(model, run, None))
    assert run["report"]["reading"][0]["text"] == "It guessed." and "1 of 2 tries" in calls[0]

    blind = _run("r2", NOW, {"a": [False]})
    blind["cases"]["a"]["tries"][0]["error"] = NO_TRACES
    blind = upcast(blind)
    asyncio.run(write_report(model, blind, None))
    assert "report" not in blind and len(calls) == 1
    report = _health([{**run, "report": found}])["report"]
    assert report["run"] == "r1" and report["next"][0]["text"] == "Look at the prompt."
    # Next steps are claims like the rest; one stored as bare words reads as citing nothing.
    assert not report["next"][0]["verified"]
    stored = _health([{**run, "report": {**found, "next": ["Old words."]}}])["report"]
    assert stored["next"][0] == {
        "case": None,
        "text": "Old words.",
        "refs": [],
        "quotes": [],
        "verified": False,
    }
    # What it came to is the code's, whatever the model said, and with no model at all.
    assert report["facts"]["passed"] == 0 and report["facts"]["total"] == 1
    assert report["facts"]["failed"][0]["verdict"] == "flaky"
    without = _health([{**run, "report": None}])["report"]
    assert without["facts"] == report["facts"] and without["reading"] == []


def test_steps_bind_a_try_only_when_the_path_is_the_point() -> None:
    def case(tools, reply=None):
        data = {"name": "t", "turns": [{"message": "hi"}], "tools": tools}
        return parse_case({**data, **({"reply": reply} if reply else {})}, "t", "t")

    said = {"rubric": ["Says hi"]}

    assert not steps_bind(case({"expected": [{"name": "a"}]}, said))
    assert steps_bind(case({"expected": [{"name": "a"}], "strict": True}, said))
    assert steps_bind(case({"expected": [{"name": "a"}], "order": "exact"}, said))
    assert steps_bind(case({"expected": [{"name": "a"}], "extras": False}, said))
    assert steps_bind(case({"expected": [{"name": "a"}]}))


def test_an_old_report_reads_as_unchecked_claims() -> None:
    old = {"headline": "H", "well": ["It held."], "wrong": [{"text": "It broke.", "case": "a"}]}
    doc = upcast({**_run("r1", NOW, {"a": [True, False]}), "report": old})
    report = doc["report"]
    assert report["well"] == [
        {"case": None, "text": "It held.", "refs": [], "quotes": [], "verified": False}
    ]
    assert report["reading"][0]["case"] == "a" and report["reading"][0]["verified"] is False


def test_a_comparison_keeps_a_test_edited_between_runs_and_says_so() -> None:
    before = _on_record(_run("r1", NOW - DAY, {"a": [True] * 4, "b": [True] * 4}))
    now = _on_record(_run("r2", NOW, {"a": [False] * 4, "b": [True] * 4}))
    found = comparison(now, before)
    tests = {t["case"]: t for t in found["tests"]}
    assert found["answer"] == "worse" and found["headline"] == "1 test got worse"
    assert tests["a"]["change"] == "worse" and not tests["a"]["chance"]
    assert tests["b"]["change"] == "same" and found["tests"][0]["case"] == "a", "worst first"
    edited = {**now, "case_versions": {"a": "new"}}
    was = {**before, "case_versions": {"a": "old"}}
    assert comparison(edited, was)["tests"][0]["caveats"] == ["the test was edited"]
    assert default_base(now, [now, before]) == "r1"
    assert default_base(before, [now, before]) is None, "nothing earlier"
    # The newest run on another setup, over the one right before it on the same.
    other = {**before, "commit": "a" * 40}
    middle = {**now, "run": "r1b", "started_at": NOW - 60, "commit": "b" * 40}
    latest = {**now, "commit": "b" * 40}
    assert default_base(latest, [latest, middle, other]) == "r1"
    # A run of the app somewhere else is no base: the app, its data and its network differ.
    staging = {**latest, "environment": {"where": "local", "name": "staging"}}
    assert default_base(staging, [staging, middle, other]) is None
    assert default_base({**staging, "run": "r9", "started_at": NOW + 60}, [staging, middle]) == "r2"
    # Two connected apps are two setups, and five tries each can settle what three can't.
    across = comparison({**now, "target": "b"}, {**before, "target": "a"})
    assert {"what": "App", "before": "a", "now": "b"} in across["changed"]
    many = comparison(
        _on_record(_run("r4", NOW, {"a": [False] * 3})),
        _on_record(_run("r3", NOW - DAY, {"a": [True] * 3})),
    )
    assert many["could_be_chance"] and many["headline"].startswith("It looks like")
    five = comparison(
        _on_record(_run("r6", NOW, {"a": [False] * 5})),
        _on_record(_run("r5", NOW - DAY, {"a": [True] * 3})),
    )
    assert not five["could_be_chance"] and five["headline"] == "1 test got worse"
    # For a pull request: worse, better and could-be-chance apart, linked when there's a page.
    mixed_now = _on_record(_run("r8", NOW, {"a": [False] * 5, "b": [True] * 5, "c": [False] * 3}))
    mixed_was = _on_record(
        _run("r7", NOW - DAY, {"a": [True] * 5, "b": [False] * 5, "c": [True] * 3})
    )
    said = comparison(mixed_now, mixed_was)
    text = comparison_markdown(said, mixed_now)
    assert text.startswith("### 2 tests got worse and 1 test got better\n")
    worse, better, chance = (
        text.split(f"**{part}**\n")[1] for part in ("Worse", "Better", "Could be chance")
    )
    assert worse.startswith("- A `a`: 5/5 → 0/5\n")
    assert better.startswith("- B `b`: 0/5 → 5/5\n")
    assert chance == "- C `c`: 3/3 → 0/3 (worse)\n"
    linked = comparison_markdown(said, mixed_now, "http://evals.test/")
    assert "- [A](http://evals.test/?target=bot&set=main&view=runs&run=r8&case=a) `a`" in linked
    # Accepting one test takes its newer result into the baseline and leaves the others'.
    kept = baseline.accept(mixed_was, "a", [mixed_now])
    assert kept["cases"]["a"] == mixed_now["cases"]["a"]
    assert {k: v for k, v in kept["cases"].items() if k != "a"} == {
        k: v for k, v in mixed_was["cases"].items() if k != "a"
    }
    assert kept["run"] == "r7" and mixed_was["cases"]["a"] != kept["cases"]["a"], "a copy"
    with pytest.raises(LookupError, match="no test nope"):
        baseline.accept(mixed_was, "nope", [mixed_now])
    with pytest.raises(LookupError, match="no run of a"):
        baseline.accept(mixed_was, "a", [])
