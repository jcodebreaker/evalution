"""A run on evalution's own copy of the app (ADR 0009, phase 5): what a plan covers, what a run
stands on, a stale plan rehearsed again, old runs read as not isolated, and the test setup
honored: the copies side by side, when they're put back, and the test person."""

import dataclasses
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import scripted_agent
from scripted_agent import SET, TARGET

from evalution.adapters import base
from evalution.adapters.base import Capabilities, Isolation
from evalution.config import IsolationConfig
from evalution.core.case import CLEAN_KINDS
from evalution.core.rules import current
from evalution.isolation.launch import LaunchError
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.isolation.setup import Setup, SetupStore
from evalution.results.migrate import upcast
from evalution.results.record import SCHEMA, isolation_of
from evalution.runs import isolated

MEMORY_FILE = "app-data/memory.json"


def _plan(*stores: PlannedStore, status: str = "proven", **fields: Any) -> Plan:
    return Plan(target=TARGET, stores=list(stores), status=status, **fields)  # type: ignore[arg-type]


def test_what_a_plan_covers() -> None:
    db = PlannedStore("db", "postgres", "eval-x-0-postgres-1:app", strategy="relocate")
    volume = PlannedStore("files", "volume", "volume:data", strategy="relocate")
    hosted = PlannedStore("mem", "hosted", "env:MEM0_API_KEY", strategy="none")
    assert isolated.covered(_plan(db, volume)) == frozenset(CLEAN_KINDS)
    assert isolated.covered(_plan(db)) == {"conversation", "memory"}
    # A store nothing resets could hold the memory: only the conversation is promised.
    assert isolated.covered(_plan(db, hosted)) == {"conversation"}
    assert isolated.covered(_plan(hosted)) == frozenset()


def test_a_managed_target_starts_clean_of_what_its_plan_covers() -> None:
    session = Capabilities(Isolation.SESSION, "")
    managed = session.managed(frozenset({"memory", "nonsense"}), "note")
    assert managed.isolation is Isolation.MANAGED and managed.isolation_note == "note"
    assert managed.starts_clean() == {"conversation", "memory"}
    assert "isolation" in managed.names()
    # Without a plan nothing changes.
    assert session.starts_clean() == {"conversation"}


def test_what_a_run_stands_on(tmp_path) -> None:
    code = tmp_path / "app"
    code.mkdir()
    (code / "db.py").write_text("DATABASE_URL = 'x'\n")
    config = _config(tmp_path, code)
    plain = Capabilities(Isolation.SESSION, "")
    assert isolated.footing(config, TARGET, plain).how == "none"
    assert isolated.footing(config, TARGET, replace(plain, isolation=Isolation.IDENTITY)).how == (
        "identity"
    )
    store = PlanStore(config.data_dir)
    db = PlannedStore("db", "sqlite", str(code / "app.db"), strategy="snapshot")
    db.evidence = ["db.py:1"]
    from evalution.isolation.plan import hashes

    store.save(_plan(db, evidence_hashes=hashes(code, db.evidence)))
    found = isolated.footing(config, TARGET, plain)
    assert (found.how, found.version, found.managed) == ("proven", 1, True)
    assert found.run_label() == {"how": "proven", "plan_version": 1, "person": None}

    (code / "db.py").write_text("DATABASE_URL = 'y'\n")
    found = isolated.footing(config, TARGET, plain)
    assert found.how == "none" and found.to_rehearse and found.changed == ["db.py"]
    # Rehearsed as the run starts, not refused before it.
    required = replace(config, isolation=IsolationConfig(require_proven=True))
    assert isolated.refusal(required, TARGET, found) is None

    store.save(_plan(db, status="failed"))
    found = isolated.footing(config, TARGET, plain)
    assert not found.to_rehearse and found.problem == "its isolation plan is failed, not proven"
    assert found.try_label()["problem"] == found.problem
    said = isolated.refusal(required, TARGET, found)
    assert said is not None and "`evalution isolate --target agent`" in said
    assert isolated.refusal(config, TARGET, found) is None


def _config(tmp_path: Path, code: Path, url: str | None = "http://127.0.0.1:9") -> Any:
    from evalution.config import load

    (tmp_path / "evalution.yaml").write_text(
        f"targets:\n  {TARGET}:\n    adapter: http\n    code: {{path: {code}}}\n"
        + (f"    url: '{url}'\n" if url else "")
        + "    http: {send: {url: 'http://127.0.0.1:9/x'}, reply: '$.reply'}\n"
    )
    return load(tmp_path / "evalution.yaml")


