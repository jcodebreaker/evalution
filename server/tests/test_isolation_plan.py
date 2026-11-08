"""The plan drafted by rule from what the code read and the probe found, kept as data: the
relay-lite probe gives two stores to reset with traces left alone, a toy app's files give
snapshots, a cited file's edit marks the plan stale, and a saved plan can be undone."""

from pathlib import Path

import pytest

from evalution.isolation import containers
from evalution.isolation.discover import Store
from evalution.isolation.observe import Observation
from evalution.isolation.plan import Plan, PlanStore, draft_from, stale, strategy_for

PG = "relay-evals-postgres-1"
ENGINE = "relay-evals-engine-1"
MEMORY = "volume:relay-evals_artifact-data/artifacts/memory_index/documents"


def _project(root: Path) -> Path:
    """The files relay-lite's evidence cites, small stand-ins at the same paths."""
    for rel in (
        "deployment/entrypoint.py",
        "deployment/compose.yaml",
        "engine/atrium/db/migrations/env.py",
        "engine/pyproject.toml",
        "engine/atrium/models/model_catalog_store.py",
        "engine/atrium/db/utils.py",
    ):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(f"# {rel}\n")
    return root


def _relay() -> tuple[list[Store], list[containers.Found]]:
    """Stores as the code read and both reconciles leave them after one probe on relay-lite."""

    def pg(note: str) -> list[str]:
        return [f"holds the canary: {PG}: atrium.{note}"]

    tables = ["engine.conversation_items", "engine.round_events", "phoenix.spans"]
    seen_pg = [n for t in tables for n in pg(t)]
    found = [containers.Found(PG, "postgres", f"atrium.{t}") for t in tables]
    found.append(containers.Found(ENGINE, "volume", MEMORY))
    stores = [
        Store("postgres", "env:DATABASE_URL", ["deployment/entrypoint.py:166"], list(seen_pg)),
        Store(
            "postgres",
            "env:ATRIUM_DB_URL",
            ["engine/atrium/db/migrations/env.py:40"],
            list(seen_pg),
        ),
        Store("postgres", "service:postgres", ["deployment/compose.yaml:24"], list(seen_pg)),
        Store("postgres", "package:postgres", ["engine/pyproject.toml:185"], list(seen_pg)),
        Store(
            "volume",
            "volume:artifact-data",
            ["deployment/compose.yaml:121"],
            [f"holds the canary: {ENGINE}: {MEMORY}"],
        ),
        # Named, never reached: the database's own folder, a test URL, a data dir setting.
        Store("volume", "volume:postgres-data", ["deployment/compose.yaml:31"]),
        Store("postgres", "postgresql://prod-test-cluster/app", ["engine/tests/g.py:113"]),
        Store("dir", "env:ATRIUM_DATA_DIR", ["engine/atrium/models/model_catalog_store.py:90"]),
        # SQLite fallbacks and comments the probe never saw.
        *(
            Store("sqlite", f"{name}.db", [f"engine/atrium/db/utils.py:{n}"])
            for n, name in enumerate(["atrium", "local", "dev", "cache", "scratch"], 10)
        ),
    ]
    added = [
        Store(f.kind, f"{f.container}:{f.where}", ["found inside a container, by the probe"])
        for f in found
    ]
    for s, f in zip(added, found, strict=True):
        s.seen.append(f"holds the canary: {f.container}: {f.where}")
    return [*stores, *added], found


@pytest.mark.parametrize("pass_found", [True, False])
def test_relay_probe_drafts_two_stores_and_leaves_traces_alone(tmp_path, pass_found):
    root = _project(tmp_path)
    stores, found = _relay()
    plan = draft_from(root, stores, found=found if pass_found else None, target="relay-lite")

    by_kind = {p.kind: p for p in plan.stores}
    assert len(plan.stores) == 2, plan.report
    db, volume = by_kind["postgres"], by_kind["volume"]

    assert db.locator == f"{PG}:atrium"
    assert db.moved_by == ["DATABASE_URL", "ATRIUM_DB_URL"]
    assert db.strategy == "relocate"
    assert {"env:DATABASE_URL", "service:postgres", "package:postgres"} <= set(db.found_as)
    assert f"{PG}:atrium.engine.conversation_items" in db.found_as

    assert volume.locator == "volume:artifact-data"
    assert volume.strategy == "relocate"
    assert volume.moved_by == []
    assert f"{ENGINE}:{MEMORY}" in volume.found_as

    assert plan.exclude == ["atrium.phoenix.spans"]
    assert not any("phoenix" in loc for p in plan.stores for loc in p.found_as)
    assert not any(p.kind == "sqlite" for p in plan.stores)
    assert not any("postgres-data" in loc for p in plan.stores for loc in p.found_as)
    assert plan.launch == {"kind": "compose", "project": "relay-evals"}
    assert plan.status == "draft"
    assert "ATRIUM_DATA_DIR" in plan.report

    cited = set(plan.evidence_hashes)
    assert {"deployment/entrypoint.py", "deployment/compose.yaml", "engine/pyproject.toml"} <= cited
    assert "engine/atrium/db/utils.py" not in cited, "only the kept stores' evidence"


