"""The change card: a change is measured from what was last tested, interpreted once, and goes
away once its tests ran. Offline: the model is a fake, the code a plain folder."""

import asyncio
import json

import pytest
import yaml

from evalution.config import load
from evalution.project import impact
from evalution.project.profile import read_doc, set_updates
from evalution.review.health import for_target
from evalution.runs.runner import Runs
from evalution.suite.cases import import_sets, load_cases
from evalution.targets import Targets

PASSED = {
    "n": 1,
    "passed": True,
    "seconds": 1.0,
    "tokens": 1,
    "usd": 0.1,
    "tools": 1.0,
    "reply": 1.0,
}


def _answer(**fields):
    return json.dumps(
        {"summary": "s", "areas": ["memory"], "tests": ["main/a"], "uncovered": [], **fields}
    )


def test_ids_are_checked_against_real_tests() -> None:
    known = ["main/a", "main/b"]
    read = impact.read_answer(_answer(tests=["main/a", "main/nope", "main/a"]), known)
    assert read["tests"] == ["main/a"] and read["areas"] == ["memory"]
    assert impact.read_answer(_answer(tests=[]), known)["tests"] == []
    with pytest.raises(ValueError, match="none of them"):
        impact.read_answer(_answer(tests=["main/nope"]), known)
    with pytest.raises(ValueError):
        impact.read_answer("no json here", known)


@pytest.fixture
def app(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "memory.py").write_text("a = 1")
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    tests = tmp_path / "sets" / "bot" / "main"
    tests.mkdir(parents=True)
    (tests / "set.json").write_text(json.dumps({"name": "Main"}))
    for stem in ("a", "b"):
        (tests / f"{stem}.case.json").write_text(
            json.dumps({"name": stem.upper(), "turns": [{"message": "hi"}]})
        )
    config = load(tmp_path / "evalution.yaml")
    runs = Runs(Targets(config), None)
    import_sets(runs.targets.docs, tmp_path / "sets")
    runs.tmp = tmp_path
    set_updates(runs.targets, "bot", "auto")
    return runs


def _ran(runs, n, stamp, tests, scope="some"):
    """A finished run played on the code as *stamp* recorded it."""
    doc = {
        "run": f"r{n}",
        "target": "bot",
        "set": "main",
        "scope": scope,
        "status": "done",
        "started_at": stamp["at"],
        "prompt": "",
        "totals": {},
        "cases": {t: {"name": t, "tries": [PASSED]} for t in tests},
        **{k: stamp[k] for k in ("commit", "code")},
    }
    asyncio.run(runs.store.save(doc))


def _stamp(runs):
    found = impact.record_run(runs.targets, "bot")
    doc = read_doc(runs.targets, "bot", impact.TESTED_DOC)
    return {**found, "at": doc["runs"][found["code"]]["at"] - 1}


def _card(runs):
    cases = {"main": load_cases(runs.targets.docs, "bot", "main")}
    return impact.card(runs.targets, "bot", runs.all("bot"), cases)


def _edit(runs, text):
    (runs.tmp / "app" / "memory.py").write_text(text)


