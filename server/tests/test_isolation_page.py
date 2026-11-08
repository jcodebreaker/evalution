"""Keeping an isolation plan current on commit, and finding it from the page (ADR 0009, phase 6).
Offline: a temp git repo, and a fake in place of finding and rehearsing on a copy of the app."""

import asyncio
import subprocess
import threading
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.accounts.context import LOCAL
from evalution.api.app import create_app
from evalution.config import load
from evalution.isolation.plan import Plan, PlannedStore, PlanStore, hashes
from evalution.project import impact, isolating
from evalution.runs.runner import Runs
from evalution.targets import Targets


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


@pytest.fixture
def config(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, {"db/utils.py": "URL = 'postgres://db'\n", "other.py": "x = 1\n"})
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    return load(tmp_path / "evalution.yaml")


def _repo(config):
    return config.targets["bot"].code.path


def _proven(config, status="proven") -> Plan:
    plan = Plan(
        target="bot",
        stores=[
            PlannedStore(
                "postgres-app",
                "postgres",
                "eval-bot-postgres-1:app",
                moved_by=["DATABASE_URL"],
                strategy="relocate",
                evidence=["db/utils.py:1"],
                seen=["holds the canary"],
            )
        ],
        exclude=["app.phoenix.spans"],
        status=status,
        report="Rehearsed on eval-bot in 9s:\nProven.",
        evidence_hashes=hashes(_repo(config), ["db/utils.py:1"]),
    )
    PlanStore(config.data_dir).save(plan)
    return plan


def _load(config) -> Plan:
    found = PlanStore(config.data_dir).load("bot")
    assert found is not None
    return found


# Stale on commit


def test_a_commit_under_the_evidence_marks_the_plan_stale_and_an_edit_does_not(config) -> None:
    _proven(config)
    targets = Targets(config)

    (_repo(config) / "db/utils.py").write_text("URL = 'postgres://other'\n")
    assert isolating.mark_stale(targets, "bot") == []
    assert _load(config).status == "proven"

    (_repo(config) / "other.py").write_text("x = 2\n")
    _git(_repo(config), "commit", "-qm", "other", "--", "other.py")
    assert isolating.mark_stale(targets, "bot") == []

    _commit(_repo(config), {})
    assert isolating.mark_stale(targets, "bot") == ["db/utils.py"]
    plan = _load(config)
    assert plan.status == "stale"
    assert plan.report.endswith("Out of date: a commit changed db/utils.py.")
    assert len(PlanStore(config.data_dir).history("bot")) == 2
    # Marked once: a second look saves nothing.
    assert isolating.mark_stale(targets, "bot") == []
    assert len(PlanStore(config.data_dir).history("bot")) == 2
    assert isolating.brief(targets, "bot") == {"status": "stale", "stale_files": ["db/utils.py"]}


def test_only_a_proven_plan_goes_stale(config) -> None:
    _proven(config, status="failed")
    _commit(_repo(config), {"db/utils.py": "URL = 'postgres://moved'\n"})
    targets = Targets(config)
    assert isolating.mark_stale(targets, "bot") == []
    assert isolating.brief(targets, "bot") is None


def test_the_commit_hook_check_marks_it(config, monkeypatch) -> None:
    """The same background check a hook's ``changed`` starts for what evalution knows."""
    _proven(config)
    _commit(_repo(config), {"db/utils.py": "URL = 'postgres://moved'\n"})
    monkeypatch.setattr(impact, "SETTLE", 0.01)
    runs = Runs(Targets(config), None)

    async def go():
        watcher = impact.Watcher(runs, None)
        watcher.changed("bot")
        for _ in range(100):
            await asyncio.sleep(0.02)
            if _load(config).status == "stale":
                break
        await watcher.close()

    asyncio.run(go())
    assert _load(config).status == "stale"


# The page's routes


class Fake:
    """Finding and rehearsing, as the job sees it: stages, then a saved plan or a failure."""

    def __init__(self, config, outcome="proven"):
        self.config = config
        self.outcome = outcome
        self.gate = threading.Event()
        self.calls = 0

    @property
    def go(self) -> bool:
        return self.gate.is_set()

    @go.setter
    def go(self, value: bool) -> None:
        if value:
            self.gate.set()

    async def __call__(self, config, target, stage):
        self.calls += 1
        stage("find")
        await asyncio.to_thread(self.gate.wait, 10)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        stage("plan")
        stage("rehearse")
        plan = _proven(self.config, status=self.outcome)
        if self.outcome == "failed":
            plan.leaks = ["eval-bot-app-1:/data/memory.idx"]
            PlanStore(config.data_dir).save(plan)
        return plan