def test_a_target_that_would_still_reach_the_app_isnt_run_on_its_copy(tmp_path) -> None:
    """An http route written in full on another address than the target's own would reach that
    address from the copy too, while the run says it played on the copy: refused, naming it,
    whatever ``isolation.require_proven`` says. On the target's own address it is moved."""
    code = tmp_path / "app"
    code.mkdir()
    plain = Capabilities(Isolation.SESSION, "")
    config = _config(tmp_path, code, url=None)
    PlanStore(config.data_dir).save(_plan(PlannedStore("db", "sqlite", "app.db")))
    found = isolated.footing(config, TARGET, plain)
    assert found.managed
    said = isolated.refusal(config, TARGET, found)
    assert said is not None and "can't play on evalution's own copy of the app" in said
    assert "http.send.url (http://127.0.0.1:9/x)" in said and "{url}" in said
    # Not on a copy, there is nothing to say.
    assert isolated.refusal(config, TARGET, isolated.Footing("none")) is None
    # Before a copy is started: isolating or rehearsing it again starts nothing.
    from evalution.config import ConfigError
    from evalution.isolation import isolate

    with pytest.raises(ConfigError, match=r"http\.send\.url"):
        isolate.adapter_on(config, TARGET, "http://127.0.0.1:5123")
    assert isolate.off_copy(config, TARGET) is not None

    config = _config(tmp_path, code)
    assert isolated.refusal(config, TARGET, isolated.footing(config, TARGET, plain)) is None
    adapter = isolate.adapter_on(config, TARGET, "http://127.0.0.1:5123")
    assert adapter.send_conf["url"] == "http://127.0.0.1:5123/x"  # type: ignore[attr-defined]


async def test_a_stale_plan_is_rehearsed_again_and_the_run_plays_on_the_copy(
    make_rig, monkeypatch
) -> None:
    rig = make_rig(options={"memory_file": MEMORY_FILE})
    rig.manage()
    store = PlanStore(rig.config.data_dir)
    plan = store.load(TARGET)
    assert plan is not None
    plan.status = "stale"
    store.save(plan)
    rehearsed: list[str] = []

    async def rehearse(config, target, plan, *, slot=0):
        rehearsed.append(f"{target}-{slot}")
        plan.status = "proven"
        return plan

    monkeypatch.setattr(isolated, "rehearse_plan", rehearse)
    rig.fill("memory-recalls-fact")
    doc = await rig.run(tries=1)
    assert rehearsed == [f"{TARGET}-0"]
    assert doc["isolation"] == {"how": "proven", "plan_version": 3, "person": None}
    # How long the copies took to start, for the Run dialog's rough figure next time.
    assert isinstance(doc["copy_start_seconds"], float) and doc["copy_start_seconds"] >= 0
    assert store.load(TARGET).status == "proven"  # type: ignore[union-attr]
    (attempt,) = [t for c in doc["cases"].values() for t in c["tries"]]
    assert attempt["isolation"]["how"] == "proven" and attempt["isolation"]["problem"] is None


async def test_a_stale_plan_that_fails_again_refuses_a_run_that_requires_one(
    make_rig, monkeypatch
) -> None:
    rig = make_rig(options={"memory_file": MEMORY_FILE})
    rig.manage()
    store = PlanStore(rig.config.data_dir)
    plan = store.load(TARGET)
    assert plan is not None
    plan.status = "stale"
    store.save(plan)

    async def rehearse(config, target, plan, *, slot=0):
        plan.status = "failed"
        return plan

    monkeypatch.setattr(isolated, "rehearse_plan", rehearse)
    rig.targets.config = replace(rig.config, isolation=IsolationConfig(require_proven=True))
    rig.fill("memory-recalls-fact")
    doc = await rig.run(tries=1)
    assert doc["status"] == "failed"
    assert "evalution isolate --target agent" in doc["stop_reason"]
    assert not [t for c in doc["cases"].values() for t in c["tries"]]
    assert store.load(TARGET).status == "failed"  # type: ignore[union-attr]


def test_an_older_run_reads_as_not_isolated() -> None:
    old = {
        "schema": 2,
        "run": "r",
        "cases": {"a": {"tries": [{"n": 1}]}},
    }
    read = upcast(old)
    assert read["schema"] == SCHEMA and "isolation" not in old
    assert read["isolation"] == {"how": "none", "plan_version": None, "person": None}
    assert read["cases"]["a"]["tries"][0]["isolation"] == {
        "how": "none",
        "plan_version": None,
        "restore_seconds": None,
        "checked": [],
        "problem": None,
        "reset": "try",
    }
    assert isolation_of({}) == read["cases"]["a"]["tries"][0]["isolation"]
    assert isolation_of({}, run=True) == read["isolation"]


