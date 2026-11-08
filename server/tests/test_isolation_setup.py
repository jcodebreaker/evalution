"""The test setup evalution's copies of an app play on (ADR 0009): kept per target, changed from
the page, shown live while testing is set up, and honored by the copies. Offline: fakes in place
of copies of the app and of Docker."""

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.accounts.context import LOCAL
from evalution.adapters import base
from evalution.api.app import create_app
from evalution.config import load
from evalution.isolation import isolate as iso
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.isolation.setup import (
    Setup,
    SetupError,
    SetupStore,
    changed,
    default_copies,
    setup_for,
)
from evalution.project import isolating
from evalution.results.record import isolation_of
from evalution.runs import isolated
from evalution.targets import Targets


@pytest.fixture
def config(tmp_path):
    app = tmp_path / "app"
    (app / "db").mkdir(parents=True)
    (app / "db" / "seed.sql").write_text("CREATE TABLE t (x int);\n")
    (app / "notes.txt").write_text("not sql\n")
    (tmp_path / "elsewhere.sql").write_text("SELECT 1;\n")
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "targets": {
                    "bot": {
                        "adapter": "http",
                        "http": {},
                        "code": {"path": "app"},
                        "max_parallel": 5,
                    }
                }
            }
        )
    )
    return load(tmp_path / "evalution.yaml")


def _plan(status: str = "proven", *, postgres: bool = True) -> Plan:
    stores = [
        PlannedStore(
            "volume-artifact-data",
            "volume",
            "volume:artifact-data",
            strategy="relocate",
            seen=["memory_index.json holds the canary"],
        ),
        PlannedStore("dir-uploads", "dir", "/tmp/eval-x/uploads", strategy="snapshot"),
        PlannedStore("hosted-mem", "hosted", "env:MEM_API_KEY", strategy="none"),
    ]
    if postgres:
        stores.insert(
            0,
            PlannedStore(
                "postgres-app", "postgres", "eval-bot-postgres-1:app", strategy="relocate"
            ),
        )
    return Plan(target="bot", stores=stores, status=status, report="Rehearsed on eval-bot in 9s.")


# The store


def test_defaults_until_one_is_saved(config) -> None:
    found = setup_for(config, "bot")
    assert found.view() == {
        "person": {"name": "", "language": "", "timezone": "", "new_every_try": True},
        "start": {"kind": "empty", "file": None},
        "reset": "try",
        "copies": 3,  # max_parallel 5, capped at 3
        "version": 0,
        "updated": None,
    }
    assert [default_copies(n) for n in (0, 1, 2, 3, 16)] == [1, 1, 2, 3, 3]


def test_changes_are_checked(config) -> None:
    folder = config.targets["bot"].code.path
    now = Setup()
    for body, said in [
        ({"copies": 0}, "from 1 to 3"),
        ({"copies": 4}, "from 1 to 3"),
        ({"copies": True}, "whole number"),
        ({"reset": "never"}, "try or test"),
        ({"person": {"name": "x" * 81}}, "at most 80"),
        ({"person": {"name": 3}}, "must be text"),
        ({"person": {"email": "a@b"}}, "no field email"),
        ({"person": {"new_every_try": "yes"}}, "true or false"),
        ({"start": {"kind": "snapshot"}}, "empty or seed"),
        ({"start": {"kind": "seed", "file": "../elsewhere.sql"}}, "inside the app's folder"),
        ({"start": {"kind": "seed", "file": "notes.txt"}}, "a .sql file"),
        ({"start": {"kind": "seed", "file": "missing.sql"}}, "isn't there"),
        ({"start": {"kind": "seed"}}, "name the .sql file"),
        ({"colour": "blue"}, "not part of the test setup: colour"),
    ]:
        with pytest.raises(SetupError, match=said):
            changed(now, body, folder=folder)
    with pytest.raises(SetupError, match="needs a Postgres store"):
        changed(now, {"start": {"kind": "seed", "file": "db/seed.sql"}}, folder=folder, seed=False)
    with pytest.raises(SetupError, match="say where the app's code is"):
        changed(now, {"start": {"kind": "seed", "file": "db/seed.sql"}}, folder=None)

    made = changed(
        now,
        {
            "person": {"name": "  Alex Rivera ", "timezone": "Europe/Lisbon"},
            "start": {"kind": "seed", "file": str(folder / "db" / "seed.sql")},
            "reset": "test",
            "copies": 2,
        },
        folder=folder,
    )
    assert made.person == {
        "name": "Alex Rivera",
        "language": "",
        "timezone": "Europe/Lisbon",
        "new_every_try": True,
    }
    assert made.start == {"kind": "seed", "file": "db/seed.sql"}  # kept relative to the app
    assert (made.reset, made.copies) == ("test", 2)
    assert now.person["name"] == "" and now.copies == 1  # the setup changed is left as it was