@pytest.fixture
def fake(config, monkeypatch):
    made = Fake(config)
    monkeypatch.setattr(isolating, "isolate", made)
    return made


@pytest.fixture
def client(config, fake):
    with TestClient(create_app(config)) as made:
        yield made


def _until_ended(client) -> dict:
    for _ in range(200):
        found = client.get("/v1/targets/bot/isolation").json()
        if found["job"] and found["job"]["state"] != "running":
            return found
        time.sleep(0.02)
    raise AssertionError("the job never ended")


def test_nothing_found_yet(client) -> None:
    found = client.get("/v1/targets/bot/isolation").json()
    assert found["status"] == "none" and found["plan"] is None and found["job"] is None
    assert found["can_run"] and not found["can_undo"] and found["history"] == []


def test_run_needs_a_confirm_since_it_sends_two_messages(client, fake) -> None:
    refused = client.post("/v1/targets/bot/isolation/run", json={})
    assert refused.status_code == 400 and "two messages" in refused.json()["detail"]
    assert fake.calls == 0


def test_run_shows_its_stages_holds_runs_and_saves_a_proven_plan(client, fake) -> None:
    started = client.post("/v1/targets/bot/isolation/run", json={"confirm": True})
    assert started.status_code == 202, started.text
    job = started.json()["job"]
    assert job["state"] == "running" and job["steps"][0]["key"] == "start"

    ws = client.app.state.workspaces.get(LOCAL)
    assert "bot" in ws.probes.busy  # no run, and no test message, starts under it
    again = client.post("/v1/targets/bot/isolation/run", json={"confirm": True})
    assert again.status_code == 409

    fake.go = True
    found = _until_ended(client)
    assert found["status"] == "proven" and found["version"] == 1
    assert [s["state"] for s in found["job"]["steps"]] == ["done"] * 5
    assert found["plan"]["stores"][0]["moved_by"] == ["DATABASE_URL"]
    assert found["plan"]["exclude"] == ["app.phoenix.spans"]
    assert "bot" not in ws.probes.busy and fake.calls == 1


def test_a_failed_rehearsal_shows_its_leaks(client, fake) -> None:
    fake.outcome, fake.go = "failed", True
    client.post("/v1/targets/bot/isolation/run", json={"confirm": True})
    found = _until_ended(client)
    assert found["status"] == "failed"
    assert found["plan"]["leaks"] == ["eval-bot-app-1:/data/memory.idx"]
    assert found["job"]["steps"][-1] == {"key": "done", "text": "Not proven", "state": "problem"}


def test_a_run_that_cannot_start_says_why(client, fake) -> None:
    fake.outcome, fake.go = LookupError("no compose file in app"), True
    client.post("/v1/targets/bot/isolation/run", json={"confirm": True})
    found = _until_ended(client)
    assert found["job"]["state"] == "failed" and "no compose file" in found["job"]["error"]
    assert found["job"]["steps"][-1]["state"] == "problem"
    assert found["status"] == "none"


def test_not_during_a_test_message(client, fake) -> None:
    ws = client.app.state.workspaces.get(LOCAL)
    ws.probes.busy.add("bot")
    refused = client.post("/v1/targets/bot/isolation/run", json={"confirm": True})
    assert refused.status_code == 409 and fake.calls == 0


def test_undo_goes_back_a_version(client, config) -> None:
    _proven(config)
    _commit(_repo(config), {"db/utils.py": "URL = 'postgres://moved'\n"})
    isolating.mark_stale(Targets(config), "bot")
    found = client.get("/v1/targets/bot/isolation").json()
    assert found["status"] == "stale" and found["stale_files"] == ["db/utils.py"]
    assert [h["status"] for h in found["history"]] == ["proven", "stale"]

    back = client.post("/v1/targets/bot/isolation/undo")
    assert back.status_code == 200 and back.json()["status"] == "proven"
    assert client.post("/v1/targets/bot/isolation/undo").json()["status"] == "none"
    assert client.post("/v1/targets/bot/isolation/undo").status_code == 409


def test_an_unknown_app_is_not_found(client) -> None:
    assert client.get("/v1/targets/nope/isolation").status_code == 404