@pytest.mark.parametrize("how", ["proven", "identity", "none"])
def test_isolation_labels_keep_one_shape(how: str) -> None:
    from evalution.results.record import isolation

    assert set(isolation(how)) == {
        "how",
        "plan_version",
        "restore_seconds",
        "checked",
        "problem",
        "reset",
    }
    with pytest.raises(ValueError, match="how is one of"):
        isolation("maybe")
    with pytest.raises(ValueError, match="reset is one of"):
        isolation(how, reset="never")


# The test setup, honored


def _tries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for c in doc["cases"].values() for t in c["tries"]]


async def test_put_back_before_each_test_only_restores_its_first_try(make_rig) -> None:
    """The faster reset: a test's later tries find what its earlier ones left."""
    rig = make_rig(
        ["leak_across_tries", "forgets_to_store"],
        options={"store_fails_from_try": 2, "memory_file": MEMORY_FILE},
    )
    rig.manage()
    SetupStore(rig.config.data_dir).save(TARGET, Setup(reset="test"))
    rig.fill("memory-recalls-fact")
    doc = await rig.run(tries=2)
    first, second = (t["isolation"] for t in _tries(doc))
    assert first["reset"] == second["reset"] == "test"
    assert isinstance(first["restore_seconds"], float) and first["checked"] == ["memory"]
    assert second["restore_seconds"] is None and second["checked"] == []
    assert second["how"] == "proven" and second["problem"] is None
    # Not put back, try 2 recalls what try 1 stored: what a reset per test can't rule out.
    assert [current(t).outcome.value for t in _tries(doc)] == ["passed", "passed"]


async def test_a_run_plays_on_as_many_copies_as_the_setup_says(make_rig, monkeypatch) -> None:
    parallel = replace(scripted_agent.ScriptedAgent.capabilities, parallel=True)
    monkeypatch.setattr(scripted_agent.ScriptedAgent, "capabilities", parallel)
    started: list[int] = []

    async def start(self, slots: int) -> None:
        started.append(slots)
        raise LaunchError("only counting")

    monkeypatch.setattr(isolated.Copies, "start", start)
    rig = make_rig(options={"memory_file": MEMORY_FILE})
    rig.manage()
    rig.fill("memory-recalls-fact")
    rig.fill("forget-on-request")
    assert rig.config.targets[TARGET].max_parallel == 1
    SetupStore(rig.config.data_dir).save(TARGET, Setup(copies=2))
    await rig.run(tries=1)
    SetupStore(rig.config.data_dir).save(TARGET, Setup(copies=1))
    await rig.run(tries=1)
    assert started == [2, 1]


@dataclasses.dataclass
class _Person:
    name: str = ""
    language: str = ""
    timezone: str = ""
    new_every_try: bool = True


async def test_each_copy_plays_as_the_setups_test_person(make_rig, monkeypatch) -> None:
    events: list[str] = []
    opened = scripted_agent.ScriptedAgent.open

    def persona(self, person: _Person) -> None:
        events.append(f"persona {person.name} {person.timezone}")

    async def open_(self, run: str):
        events.append("open")
        return await opened(self, run)

    monkeypatch.setattr(base, "Person", _Person, raising=False)
    monkeypatch.setattr(scripted_agent.ScriptedAgent, "persona", persona, raising=False)
    monkeypatch.setattr(scripted_agent.ScriptedAgent, "open", open_)
    rig = make_rig(options={"memory_file": MEMORY_FILE})
    rig.manage()
    person = {"name": "Alex Rivera", "language": "", "timezone": "Europe/Lisbon",
              "new_every_try": True}  # fmt: skip
    SetupStore(rig.config.data_dir).save(TARGET, Setup(person=person))
    rig.fill("memory-recalls-fact")
    doc = await rig.run(tries=1)
    assert events[0] == "persona Alex Rivera Europe/Lisbon" and "open" in events
    assert doc["isolation"] == {"how": "proven", "plan_version": 1, "person": "Alex Rivera"}


async def test_without_a_person_type_the_copies_play_as_the_apps_own(make_rig, monkeypatch) -> None:
    monkeypatch.delattr(base, "Person", raising=False)
    rig = make_rig(options={"memory_file": MEMORY_FILE})
    rig.manage()
    SetupStore(rig.config.data_dir).save(TARGET, Setup(person={"name": "Alex"}))
    rig.fill("memory-recalls-fact")
    doc = await rig.run(tries=1)
    assert doc["status"] == "done" and doc["isolation"]["person"] is None


_ = SET