def test_each_save_is_a_version_and_only_a_new_start_needs_the_plan_proven_again(config) -> None:
    store = SetupStore(config.data_dir)
    plans = PlanStore(config.data_dir)
    plans.save(_plan())
    folder = config.targets["bot"].code.path
    before = setup_for(config, "bot")

    after = store.save(
        "bot", changed(before, {"person": {"name": "Alex"}, "copies": 1}), before=before
    )
    assert after.version == 1 and isinstance(after.updated, float)
    assert plans.load("bot").status == "proven"  # type: ignore[union-attr]
    assert len(plans.history("bot")) == 1

    seeded = changed(after, {"start": {"kind": "seed", "file": "db/seed.sql"}}, folder=folder)
    assert store.save("bot", seeded, before=after).version == 2
    plan = plans.load("bot")
    assert plan is not None and plan.status == "stale"
    assert plan.report.endswith("Setup changed: starts with sample data.")
    assert len(plans.history("bot")) == 2

    back = changed(seeded, {"start": {"kind": "empty"}, "reset": "test"})
    store.save("bot", back, before=seeded)
    assert plans.load("bot").report.endswith("Setup changed: starts empty.")  # type: ignore[union-attr]

    loaded = store.load("bot")
    assert loaded.version == 3 and loaded.reset == "test" and loaded.person["name"] == "Alex"
    assert [s.version for s in store.history("bot")] == [1, 2, 3]
    kept = json.loads((config.data_dir / "isolation/bot/setup.json").read_text())
    assert kept["format"] == 1 and "version" not in kept
    # The plan's own versions are where they were, untouched by the setup's.
    assert sorted(p.name for p in (config.data_dir / "isolation/bot/versions").iterdir()) == [
        "1.json",
        "2.json",
        "3.json",
    ]


def test_a_failed_plan_isnt_marked_stale_by_a_new_start(config) -> None:
    PlanStore(config.data_dir).save(_plan("failed"))
    before = setup_for(config, "bot")
    folder = config.targets["bot"].code.path
    seeded = changed(before, {"start": {"kind": "seed", "file": "db/seed.sql"}}, folder=folder)
    SetupStore(config.data_dir).save("bot", seeded, before=before)
    assert PlanStore(config.data_dir).load("bot").status == "failed"  # type: ignore[union-attr]


def test_an_older_try_reads_as_put_back_every_try_and_an_older_run_with_no_person() -> None:
    old_try = {"isolation": {"how": "proven", "plan_version": 2, "restore_seconds": 1.0,
                             "checked": ["db"], "problem": None}}  # fmt: skip
    assert isolation_of(old_try)["reset"] == "try"
    old_run = {"isolation": {"how": "proven", "plan_version": 2}}
    assert isolation_of(old_run, run=True) == {"how": "proven", "plan_version": 2, "person": None}


# The routes


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as made:
        yield made


def test_get_shows_the_setup_what_the_app_supports_and_whether_it_is_checked_again(
    client, config, monkeypatch
) -> None:
    found = client.get("/v1/targets/bot/isolation/setup").json()
    assert found["copies"] == 3 and found["reset"] == "try" and found["version"] == 0
    assert found["supports"] == {"person": [], "seed": False}
    assert found["needs_check"] is False

    PlanStore(config.data_dir).save(_plan())
    person = SimpleNamespace(capabilities=SimpleNamespace(person=("timezone", "name", "nickname")))
    monkeypatch.setattr(Targets, "adapter", lambda self, name: person)
    found = client.get("/v1/targets/bot/isolation/setup").json()
    assert found["supports"] == {"person": ["name", "timezone"], "seed": True}
    assert client.get("/v1/targets/nope/isolation/setup").status_code == 404


