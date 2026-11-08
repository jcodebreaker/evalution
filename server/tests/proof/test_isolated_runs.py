"""A run on evalution's own copy of the app (ADR 0009, phase 5): what a plan covers, what a run
stands on, a stale plan rehearsed again, and old runs read as not isolated."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripted_agent import SET, TARGET

from evalution.adapters.base import Capabilities, Isolation
from evalution.config import IsolationConfig
from evalution.core.case import CLEAN_KINDS
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.results.migrate import upcast
from evalution.results.record import isolation_of
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
    assert found.run_label() == {"how": "proven", "plan_version": 1}

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


def _config(tmp_path: Path, code: Path) -> Any:
    from evalution.config import load

    (tmp_path / "evalution.yaml").write_text(
        f"targets:\n  {TARGET}:\n    adapter: http\n    code: {{path: {code}}}\n"
        "    http: {send: {url: 'http://127.0.0.1:9/x'}, reply: '$.reply'}\n"
    )
    return load(tmp_path / "evalution.yaml")


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
    assert doc["isolation"] == {"how": "proven", "plan_version": 3}
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
    assert read["schema"] == 3 and "isolation" not in old
    assert read["isolation"] == {"how": "none", "plan_version": None}
    assert read["cases"]["a"]["tries"][0]["isolation"] == {
        "how": "none",
        "plan_version": None,
        "restore_seconds": None,
        "checked": [],
        "problem": None,
    }
    assert isolation_of({}) == read["cases"]["a"]["tries"][0]["isolation"]
    assert isolation_of({}, run=True) == read["isolation"]


@pytest.mark.parametrize("how", ["proven", "identity", "none"])
def test_isolation_labels_keep_one_shape(how: str) -> None:
    from evalution.results.record import isolation

    assert set(isolation(how)) == {"how", "plan_version", "restore_seconds", "checked", "problem"}
    with pytest.raises(ValueError, match="how is one of"):
        isolation("maybe")


_ = SET
