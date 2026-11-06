"""A test set's pass marks, a test's file edited by hand, and a failed test's finding."""

import asyncio
import contextlib
import json

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from evalution import adapters, cli
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
from evalution.runs import collect
from evalution.runs.runner import Runs
from evalution.storage import Database
from evalution.suite.cases import CaseError, is_benchmark, meaning_changes
from evalution.suite.store import (
    case_text,
    export_sets,
    import_sets,
    own_sets,
    put_bundle,
    put_case,
    save_set,
    write_case_text,
)
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
    import_sets(_docs(tmp_path), tmp_path / "sets")
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as made:
        yield made


def _docs(tmp_path):
    return Database.open(load(tmp_path / "evalution.yaml").data_dir).docs()


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
    # Exported, the set's file says so, and its tests come out as they went in.
    out = tmp_path / "out"
    assert export_sets(_docs(tmp_path), out) == ["bot/main"]
    written = json.loads((out / "bot" / "main" / "set.json").read_text())
    assert "pass_scores" not in written and written["pass_marks"]["reply"] == 0.9
    case = "greets.case.json"
    assert (out / "bot/main" / case).read_text() == (tmp_path / "sets/bot/main" / case).read_text()
    # The same files over the API, for a run played where the app runs; they go back in whole.
    url_export = "/v1/evals/sets/export"
    bundle = client.get(url_export, params={"target": "bot", "set": "main"}).json()
    assert (
        bundle["target"] == "bot"
        and bundle["sets"]["main"][case] == (out / "bot/main" / case).read_text()
    )
    assert json.loads(bundle["sets"]["main"]["set.json"]) == written
    elsewhere = Database.open(tmp_path / "elsewhere").docs()
    put_bundle(
        elsewhere, "bot", "main", bundle["sets"]["main"] | {"fixtures/a.bin": {"base64": "AP8="}}
    )
    assert case_text(elsewhere, "bot", "main", "greets") == (out / "bot/main" / case).read_text()
    with pytest.raises(CaseError):
        put_bundle(elsewhere, "bot", "main", {"set.json": "{}", "../out.txt": "x"})
    assert client.get(url_export, params={"target": "bot", "set": "nope"}).status_code == 404
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
def test_a_failed_run_records_what_kind_of_failure(
    tmp_path, monkeypatch, capsys, where, error, reason
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
    monkeypatch.setitem(adapters._built, "bot", _LosesTheApp(where, error))
    # As in CI: a database with no tests yet, and the repo's folder imported as the run starts.
    config, kept = str(tmp_path / "evalution.yaml"), tmp_path / ".evalution" / "baseline.json"
    play = ["run", "--target", "bot", "--set", "main", "--tries", "1", "--yes", "--sets"]
    assert cli.main(["--config", config, *play, "--save", str(kept), "--env", "ci"]) == 1
    assert "imported 1 set(s)" in capsys.readouterr().out
    [run] = RunStore(_docs(tmp_path)).all()
    assert json.loads(kept.read_text())["run"] == run["run"]
    assert run["environment"] == {"where": "local", "name": "ci"}
    # Against that baseline, the comparison is also written as markdown, for a pull request.
    said = tmp_path / "summary.md"
    assert (
        cli.main(["--config", config, *play, "--against", str(kept), "--summary", str(said)]) == 0
    )
    assert said.read_text().startswith("### ")
    # Only a test some run played can be accepted into it, and then nothing is written.
    before = kept.read_text()
    for test, error in (("nope", "no test nope in bot/main"), ("greets", "no run of greets")):
        assert cli.main(["--config", config, "accept", test, "--baseline", str(kept)]) == 2
        assert error in capsys.readouterr().err
    assert kept.read_text() == before
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


_ALL = tuple(f"msg{n}" for n in range(6))


def _quiet_app(tmp_path, monkeypatch) -> None:
    """A target ``bot`` whose app sends no traces: a try waits for them only briefly."""
    monkeypatch.setattr(collect, "TRACE_WAIT", 0.2)
    monkeypatch.setattr(collect, "_POLL", 0.05)
    monkeypatch.setattr("evalution.runs.runner.NO_TRACES_WAIT", 0.1)
    bot = {"adapter": "http", "http": {"send": {"url": "x"}}}
    config = {"targets": {"bot": bot}, "traces": {"settle_seconds": 0.1}}
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump(config))