def test_put_changes_part_of_it_and_a_new_start_needs_a_check(client, config) -> None:
    PlanStore(config.data_dir).save(_plan())
    done = client.put("/v1/targets/bot/isolation/setup", json={"reset": "test", "copies": 1})
    assert done.status_code == 200, done.text
    found = done.json()
    assert (found["reset"], found["copies"], found["version"]) == ("test", 1, 1)
    assert found["person"]["new_every_try"] is True and found["needs_check"] is False

    done = client.put(
        "/v1/targets/bot/isolation/setup",
        json={"start": {"kind": "seed", "file": "db/seed.sql"}, "person": {"name": "Alex"}},
    )
    found = done.json()
    assert found["start"] == {"kind": "seed", "file": "db/seed.sql"}
    assert found["person"]["name"] == "Alex" and found["reset"] == "test"
    assert found["needs_check"] is True
    assert client.get("/v1/targets/bot/isolation").json()["status"] == "stale"

    refused = client.put("/v1/targets/bot/isolation/setup", json={"copies": 9})
    assert refused.status_code == 400 and "from 1 to 3" in refused.json()["detail"]
    refused = client.put(
        "/v1/targets/bot/isolation/setup", json={"start": {"kind": "seed", "file": "../x.sql"}}
    )
    assert refused.status_code == 400
    assert client.get("/v1/targets/bot/isolation/setup").json()["version"] == 2


def test_put_wants_a_postgres_store_for_sample_data(client, config) -> None:
    PlanStore(config.data_dir).save(_plan(postgres=False))
    refused = client.put(
        "/v1/targets/bot/isolation/setup", json={"start": {"kind": "seed", "file": "db/seed.sql"}}
    )
    assert refused.status_code == 400 and "Postgres" in refused.json()["detail"]


def test_put_is_refused_during_a_run_or_while_testing_is_set_up(client, monkeypatch) -> None:
    ws = client.app.state.workspaces.get(LOCAL)
    monkeypatch.setattr(ws.runs, "busy", lambda: True)
    refused = client.put("/v1/targets/bot/isolation/setup", json={"copies": 2})
    assert refused.status_code == 409 and "run is going" in refused.json()["detail"]
    monkeypatch.setattr(ws.runs, "busy", lambda: False)
    monkeypatch.setattr(ws.isolations, "running", lambda name: True)
    assert client.put("/v1/targets/bot/isolation/setup", json={"copies": 2}).status_code == 409
    assert client.get("/v1/targets/bot/isolation/setup").json()["version"] == 0


# Live setup progress


def _states(job: isolating.Job) -> list[tuple[str, str]]:
    return [(s["key"], s["state"]) for s in job.steps]


