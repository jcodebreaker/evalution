"""One record of what the code was (docs/plans/next-level.md, C2): the learned, refreshed and
tested marks in one document, folded in from the old ones, and one answer for every signal the page
gets. Offline: a temp git repo and a fake model."""

import asyncio
import json
import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load
from evalution.project import drift, filememory, impact, knowledge, state, understanding
from evalution.project.profile import FINGERPRINT_DOC, read_doc, write_doc
from evalution.review import health
from evalution.runs.runner import Runs
from evalution.suite.store import import_sets
from evalution.targets import Targets

PASSED = {"n": 1, "passed": True, "seconds": 1.0, "tokens": 1, "tools": 1.0, "reply": 1.0}


def _git(folder, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def _commit(folder, files):
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(folder, "add", "-A")
    _git(folder, "commit", "-qm", "change")


def _doc():
    return {
        "one_line": "A bot.",
        "nodes": [],
        "edges": [],
        "paths": [],
        "sections": [
            {
                "id": "can_do",
                "title": "Can do",
                "claims": [
                    {
                        "id": "direct",
                        "text": "It refunds.",
                        "evidence": [{"kind": "code", "ref": "tools/a.py (run)"}],
                    }
                ],
            }
        ],
        "priorities": [],
        "starters": [],
        "step_labels": {},
        "expected_paths": [],
    }


@pytest.fixture
def app(tmp_path, monkeypatch):
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, {"tools/a.py": "from lib.b import run\n", "lib/b.py": "def run(): return 1\n"})
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    sets = tmp_path / "sets" / "bot" / "main"
    sets.mkdir(parents=True)
    (sets / "set.json").write_text(json.dumps({"name": "Main"}))
    (sets / "a.case.json").write_text(json.dumps({"name": "A", "turns": [{"message": "hi"}]}))
    runs = Runs(Targets(load(tmp_path / "evalution.yaml")), None)
    import_sets(runs.targets.docs, tmp_path / "sets")
    runs.repo = repo
    runs.calls = []
    runs.answer = json.dumps({"claims": []})

    async def model(*, instructions, input_text):
        runs.calls.append(input_text)
        return runs.answer

    monkeypatch.setattr(runs, "caller", lambda _key: model)
    return runs


def _learned(runs):
    """What a finished learn keeps: the understanding, the drift baseline, the knowledge links and
    what the learn saw of the code."""
    targets = runs.targets
    code = targets.code("bot")
    understanding.save(targets, "bot", _doc())
    _found, taken = asyncio.run(drift.take(targets, "bot", 1))
    drift.keep_baseline(targets, "bot", taken)
    knowledge.written(targets, "bot", code)
    filememory.learned(targets, "bot", code)


def _full_run(runs, n):
    stamp = impact.record_run(runs.targets, "bot")
    at = state.read(runs.targets, "bot")["tested"]["runs"][stamp["code"]]["at"] - 1
    doc = {
        "run": f"r{n}",
        "target": "bot",
        "set": "main",
        "scope": "all",
        "status": "done",
        "started_at": at,
        "prompt": "",
        "totals": {},
        "cases": {"a": {"name": "A", "tries": [PASSED]}},
        **stamp,
    }
    asyncio.run(runs.store.save(doc))
    return stamp


def _look(runs):
    """What the watcher does after a commit: the code part of drift, knowledge, the card."""
    asyncio.run(drift.check_code(runs.targets, "bot"))
    asyncio.run(knowledge.refresh(runs, "bot"))
    asyncio.run(impact.refresh(runs, "bot"))


def _code_files(runs):
    found = drift.read(runs.targets, "bot")
    return [f for c in found["changes"] if c["kind"] == "code_changed" for f in c["files"]]


def test_old_documents_are_folded_in_once_and_read_as_before(app) -> None:
    targets = app.targets
    code = targets.code("bot")
    head = code.commit()
    files = code.hashes()
    old_code = {"commit": head, "files": files, "watch": code.watch()}
    write_doc(targets, "bot", FINGERPRINT_DOC, {"taken_at": 1, "tools": {}, "code": old_code})
    write_doc(
        targets,
        "bot",
        "files.json",
        {"version": 2, **old_code, "clean": True, "learned_at": 5.0, "slice": ["lib/b.py"]},
    )
    write_doc(
        targets,
        "bot",
        "knowledge.json",
        {"checked": "abc", "detected": "abd", "backlog_since": "abb", "links": {}, "stale": {}},
    )
    tested = {**old_code, "code": "c1", "at": 3.0}
    write_doc(targets, "bot", "tested.json", {"tested": tested, "runs": {"c1": tested}})
    assert read_doc(targets, "bot", state.DOC) is None

    marks = state.read(targets, "bot")
    assert marks["learned"]["hashes"] == files and marks["learned"]["commit"] == head
    assert marks["learned"]["rules"] == code.watch() and marks["learned"]["clean"] is True
    assert marks["refreshed"] == {
        "commit": "abc",
        "detected": "abd",
        "backlog_since": "abb",
        "at": None,
    }
    assert marks["tested"]["code"] == "c1" and marks["tested"]["runs"]["c1"]["hashes"] == files

    # Each module reads its fields as it did from its own document.
    memory = filememory.load(targets, "bot")
    assert memory["files"] == files and memory["commit"] == head and memory["slice"] == ["lib/b.py"]
    assert filememory.unmoved(memory, code)
    known = knowledge.load(targets, "bot")
    assert (known["checked"], known["detected"], known["backlog_since"]) == ("abc", "abd", "abb")

    # Nothing is deleted, and nothing writes those fields to the old documents again.
    filememory.keep_slice(targets, "bot", ["tools/a.py"])
    kept = read_doc(targets, "bot", "files.json")
    assert kept["slice"] == ["tools/a.py"] and "files" not in kept and "commit" not in kept
    assert filememory.load(targets, "bot")["files"] == files
    assert read_doc(targets, "bot", "tested.json")["tested"]["code"] == "c1"


