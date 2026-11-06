"""What the page reads of a stored run, pinned: the same run files must keep giving the same
``summary``, ``detail``, ``case_detail``, ``latest_rows`` and ``health``, to the byte.

The run files in ``fixtures/runs`` are neutral stand-ins for real ones (``v0-edge`` adds what those
never showed: tries without traces, a target that went away, an older file's ``pass_scores``).
They are schema 0, so this pins reading those too (:mod:`evalution.results.migrate`). To change a
view on purpose: ``UPDATE_GOLDENS=1 uv run pytest tests/test_golden_views.py``, and read the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from evalution.core.rules import RULES_VERSION, SERVER_RESTARTED, TARGET_GONE
from evalution.project.drift import stale
from evalution.results.migrate import upcast
from evalution.results.record import SCHEMA, regrade_rules
from evalution.results.store import RUN, RunStore
from evalution.results.views import case_detail, detail, latest_rows, summary
from evalution.review.health import health
from evalution.storage import Database
from evalution.suite.cases import parse_case
from evalution.suite.library import Template

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = FIXTURES / "golden"
#: An hour after the newest run, so nothing here reads the clock.
NOW = 1793590242.0
NAMES = ("v0-first", "v0-second", "v0-edge")
READY, EARLY = "the app wasn't ready", "it stopped early"
FOUND = {
    "changes": [
        {"id": "c1", "kind": "tool_removed", "tool": "tool_a", "detail": "Fake removal."},
        {"id": "c2", "kind": "prompt_changed", "seen_at": 1793583000.0, "summary": "Fake change."},
    ]
}
DOC = {
    "priorities": [{"id": "p1", "title": "Fake priority"}],
    "starters": [{"title": "Fake test 1", "priority": "p1"}],
}
VERSIONS = [
    {"fingerprint": "aaaa", "written_at": 1793570000.0},
    {"fingerprint": "bbbb", "written_at": 1793580000.0},
]
TEMPLATES = [
    Template(
        id="fake-template",
        title="Fake",
        checks="Fake",
        why="Fake",
        needs=("memory",),
        est_turns=2,
        case={},
        placeholders=(),
        state_keys=(),
    )
]


def _run(name: str) -> dict[str, Any]:
    return upcast(json.loads((FIXTURES / "runs" / f"{name}.json").read_text(encoding="utf-8")))


def _runs() -> list[dict[str, Any]]:
    """Newest first, as the store hands them out."""
    runs = [_run(name) for name in NAMES]
    return sorted(runs, key=lambda r: float(r["started_at"]), reverse=True)


def _cases(run: dict[str, Any]) -> list[Any]:
    """The run's tests as files, as far as its own copy of them says."""
    return [
        parse_case(
            {
                "name": entry["name"],
                "turns": [{"message": "Fake message."}],
                "tools": entry["expected"],
            },
            stem,
            stem,
        )
        for stem, entry in run["cases"].items()
    ]


def _views(name: str) -> dict[str, Any]:
    run = _run(name)
    others = [r for r in _runs() if r["set"] == run["set"]]
    return {
        "summary": summary(run),
        "detail": detail(run, others, None),
        "case_detail": {stem: case_detail(run, stem) for stem in run["cases"]},
        "case_detail_unknown": case_detail(run, "no-such-test"),
        "latest_rows": latest_rows(_cases(run), others, lambda case: stale(case, FOUND)),
    }


def _health(period: str, days_later: int) -> dict[str, Any]:
    runs = _runs()
    cases = {"main": _cases(runs[1]), "edge": _cases(runs[0])}
    titles = {"main": "Main", "edge": "Edge"}
    now = NOW + days_later * 24 * 3600
    return health(runs, cases, titles, DOC, VERSIONS, FOUND, TEMPLATES, period, now)