async def test_the_job_shows_each_step_as_the_real_events_come(config, monkeypatch) -> None:
    SetupStore(config.data_dir).save("bot", changed(Setup(), {"person": {"name": "Alex Rivera"}}))
    jobs = isolating.Isolations()
    seen: dict[str, Any] = {}

    async def fake(config, target, *, ask, on_stage, on_plan, **_):
        job = jobs.jobs[target]
        assert await ask("a copy runs")
        on_stage("start")
        on_stage("copy")
        await asyncio.sleep(0.01)
        on_stage("find")
        seen["finding"] = _states(job)
        on_stage("plan")
        plan = _plan()
        on_plan(plan)
        seen["drafted"] = [dict(s) for s in job.steps]
        for store in ("postgres-app", "volume-artifact-data", "dir-uploads"):
            on_stage(f"store:{store}")
        on_stage("person")
        seen["person"] = _states(job)
        on_stage("rehearse")
        on_stage("prove")
        return plan

    monkeypatch.setattr(iso, "isolate", fake)
    job = jobs.start(config, "bot")
    assert _states(job) == [
        ("copy", "running"),
        ("find", "waiting"),
        ("person", "waiting"),
        ("prove", "waiting"),
    ]
    await asyncio.wait_for(asyncio.gather(*jobs._tasks.values()), 5)

    assert seen["finding"] == [
        ("copy", "done"),
        ("find", "running"),
        ("person", "waiting"),
        ("prove", "waiting"),
    ]
    drafted = seen["drafted"]
    assert [(s["key"], s["label"], s["detail"]) for s in drafted] == [
        ("copy", "Copy bot", None),
        ("find", "Find where it keeps things", None),
        ("store:postgres-app", "Give it its own Postgres", "app"),
        ("store:volume-artifact-data", "Give it its own memory", "artifact-data"),
        ("store:dir-uploads", "Give it its own files", "uploads"),
        ("person", "Make a test person", "Alex Rivera"),
        ("prove", "Check it resets cleanly", None),
    ]
    assert drafted[0]["seconds"] >= 0.0 and drafted[1]["state"] == "running"
    assert all(s["state"] == "waiting" and s["seconds"] is None for s in drafted[2:])
    assert seen["person"][-2:] == [("person", "running"), ("prove", "waiting")]
    assert seen["person"][2:5] == [(k, "done") for k, _ in seen["person"][2:5]]

    view = job.view()
    assert view["state"] == "done" and view["error"] is None
    assert all(s["state"] == "done" for s in view["steps"])
    assert all(isinstance(s["seconds"], float) for s in view["steps"])
    assert set(view["steps"][0]) == {"key", "label", "state", "seconds", "detail"}


async def test_a_step_that_breaks_fails_and_a_plan_not_proven_fails_the_check(
    config, monkeypatch
) -> None:
    jobs = isolating.Isolations()

    async def breaks(config, target, *, on_stage, on_plan, **_):
        on_stage("copy")
        on_stage("find")
        on_plan(_plan())
        on_stage("store:postgres-app")
        raise RuntimeError("the copy's database didn't answer")

    monkeypatch.setattr(iso, "isolate", breaks)
    job = jobs.start(config, "bot")
    await asyncio.gather(*jobs._tasks.values())
    assert job.state == "failed" and job.error == "the copy's database didn't answer"
    states = dict(_states(job))
    assert states["store:postgres-app"] == "failed" and states["find"] == "done"
    assert states["person"] == "waiting" and states["prove"] == "waiting"
    assert dict((s["key"], s["detail"]) for s in job.steps)["person"] == "the app's own"

    async def not_proven(config, target, *, on_stage, on_plan, **_):
        on_stage("copy")
        on_stage("prove")
        return _plan("failed")

    monkeypatch.setattr(iso, "isolate", not_proven)
    job = jobs.start(config, "bot")
    await asyncio.gather(*jobs._tasks.values())
    assert job.state == "done" and job.steps[-1]["state"] == "failed"


# Honoring the setup on a copy


@dataclasses.dataclass
class FakePerson:
    name: str = ""
    language: str = ""
    timezone: str = ""


class FakeDriver:
    def __init__(self, events: list[str], store_id: str, sql: bool) -> None:
        self.events = events
        self.name = self.store_id = store_id
        if sql:
            self.load_sql = lambda path: events.append(f"load_sql {store_id} {path.name}")

    def digest(self) -> str:
        self.events.append(f"digest {self.store_id}")
        return "same"

    def keep(self, into: Path) -> None:
        self.events.append(f"keep {self.store_id}")

    def put_back(self, kept: Path) -> None:
        self.events.append(f"put_back {self.store_id}")


class FakeCopy:
    url = "http://127.0.0.1:9"
    pid = None

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def start(self) -> None:
        self.events.append("copy start")

    async def stop(self) -> None:
        self.events.append("copy stop")


class FakeAdapter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def persona(self, person: FakePerson) -> None:
        self.events.append(f"persona {person.name}")