def test_a_target_with_nothing_kept_starts_empty(app) -> None:
    marks = state.read(app.targets, "bot")
    assert [marks[m] for m in state.MARKS] == [None, None, None]
    assert filememory.load(app.targets, "bot") == {}
    assert filememory.changes({}, {"a.py": "1"}).first


def test_one_change_names_the_same_files_in_every_signal(app) -> None:
    _learned(app)
    _full_run(app, 1)
    _look(app)
    found = health.changes(app.targets, app, "bot")
    assert found["files"] == [] and found["file_count"] == 0

    _commit(app.repo, {"lib/b.py": "def run(): return 2\n"})
    _look(app)
    found = health.changes(app.targets, app, "bot")
    assert found["files"] == ["lib/b.py"] and found["file_count"] == 1
    for mark in ("learned", "refreshed", "tested"):
        assert found["since"][mark]["files"] == ["lib/b.py"], mark
    assert _code_files(app) == ["lib/b.py"]
    card = impact.card(app.targets, "bot", app.all("bot"), {"main": []})
    assert card is None or card["files"] == ["lib/b.py"]
    whole = health.for_target(app.targets, app, "bot", "7d")
    assert whole["changes"] == found
    assert whole["code_change"]["files"] == ["lib/b.py"]
    assert (
        [t["case"] for t in found["tests"]]
        == ["a"]
        == [t["case"] for t in whole["code_change"]["tests"]]
    )
    # The claim that rests on tools/a.py, which imports lib/b.py, is marked.
    assert found["claims"] == 1


def test_a_refresh_clears_changed_since_learned_for_what_it_covered(app) -> None:
    _learned(app)
    _commit(app.repo, {"lib/b.py": "def run(): return 2\n"})
    _look(app)
    head = app.targets.code("bot").commit()
    assert _code_files(app) == ["lib/b.py"]
    before = state.read(app.targets, "bot")["refreshed"]["commit"]
    assert before != head

    app.answer = json.dumps(
        {
            "claims": [
                {
                    "id": "claim:direct",
                    "verdict": "rewrite",
                    "text": "It refunds twice.",
                    "evidence": [{"kind": "code", "ref": "lib/b.py (run)"}],
                }
            ]
        }
    )
    assert asyncio.run(knowledge.refresh(app, "bot", asked=True)) == "updated"
    marks = state.read(app.targets, "bot")
    assert marks["refreshed"]["commit"] == head
    # No longer "changed since learned": the drift, the page's signal and the learn all agree.
    assert _code_files(app) == []
    found = health.changes(app.targets, app, "bot")
    assert found["since"]["learned"]["files"] == [] and found["since"]["refreshed"]["files"] == []
    code = app.targets.code("bot")
    assert not filememory.changes(filememory.load(app.targets, "bot"), code.hashes())


def test_a_failed_refresh_leaves_changed_since_learned(app) -> None:
    _learned(app)
    _commit(app.repo, {"lib/b.py": "def run(): return 2\n"})
    _look(app)
    app.answer = "no json"
    assert asyncio.run(knowledge.refresh(app, "bot", asked=True)) == "failed"
    assert _code_files(app) == ["lib/b.py"]


def test_a_full_run_moves_tested(app) -> None:
    _learned(app)
    _full_run(app, 1)
    _commit(app.repo, {"lib/b.py": "def run(): return 2\n"})
    _look(app)
    assert health.changes(app.targets, app, "bot")["since"]["tested"]["files"] == ["lib/b.py"]

    second = _full_run(app, 2)
    _look(app)
    marks = state.read(app.targets, "bot")
    assert marks["tested"]["code"] == second["code"]
    assert marks["tested"]["commit"] == app.targets.code("bot").commit()
    found = health.changes(app.targets, app, "bot")
    assert found["since"]["tested"]["files"] == [] and found["tests"] == []
    # Still changed since learned: a run is not a learn.
    assert found["since"]["learned"]["files"] == ["lib/b.py"]


def test_between_compares_hashes_and_trusts_a_clean_commit() -> None:
    mark = {"commit": "c", "hashes": {"a": "1", "b": "2"}, "rules": "r", "clean": True}
    assert not state.between(mark, {**mark, "hashes": {"a": "9"}, "clean": True})
    moved = state.between(mark, {"commit": "d", "hashes": {"a": "9", "c": "3"}, "rules": "r"})
    assert (moved.changed, moved.added, moved.removed) == (["a"], ["c"], ["b"])
    assert moved.files == ["a", "b", "c"]
    # Other rules: only what both watched compares.
    other = state.between(mark, {"commit": "d", "hashes": {"a": "9", "c": "3"}, "rules": "s"})
    assert other.files == ["a"]
    assert state.between(None, {"hashes": {"a": "1"}}).first
    assert state.between(mark, None) is None


def test_the_health_and_understanding_pages_carry_the_same_changes(tmp_path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, {"lib/b.py": "def run(): return 1\n"})
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        said = client.get("/v1/targets/bot/health").json()["changes"]
        understood = client.get("/v1/targets/bot/understanding").json()["changes"]
    # Health starts a check in the background, which may take the first look meanwhile.
    for found in (said, understood):
        assert set(found) == {"files", "file_count", "areas", "claims", "flagged", "tests", "since"}
        assert found["files"] == [] and set(found["since"]) == {"learned", "refreshed", "tested"}
