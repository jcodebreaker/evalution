"""A test set's pass marks, a test's file edited by hand, and a failed test's finding."""

import asyncio
import contextlib
import json

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from evalution import adapters
from evalution.adapters.base import (
    AppError,
    Capabilities,
    Isolation,
    Session,
    Target,
    TargetGone,
    Turn,
)
from evalution.api.app import create_app
from evalution.config import load
from evalution.core.evidence import Failure
from evalution.results.migrate import upcast
from evalution.results.store import RunStore
from evalution.results.views import case_detail, summary
from evalution.runs.runner import Runs
from evalution.suite.cases import is_benchmark, meaning_changes, own_sets
from evalution.targets import Targets


@pytest.fixture
def client(tmp_path):
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    main = tmp_path / "sets" / "bot" / "main"
    main.mkdir(parents=True)
    # Written before the rename: the old key still reads.
    (main / "set.json").write_text(
        json.dumps({"name": "Main", "pass_scores": {"tools": 0.5, "reply": 0.6}})
    )
    (main / "greets.case.json").write_text(
        json.dumps({"name": "Greets", "turns": [{"message": "hi"}]})
    )
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as made:
        yield made


def test_pass_marks_round_trip_and_read_the_old_key(client, tmp_path) -> None:
    sets = client.get("/v1/evals/sets").json()["data"]
    assert sets[0]["pass_marks"] == {"tools": 0.5, "reply": 0.6}
    url = "/v1/evals/sets/main/pass-marks"
    saved = client.put(url, json={"reply": 0.9})
    assert saved.json() == {
        "target": "bot",
        "set": "main",
        "pass_marks": {"tools": 0.5, "reply": 0.9},
    }
    written = json.loads((tmp_path / "sets" / "bot" / "main" / "set.json").read_text())
    assert "pass_scores" not in written and written["pass_marks"]["reply"] == 0.9
    assert client.put(url, json={"tools": 2}).status_code == 400
    assert client.put(url, json={"speed": 1}).status_code == 400
    assert client.get("/v1/evals/sets").json()["data"][0]["pass_marks"]["reply"] == 0.9
    # A run stored before the rename shows its marks under the new name.
    old = {"run": "r", "status": "done", "cases": {}, "pass_scores": {"tools": 0.1, "reply": 0.2}}
    assert summary(upcast(old))["pass_marks"] == {"tools": 0.1, "reply": 0.2}


FOUND = {"summary": "Never searched", "cause": "No tool", "evidence": [], "fix": "Add a line"}
TRY = {"tools": 0.0, "reply": 1.0, "seconds": 1.0, "tokens": 10, "usd": 0.1, "error": None}


def _run(analysis):
    return {
        "run": "r1",
        "target": "bot",
        "set": "main",
        "started_at": 1.0,
        "status": "done",
        "cases": {
            "greets": {
                "name": "Greets",
                "turns": [{"message": "hi"}],
                "expected": {},
                "reply_rubric": [],
                "tries": [
                    {"n": 1, "passed": True, **TRY, "analysis": None},
                    {"n": 2, "passed": False, **TRY, "error": "boom", "analysis": analysis},
                ],
            }
        },
    }


def test_a_failed_test_shows_its_finding(client, tmp_path) -> None:
    run = _run({**FOUND, "kind": "assistant"})
    RunStore(Targets(load(tmp_path / "evalution.yaml")).docs).write(run)
    why = {
        "summary": {"text": "Never searched", "refs": [], "quotes": [], "verified": False},
        "cause": {"text": "No tool", "refs": [], "quotes": [], "verified": False},
        "fix": "Add a line",
        "suspected": "app",
    }
    assert client.get("/v1/evals/runs/r1/cases/greets").json()["finding"] == why
    assert client.get("/v1/evals/sets/main/cases/greets").json()["finding"] == why
    assert case_detail(upcast(_run(None)), "greets")["finding"] is None


async def test_an_analysis_that_cannot_finish_says_so(tmp_path) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    config = load(tmp_path / "evalution.yaml")
    runs = Runs(Targets(config), None)
    run = upcast(_run(None))
    await runs._analyze(run)
    assert summary(run)["analysis_error"].startswith("No model is set up yet")
    assert not run["analyzing"]


class _LosesTheApp(Target):
    """A target that fails as *where* says: on opening a session, or in the middle of a try."""

    name = "bot"
    capabilities = Capabilities(Isolation.NONE, "")

    def __init__(self, where: str, error: Exception) -> None:
        self.where, self.error = where, error

    async def open(self, run: str) -> Session:
        if self.where == "open":
            raise self.error
        return Session(run, "session.id", run)

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> None:
        raise self.error

    async def close(self, session: Session) -> None:
        return None