def test_a_change_lives_from_the_tested_state_until_its_tests_ran(app, monkeypatch) -> None:
    runs = app
    calls = []

    async def model(*, instructions, input_text):
        calls.append(input_text)
        return _answer(tests=["main/a"])

    monkeypatch.setattr(runs, "caller", lambda _key: model)

    # Nothing to compare with before any run.
    asyncio.run(impact.refresh(runs, "bot"))
    assert _card(runs) is None and not calls

    first = _stamp(runs)
    assert first["commit"] is None
    _ran(runs, 1, first, ["a", "b"], scope="all")
    asyncio.run(impact.refresh(runs, "bot"))
    assert _card(runs) is None and not calls

    _edit(runs, "a = 2")
    asyncio.run(impact.refresh(runs, "bot"))
    asyncio.run(impact.refresh(runs, "bot"))
    card = _card(runs)
    # One call for one change; not a checkout, so the file list is what the model reads.
    assert len(calls) == 1 and "memory.py" in calls[0] and "the diff" not in calls[0]
    assert card["areas"] == ["memory"] and card["files"] == ["memory.py"]
    assert card["tests"] == [{"set": "main", "case": "a", "name": "A"}]

    # Running the affected test takes it off; the tested state moves to this code.
    second = _stamp(runs)
    _ran(runs, 2, second, ["a"])
    assert _card(runs) is None
    asyncio.run(impact.refresh(runs, "bot"))
    tested = read_doc(runs.targets, "bot", impact.TESTED_DOC)["tested"]
    assert tested["code"] == second["code"]
    assert read_doc(runs.targets, "bot", impact.IMPACT_DOC)["current"] is None

    # Told it touches nothing: no card, but only a run moves the state, so it is not forgotten.
    async def nothing(*, instructions, input_text):
        calls.append(input_text)
        return json.dumps({"summary": "", "areas": [], "tests": [], "uncovered": []})

    monkeypatch.setattr(runs, "caller", lambda _key: nothing)
    _edit(runs, "a = 3")
    asyncio.run(impact.refresh(runs, "bot"))
    assert _card(runs) is None
    tested = read_doc(runs.targets, "bot", impact.TESTED_DOC)["tested"]
    assert tested["code"] == second["code"]


def test_without_a_model_every_test_is_affected(app) -> None:
    runs = app
    _ran(runs, 1, _stamp(runs), ["a", "b"], scope="all")
    _edit(runs, "a = 3")
    asyncio.run(impact.refresh(runs, "bot"))
    card = _card(runs)
    assert card["areas"] == [] and [t["case"] for t in card["tests"]] == ["a", "b"]
    assert for_target(runs.targets, runs, "bot", "7d")["code_change"] == card
    # Not remembered: a model set up later still reads this change.
    assert read_doc(runs.targets, "bot", impact.IMPACT_DOC)["seen"] == {}


def test_manual_shows_the_files_and_all_tests_until_asked(app, monkeypatch) -> None:
    runs = app
    set_updates(runs.targets, "bot", "manual")
    calls = []

    async def model(*, instructions, input_text):
        calls.append(1)
        return _answer(tests=["main/a"])

    monkeypatch.setattr(runs, "caller", lambda _key: model)
    _ran(runs, 1, _stamp(runs), ["a", "b"], scope="all")
    _edit(runs, "a = 5")
    asyncio.run(impact.refresh(runs, "bot"))
    asyncio.run(impact.refresh(runs, "bot"))
    card = _card(runs)
    assert not calls and not card["read"] and len(card["tests"]) == 2

    # Turning updates on leaves this change as it is: the next one is the first read by itself.
    impact.hold(runs.targets, "bot")
    set_updates(runs.targets, "bot", "auto")
    asyncio.run(impact.refresh(runs, "bot"))
    assert not calls

    asyncio.run(impact.refresh(runs, "bot", asked=True))
    asyncio.run(impact.refresh(runs, "bot", asked=True))
    card = _card(runs)
    assert len(calls) == 1 and card["read"] and [t["case"] for t in card["tests"]] == ["a"]


def test_a_failed_call_falls_back_and_is_not_paid_for_twice(app, monkeypatch) -> None:
    runs = app
    calls = []

    async def model(*, instructions, input_text):
        calls.append(1)
        raise RuntimeError("down")

    monkeypatch.setattr(runs, "caller", lambda _key: model)
    _ran(runs, 1, _stamp(runs), ["a", "b"], scope="all")
    _edit(runs, "a = 4")
    asyncio.run(impact.refresh(runs, "bot"))
    asyncio.run(impact.refresh(runs, "bot"))
    assert len(calls) == 1 and len(_card(runs)["tests"]) == 2