@pytest.mark.parametrize(
    ("tries", "answers", "usd", "asked", "status"),
    [
        # Three hangs in a row stop it.
        (1, (), 0.0, 3, "failed"),
        # A count asked for is played exactly, whatever the tries say.
        (1, ("msg2", "msg5"), 0.0, 6, "done"),
        # None asked for: each test's own three, which agree, so no more.
        (None, _ALL, 0.0, 18, "done"),
        # The first test's tries cost $8 against an estimate of $0.40: the cap, three times the
        # low estimate of about $7.41 while that is more than the tries played, stops it on its
        # third, far under three times the high end.
        (None, _ALL, 8.0, 3, "stopped"),
    ],
)
async def test_a_run_plays_its_tries_and_stops_when_it_must(
    tmp_path, monkeypatch, tries, answers, usd, asked, status
) -> None:
    _quiet_app(tmp_path, monkeypatch)
    folder = tmp_path / "sets" / "bot" / "main"
    folder.mkdir(parents=True)
    (folder / "set.json").write_text(json.dumps({"name": "Main"}))
    for n in range(6):
        (folder / f"t{n}.case.json").write_text(
            json.dumps({"name": f"t{n}", "turns": [{"message": f"msg{n}"}]})
        )
    config = load(tmp_path / "evalution.yaml")
    import_sets(_docs(tmp_path), tmp_path / "sets")
    target = _Hangs(answers)
    monkeypatch.setitem(adapters._built, "bot", target)
    play = Runs._try

    async def spending(self, *args):
        # What the traces would say the app spent on a try of the first test.
        attempt, played = await play(self, *args)
        return {**attempt, "usd": usd if args[7].case == "t0" else 0.0}, played

    monkeypatch.setattr(Runs, "_try", spending)
    runs = Runs(Targets(config), None)
    estimate = runs.estimate("bot", "main", None, tries)
    assert (estimate["tries"], estimate["tries_high"]) == ((6, 6) if tries else (18, 120))
    assert estimate["usd_high"] == estimate["usd"] if tries else estimate["usd_high"] > 6
    made = await runs.start(estimate["token"], env="staging")
    assert runs.load(made["run"])["status"] == "running", "stored before start answers"
    assert runs.load(made["run"])["environment"] == {"where": "local", "name": "staging"}
    await runs.wait()
    run = runs.load(made["run"])
    assert (run["status"], target.asked) == (status, asked)
    played = [c for c in run["cases"].values() if c["tries"]]
    assert all(c["planned"] == len(c["tries"]) for c in played), "planned ends at what it played"
    if status == "failed":
        assert "sleep" in run["stop_reason"]
    if status == "stopped":
        # Nothing judged from the tests it didn't reach, and none of its tries failed for it.
        reasons = [t["reason"] for t in summary(run)["tests"]]
        assert reasons == [None] + ["spend cap"] * 5
        assert run["totals"]["usd"] < 3 * estimate["usd_high"]
        errors = {t["error"] for c in played for t in c["tries"]}
        assert errors <= {None, "No traces arrived"}, "the app sends none"


async def test_stop_reaches_a_run_another_process_is_playing(tmp_path, monkeypatch) -> None:
    _quiet_app(tmp_path, monkeypatch)
    folder = tmp_path / "sets" / "bot" / "main"
    folder.mkdir(parents=True)
    (folder / "set.json").write_text(json.dumps({"name": "Main"}))
    (folder / "t.case.json").write_text(json.dumps({"name": "t", "turns": [{"message": "msg"}]}))
    config = load(tmp_path / "evalution.yaml")
    import_sets(_docs(tmp_path), tmp_path / "sets")
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

    def stored() -> str:
        return case_text(_docs(tmp_path), "bot", "main", "greets")

    assert stored() == typed + "\n"
    # Control: only a name changes, so there's nothing to review.
    renamed = '{"name": "Says hello", "about": "x", "turns": [{"message": "hello"}]}'
    assert client.put(url, content=renamed).status_code == 200
    assert client.put(url, content=typed).status_code == 200
    broken = client.put(url, content='{"name": "x"')
    assert broken.status_code == 400 and "not JSON" in broken.json()["detail"]
    bad = client.put(url, content='{"name": "x", "turns": []}')
    assert bad.status_code == 400
    assert bad.json()["detail"] == "bot/main/greets.case.json: turns: must be a non-empty list"
    assert stored() == typed + "\n"
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
    docs = _docs(tmp_path)
    typed = '{"name": "Greets", "turns": [{"message": "hello there"}]}'
    token = client.put(url, content=typed).json()["token"]
    before = case_text(docs, "bot", "main", "greets")
    # Someone else's edit comes in between: what was reviewed isn't what would be replaced.
    write_case_text(docs, "bot", "main", "greets", before.replace('"Greets"', '"Greets too"'))
    stale = client.put(url, content=typed, headers={"X-Reviewed": token})
    assert stale.status_code == 409 and stale.json()["token"] != token
    assert "Greets too" in case_text(docs, "bot", "main", "greets")
    # Control: reviewed again over what's there now, it saves.
    fresh = client.put(url, content=typed, headers={"X-Reviewed": stale.json()["token"]})
    assert fresh.status_code == 200


def test_a_benchmark_stays_out_of_the_apps_own_numbers(client, tmp_path) -> None:
    docs = _docs(tmp_path)
    save_set(docs, "bot", "longmemeval-quick", {"name": "LongMemEval"})
    put_case(docs, "bot", "longmemeval-quick", "q", {"name": "q", "turns": [{"message": "hi"}]})
    assert is_benchmark("longmemeval-quick") and not is_benchmark("main")
    assert own_sets(docs, "bot") == ["main"]
    seen = client.get("/v1/targets/bot/health", params={"period": "7d"})
    assert seen.status_code == 200 and "LongMemEval" not in seen.text


def test_benchmarks_list_their_sizes_and_stay_empty_until_played(client, tmp_path) -> None:
    found = client.get("/v1/benchmarks", params={"target": "bot"}).json()
    assert [entry["size"] for entry in found["sizes"]] == ["quick", "oracle", "middle", "s", "m"]
    assert found["sizes"][0]["questions"] is None and found["runs"] == []
    refused = client.post("/v1/benchmarks/install", json={"target": "bot", "size": "huge"})
    assert refused.status_code == 400 and "huge" in refused.text