@pytest.mark.parametrize(
    ("where", "error", "reason"),
    [
        ("send", TargetGone("gone"), "the app wasn't ready"),
        ("open", AppError("it answered 503"), "the app wasn't ready"),
        ("open", httpx.ConnectError("refused"), "the app wasn't ready"),
        ("open", KeyError("no such field"), "it stopped early"),
    ],
)
async def test_a_failed_run_records_what_kind_of_failure(
    tmp_path, monkeypatch, where, error, reason
) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    folder = tmp_path / "sets" / "bot" / "main"
    folder.mkdir(parents=True)
    (folder / "set.json").write_text(json.dumps({"name": "Main"}))
    for stem in ("greets", "thanks"):
        (folder / f"{stem}.case.json").write_text(
            json.dumps({"name": stem, "turns": [{"message": "hello"}]})
        )
    config = load(tmp_path / "evalution.yaml")
    monkeypatch.setitem(adapters._built, "bot", _LosesTheApp(where, error))
    runs = Runs(Targets(config), None)
    made = await runs.start(runs.estimate("bot", "main", None, 1)["token"])
    await runs.wait()
    run = runs.load(made["run"])
    assert run["status"] == "failed"
    assert run["failure"] == ("app_not_ready" if "ready" in reason else "other")
    # The test the run never got to says why.
    assert summary(run)["tests"][-1]["reason"] == reason


class _Hangs(_LosesTheApp):
    """Runs out of time on every message, except the ones it answers."""

    def __init__(self, answers: tuple[str, ...] = ()) -> None:
        super().__init__("send", TargetGone("unused"))
        self.answers = answers
        self.asked = 0

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
        if not message.startswith("msg"):  # the connection check before the run
            return Turn("ok", 0.0, 0.0)
        self.asked += 1
        if message in self.answers:
            return Turn("ok", 0.0, 0.0)
        return Turn("", 0.0, 0.0, error="out of time", failure=Failure.TIMED_OUT, heard=True)


@pytest.mark.parametrize(
    ("answers", "asked", "status"),
    [((), 3, "failed"), (("msg2", "msg5"), 6, "done")],
)
async def test_a_run_stops_when_try_after_try_hangs(
    tmp_path, monkeypatch, answers, asked, status
) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    folder = tmp_path / "sets" / "bot" / "main"
    folder.mkdir(parents=True)
    (folder / "set.json").write_text(json.dumps({"name": "Main"}))
    for n in range(6):
        (folder / f"t{n}.case.json").write_text(
            json.dumps({"name": f"t{n}", "turns": [{"message": f"msg{n}"}]})
        )
    config = load(tmp_path / "evalution.yaml")
    target = _Hangs(answers)
    monkeypatch.setitem(adapters._built, "bot", target)
    runs = Runs(Targets(config), None)
    made = await runs.start(runs.estimate("bot", "main", None, 1)["token"])
    await runs.wait()
    run = runs.load(made["run"])
    assert (run["status"], target.asked) == (status, asked)
    if status == "failed":
        assert "sleep" in run["stop_reason"]


async def test_stop_reaches_a_run_another_process_is_playing(tmp_path, monkeypatch) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    folder = tmp_path / "sets" / "bot" / "main"
    folder.mkdir(parents=True)
    (folder / "set.json").write_text(json.dumps({"name": "Main"}))
    (folder / "t.case.json").write_text(json.dumps({"name": "t", "turns": [{"message": "msg"}]}))
    config = load(tmp_path / "evalution.yaml")
    playing = asyncio.Event()

    class Slow(_LosesTheApp):
        async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
            if message != "msg":
                return Turn("ok", 0.0, 0.0)
            playing.set()
            await asyncio.sleep(60)
            return Turn("late", 0.0, 0.0)

    monkeypatch.setitem(adapters._built, "bot", Slow("send", TargetGone("unused")))
    monkeypatch.setattr("evalution.runs.runner.STOP_POLL_SECONDS", 0.01)
    runs = Runs(Targets(config), None)
    made = await runs.start(runs.estimate("bot", "main", None, 1)["token"])
    await asyncio.wait_for(playing.wait(), 5)

    # The page's server: the same files, and not the process that holds the run.
    page = Runs(Targets(config), None)
    page._orphans_marked = True
    await page.stop(made["run"])
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(runs.wait(), 5)
    run = page.load(made["run"])
    assert (run["status"], run["stop_reason"]) == ("stopped", "Stopped by the admin")