def test_plan_survives_a_json_round_trip(tmp_path):
    stores, found = _relay()
    plan = draft_from(_project(tmp_path), stores, found=found, target="relay-lite")
    text = plan.to_json()
    assert '"version": 1' in text
    assert Plan.from_json(text) == plan


def test_a_plan_from_a_newer_evalution_is_refused():
    with pytest.raises(ValueError, match="newer"):
        Plan.from_json('{"version": 99, "target": "x"}')


def test_editing_a_cited_file_marks_the_plan_stale(tmp_path):
    root = _project(tmp_path)
    stores, found = _relay()
    plan = draft_from(root, stores, found=found, target="relay-lite")
    assert stale(plan, root) == []

    (root / "deployment/compose.yaml").write_text("services: {}\n")
    (root / "engine/pyproject.toml").unlink()
    (root / "engine/atrium/db/utils.py").write_text("changed, but no kept store cites it\n")
    assert stale(plan, root) == ["deployment/compose.yaml", "engine/pyproject.toml"]


def test_saved_plans_keep_versions_and_undo(tmp_path):
    store = PlanStore(tmp_path / "data")
    assert store.load("relay-lite") is None

    first = Plan("relay-lite", report="first")
    assert store.save(first) == 1
    second = Plan("relay-lite", status="proven", report="second")
    assert store.save(second) == 2

    path = tmp_path / "data" / "isolation" / "relay-lite"
    assert (path / "plan.json").is_file() and (path / "versions" / "2.json").is_file()
    assert store.load("relay-lite").report == "second"
    assert [p.report for p in store.history("relay-lite")] == ["first", "second"]

    back = store.undo("relay-lite")
    assert back is not None and back.report == "first"
    assert store.load("relay-lite").report == "first"
    assert store.undo("relay-lite") is None
    assert store.load("relay-lite") is None
    with pytest.raises(LookupError):
        store.undo("relay-lite")


def test_plan_store_refuses_a_name_that_is_a_path(tmp_path):
    with pytest.raises(ValueError):
        PlanStore(tmp_path).load("../etc")


def test_toy_app_files_are_snapshotted_at_their_host_paths(tmp_path):
    app = tmp_path / "app"
    (app / "data").mkdir(parents=True)
    (app / "app.py").write_text('import os\nDATABASE_URL = os.getenv("DATABASE_URL")\n')
    outside = tmp_path / "elsewhere" / ".toyagent" / "memory.db"
    unnamed = "not named anywhere in the code"
    stores = [
        Store("sqlite", "data/chat.db", ["app.py:20"], [f"written: {app}/data/chat.db"]),
        Store("postgres", "env:DATABASE_URL", ["app.py:2"]),  # read, never connected
        Store("dir", f"{app}/cache/notes.txt", [unnamed], ["written: ... (holds the canary)"]),
        Store("sqlite", f"{outside}-wal", [unnamed], ["written: ... (holds the canary)"]),
        Store("sqlite", str(outside), [unnamed], ["written: ..."]),
    ]
    obs = Observation([str(app), str(outside.parent)])
    plan = draft_from(app, stores, obs, target="toy")

    got = {p.locator: p for p in plan.stores}
    assert set(got) == {str(app / "data/chat.db"), str(app / "cache"), str(outside)}, plan.report
    assert all(p.strategy == "snapshot" for p in plan.stores)
    assert got[str(outside)].found_as == [f"{outside}-wal", str(outside)]
    assert plan.launch == {"kind": "process"}
    assert plan.exclude == []
    assert list(plan.evidence_hashes) == ["app.py"]
    assert str(outside.parent) in plan.report


def test_hosted_and_in_process_stores_are_kept_with_their_rule():
    stores = [
        Store("hosted", "package:mem0ai", ["pyproject.toml:3"]),
        Store("memory", "InMemoryStore", ["app/graph.py:4"]),
    ]
    plan = draft_from(Path("."), stores, target="x")
    assert {(p.kind, p.strategy) for p in plan.stores} == {
        ("hosted", "none"),
        ("memory", "relocate"),
    }


def test_strategy_rules():
    assert strategy_for("postgres", "c:db", ["DATABASE_URL"], None, False) == "relocate"
    assert strategy_for("volume", "volume:x", [], None, True) == "relocate"
    assert strategy_for("sqlite", "/a/b.db", [], None, False) == "snapshot"
    assert strategy_for("redis", "env:REDIS_URL", [], "user_id", False) == "identity"
    assert strategy_for("hosted", "package:mem0ai", [], "user_id", False) == "identity"
    assert strategy_for("hosted", "package:mem0ai", ["MEM0_URL"], None, False) == "none"
    assert strategy_for("memory", "InMemoryStore", [], None, False) == "relocate"
    assert strategy_for("redis", "env:REDIS_URL", [], "session", False) == "none"