def _check(name: str, found: Any) -> None:
    path = GOLDEN / f"{name}.json"
    text = json.dumps(found, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDENS"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_a_stored_run_reads_the_same(name: str) -> None:
    _check(name, _views(name))


@pytest.mark.parametrize(("period", "days_later"), [("7d", 0), ("all", 0), ("7d", 8), ("30d", 8)])
def test_health_reads_the_same(period: str, days_later: int) -> None:
    _check(f"health-{period}-{days_later}", _health(period, days_later))


def test_the_edge_run_still_shows_what_it_was_made_for() -> None:
    """The goldens only pin what these files reach, so say what that has to include."""
    found = _views("v0-edge")
    tests = {t["case"]: t for t in found["summary"]["tests"]}
    assert tests["no-traces"]["verdict"] == "unjudged"
    assert "no_traces" in tests["no-traces"]["unjudged"]
    # One try without traces, one passed, one whose last line neither grading was sure of.
    assert tests["some-no-traces"]["verdict"] == "pass"
    assert tests["some-no-traces"]["originally"] == "flaky"
    assert tests["not-played"]["reason"] == "the app wasn't ready"
    assert found["summary"]["pass_marks"] == {"tools": 0.8, "reply": 0.7}
    assert [r["case"] for r in found["latest_rows"] if r["stale"]]


def test_upcast_is_pure_and_keeps_what_it_read() -> None:
    raw = json.loads((FIXTURES / "runs" / "v0-edge.json").read_text(encoding="utf-8"))
    raw["report"] = {"headline": "Fake.", "well": [], "wrong": [], "next": []}
    before = json.dumps(raw)
    read = upcast(raw)
    assert json.dumps(raw) == before, "the document it was given is untouched"
    assert upcast(read) is read
    assert read["schema"] == SCHEMA and read["pass_marks"] == raw["pass_scores"]
    # Every run then played on the computer that kept it.
    assert read["environment"] == {"where": "local", "name": None}
    one = {k: v for k, v in read.items() if k != "environment"} | {"schema": 1}
    assert upcast(one)["environment"] == read["environment"] and "environment" not in one
    assert read["judge"] == {"key": raw["grader"], "model": None, "prompt_hash": None}
    assert read["report"]["verified"] is False
    old = raw["cases"]["flaky"]["tries"]
    new = read["cases"]["flaky"]["tries"]
    assert [t["legacy_passed"] for t in new] == [t["passed"] for t in old]
    assert all("passed" not in t for t in new)
    assert {j["rules_from"] for t in new for j in t["judgements"]} == {0}


def test_migrate_rewrites_stored_runs_and_twice_changes_nothing(tmp_path) -> None:
    store = RunStore(Database.open(tmp_path).docs())
    for name in NAMES:
        store.docs.put(RUN, name, json.loads((FIXTURES / "runs" / f"{name}.json").read_text()))
    store.docs.put(RUN, "not-a-run", [1, 2])
    first, unreadable = store.upcast_all()
    assert sorted(first) == sorted(NAMES) and unreadable == ["not-a-run"]
    written = {key: body for key, body, _ in store.docs.items(RUN)}
    assert store.upcast_all() == ([], ["not-a-run"])
    assert written == {key: body for key, body, _ in store.docs.items(RUN)}
    assert all(written[name]["schema"] == SCHEMA for name in NAMES)
    assert store.load("v0-edge") == written["v0-edge"], "what it wrote reads as it is"


def test_regrading_under_the_rules_only_adds() -> None:
    run = _run("v0-second")
    assert regrade_rules(run) == 0, "already under today's rules"
    attempt = next(t for c in run["cases"].values() for t in c["tries"])
    attempt["judgements"][-1]["rules"] = 0
    attempt["judgements"][-1]["outcome"] = "unjudged"
    attempt["judgements"][-1]["rules_from"] = None
    run["rules"] = 0
    assert regrade_rules(run) == 1
    assert len(attempt["judgements"]) == 2 and attempt["judgements"][0]["outcome"] == "unjudged"
    found = attempt["judgements"][-1]
    assert (found["rules"], found["rules_from"]) == (RULES_VERSION, 0)
    assert found["outcome"] != "unjudged" or any(
        c["unjudged"] and c["role"] == "must" for c in found["checks"]
    )
    assert run["rules"] == RULES_VERSION
    assert regrade_rules(run) == 0


@pytest.mark.parametrize(
    ("stored", "reason"),
    [
        ({"failure": "app_not_ready", "error": "the app's session went away"}, READY),
        ({"failure": "other", "error": "ConnectError: not the app's"}, EARLY),
        # Stored before ``failure``: only the text says.
        ({"error": "AppError: shop answered 503: starting"}, READY),
        ({"error": "ConnectError: refused"}, READY),
        ({"error": "ReadTimeout: slow"}, READY),
        ({"error": "the app's session went away", "stop_reason": TARGET_GONE}, READY),
        ({"error": "KeyError: 'x'"}, EARLY),
        # Stored before a restart read as stopped.
        ({"error": SERVER_RESTARTED, "stop_reason": SERVER_RESTARTED}, "the server restarted"),
    ],
)
def test_why_a_failed_run_has_no_verdict(stored: dict[str, str], reason: str) -> None:
    run = {"status": "failed", "cases": {"t": {"name": "T", "tries": []}}, **stored}
    assert summary(run)["tests"][0]["reason"] == reason