def test_a_test_file_edited_by_hand_is_checked_before_it_is_saved(client, tmp_path) -> None:
    url = "/v1/evals/sets/main/cases/greets/file"
    shown = client.get(url)
    assert shown.status_code == 200 and json.loads(shown.text)["name"] == "Greets"
    typed = '{\n  "name": "Greets back",\n  "turns": [{"message": "hello"}]\n}'
    # Changing what it checks is reviewed first, and the review covers exactly that text.
    asked = client.put(url, content=typed)
    assert asked.status_code == 409
    assert [c["what"] for c in asked.json()["changes"]] == ["What the person sends"]
    assert asked.json()["changes"][0]["after"] == ["hello"]
    other = client.put(url, content=typed + " ", headers={"X-Reviewed": asked.json()["token"]})
    assert other.status_code == 409
    saved = client.put(url, content=typed, headers={"X-Reviewed": asked.json()["token"]})
    assert saved.json() == {
        "case": "greets",
        "file": {"name": "Greets back", "turns": [{"message": "hello"}]},
    }
    path = tmp_path / "sets" / "bot" / "main" / "greets.case.json"
    assert path.read_text() == typed + "\n"
    # Control: only a name changes, so there's nothing to review.
    renamed = '{"name": "Says hello", "about": "x", "turns": [{"message": "hello"}]}'
    assert client.put(url, content=renamed).status_code == 200
    assert client.put(url, content=typed).status_code == 200
    broken = client.put(url, content='{"name": "x"')
    assert broken.status_code == 400 and "not JSON" in broken.json()["detail"]
    bad = client.put(url, content='{"name": "x", "turns": []}')
    assert bad.status_code == 400
    assert bad.json()["detail"] == "bot/main/greets.case.json: turns: must be a non-empty list"
    assert path.read_text() == typed + "\n"
    assert client.get("/v1/evals/sets/main/cases/nope/file").status_code == 404
    assert client.put("/v1/evals/sets/main/cases/nope/file", content="{}").status_code == 404


@pytest.mark.parametrize(
    ("change", "what"),
    [
        ({"must_hold": True}, "Must hold"),
        ({"limits": {"seconds": 5}}, "Time limit"),
        ({"turns": [{"message": "a", "blocked": True}]}, "A turn expected to be blocked"),
        ({"turns": [{"message": "a", "fresh": True}]}, "A turn that must be new each try"),
        ({"turns": [{"message": "a", "files": ["f.txt"]}]}, "Files sent"),
    ],
)
def test_what_a_test_checks_includes_how_it_runs(change, what) -> None:
    base = {"name": "t", "turns": [{"message": "a"}], "reply": {"contains": ["x"]}}
    assert [c["what"] for c in meaning_changes(base, {**base, **change})] == [what]
    assert [c["what"] for c in meaning_changes({**base, **change}, base)] == [what]
    # Control: the same thing said another way, or a name and how many tries, aren't a change.
    same = {**base, "name": "u", "about": "y", "limits": {"tries": 7}, "must_hold": False}
    assert meaning_changes(base, same) == []
    assert meaning_changes(base, {**base, "turns": [{"message": "a", "files": []}]}) == []


def test_a_review_covers_the_file_it_was_made_against(client, tmp_path) -> None:
    url = "/v1/evals/sets/main/cases/greets/file"
    path = tmp_path / "sets" / "bot" / "main" / "greets.case.json"
    typed = '{"name": "Greets", "turns": [{"message": "hello there"}]}'
    token = client.put(url, content=typed).json()["token"]
    before = path.read_text()
    # Someone else's edit comes in between: what was reviewed isn't what would be replaced.
    path.write_text(before.replace('"Greets"', '"Greets too"'))
    stale = client.put(url, content=typed, headers={"X-Reviewed": token})
    assert stale.status_code == 409 and stale.json()["token"] != token
    assert "Greets too" in path.read_text()
    # Control: reviewed again over what's there now, it saves.
    fresh = client.put(url, content=typed, headers={"X-Reviewed": stale.json()["token"]})
    assert fresh.status_code == 200


def test_a_benchmark_stays_out_of_the_apps_own_numbers(client, tmp_path) -> None:
    other = tmp_path / "sets" / "bot" / "longmemeval-quick"
    other.mkdir()
    (other / "set.json").write_text(json.dumps({"name": "LongMemEval"}))
    (other / "q.case.json").write_text(json.dumps({"name": "q", "turns": [{"message": "hi"}]}))
    assert is_benchmark("longmemeval-quick") and not is_benchmark("main")
    assert own_sets(tmp_path / "sets" / "bot") == ["main"]
    seen = client.get("/v1/targets/bot/health", params={"period": "7d"})
    assert seen.status_code == 200 and "LongMemEval" not in seen.text


def test_benchmarks_list_their_sizes_and_stay_empty_until_played(client, tmp_path) -> None:
    found = client.get("/v1/benchmarks", params={"target": "bot"}).json()
    assert [entry["size"] for entry in found["sizes"]] == ["quick", "oracle", "middle", "s", "m"]
    assert found["sizes"][0]["questions"] is None and found["runs"] == []
    refused = client.post("/v1/benchmarks/install", json={"target": "bot", "size": "huge"})
    assert refused.status_code == 400 and "huge" in refused.text