@pytest.fixture
def fakes(config, monkeypatch, tmp_path):
    """Copies of the app, drivers and adapters that only say what was done to them."""
    events: list[str] = []
    kinds = {"postgres-app": True, "volume-artifact-data": False}

    async def drivers_for(plan, copy, *, roots=(), notes=None):
        return [FakeDriver(events, s.id, kinds[s.id]) for s in plan.stores if s.id in kinds]

    monkeypatch.setattr(iso, "drivers_for", drivers_for)
    monkeypatch.setattr(base, "Person", FakePerson, raising=False)
    for module in (iso, isolated):
        monkeypatch.setattr(module, "copy_of", lambda *a, **k: FakeCopy(events))
        monkeypatch.setattr(module, "adapter_on", lambda *a, **k: FakeAdapter(events))
        monkeypatch.setattr(module, "work_folder", lambda *a: tmp_path / "work")
        monkeypatch.setattr(module, "plan_spec", lambda *a: {"kind": "process"})
    return events


def _seeded(config) -> Setup:
    folder = config.targets["bot"].code.path
    body = {"start": {"kind": "seed", "file": "db/seed.sql"}, "person": {"name": "Alex"}}
    return SetupStore(config.data_dir).save("bot", changed(Setup(), body, folder=folder))


async def test_copies_load_the_sample_data_before_keeping_and_play_as_the_person(
    config, fakes
) -> None:
    setup = _seeded(config)
    found = isolated.Footing("proven", _plan(), 1)
    copies = isolated.Copies(config, "bot", found, None, setup)
    await copies.start(2)
    one = fakes[: fakes.index("persona Alex") + 1]
    assert one == [
        "copy start",
        "load_sql postgres-app seed.sql",
        "copy stop",
        "digest postgres-app",
        "digest volume-artifact-data",
        "keep postgres-app",
        "keep volume-artifact-data",
        "copy start",
        "persona Alex",
    ]
    assert fakes.count("load_sql postgres-app seed.sql") == 2 and len(copies.slots) == 2
    assert copies.person == "Alex" and copies.reset == "try"
    assert copies.unrestored() == {
        "how": "proven",
        "plan_version": 1,
        "restore_seconds": None,
        "checked": [],
        "problem": None,
        "reset": "try",
    }


async def test_an_adapter_without_a_persona_plays_as_the_apps_own(
    config, fakes, monkeypatch
) -> None:
    monkeypatch.setattr(FakeAdapter, "persona", None)
    copies = isolated.Copies(
        config, "bot", isolated.Footing("proven", _plan(), 1), None, _seeded(config)
    )
    await copies.start(1)
    assert copies.person is None and not [e for e in fakes if e.startswith("persona")]


async def test_a_seed_file_gone_since_it_was_chosen_stops_the_copies(config, fakes) -> None:
    setup = _seeded(config)
    (config.targets["bot"].code.path / "db" / "seed.sql").unlink()
    copies = isolated.Copies(config, "bot", isolated.Footing("proven", _plan(), 1), None, setup)
    with pytest.raises(iso.LaunchError, match="sample data"):
        await copies.start(1)
    assert fakes == []


async def test_a_rehearsal_starts_the_copy_as_the_setup_says_and_says_each_step(
    config, fakes, monkeypatch
) -> None:
    _seeded(config)
    stages: list[str] = []

    async def rehearse(drivers, runner, kept, ignore=()):
        fakes.append("rehearse")
        return SimpleNamespace(ok=True, leaks=[], seconds=1.0, report=lambda: "Proven.")

    async def take_down(copy):
        return []

    monkeypatch.setattr(iso, "rehearse", rehearse)
    monkeypatch.setattr(iso, "take_down", take_down)
    plan = await iso.rehearse_plan(config, "bot", _plan(), on_stage=stages.append)
    assert plan.status == "proven"
    assert stages == [
        "start",
        "copy",
        "store:postgres-app",
        "store:volume-artifact-data",
        "store:dir-uploads",
        "person",
        "rehearse",
        "prove",
    ]
    assert fakes == [
        "copy start",
        "load_sql postgres-app seed.sql",
        "persona Alex",
        "rehearse",
    ]
