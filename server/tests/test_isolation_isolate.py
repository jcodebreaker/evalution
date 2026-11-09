"""The isolation pipeline's deterministic half: drivers mapped from a plan onto a compose copy,
the copy paused with its volumes kept, and a rehearsal there proving a plan that covers every
store and naming the store one leaves out. Docker-backed tests use only ``eval-test-*`` copies."""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from evalution.isolation import isolate as iso
from evalution.isolation.drivers import PostgresStore, VolumeStore
from evalution.isolation.launch import ComposeCopy, LaunchError, copies
from evalution.isolation.plan import Plan, PlannedStore
from evalution.isolation.rehearse import rehearse

# Without Docker ------------------------------------------------------------------------------


def test_launch_spec_finds_the_compose_file_and_env_by_path(tmp_path):
    (tmp_path / "deployment").mkdir()
    (tmp_path / "deployment" / "compose.yaml").write_text("services: {}\n")
    (tmp_path / ".env").write_text("SECRET=never-read-here\n")
    spec = iso.launch_spec(tmp_path, {"url": "http://127.0.0.1:8012"})
    assert spec == {
        "kind": "compose",
        "files": [str(tmp_path / "deployment" / "compose.yaml")],
        "env_file": str(tmp_path / ".env"),
        "app_url": "http://127.0.0.1:8012",
        "health": None,
    }
    assert "never-read-here" not in repr(spec)


def test_launch_spec_prefers_the_targets_own_block_and_says_when_there_is_none(tmp_path):
    given = {"kind": "process", "command": ["app", "--port", "{port}"], "cwd": str(tmp_path)}
    assert iso.launch_spec(tmp_path, {"launch": given})["command"] == given["command"]
    with pytest.raises(LookupError, match="launch"):
        iso.launch_spec(tmp_path, {})


def test_a_container_in_a_plan_maps_to_the_copys_container_of_the_same_service():
    own = {"eval-app-0-postgres-1": "postgres", "eval-app-0-engine-1": "engine"}
    project = "eval-app-0"
    assert iso._on_copy("relay-postgres-1", own, project) == "eval-app-0-postgres-1"
    assert iso._on_copy("eval-app-0-engine-1", own, project) == "eval-app-0-engine-1"
    assert iso._on_copy("someone-redis-1", own, project) is None


def test_only_containers_named_after_the_copy_are_its_own():
    assert iso._owned("eval-app-0-host-ab12", "eval-app-0")
    assert iso._owned("eval-app-0_data", "eval-app-0")
    assert not iso._owned("eval-app-01-host-ab12", "eval-app-0")
    assert not iso._owned("relay-evals-host-ab12", "eval-app-0")


async def test_clearing_extras_refuses_a_project_not_evalutions():
    with pytest.raises(LaunchError):
        await iso.clear_extras("relay-evals")
    with pytest.raises(LaunchError):
        await iso.copy_containers("relay")


async def test_pause_refuses_a_project_not_evalutions(tmp_path):
    copy = ComposeCopy([tmp_path / "c.yaml"], None, "x", 0, tmp_path)
    copy.project = "relay-evals"
    with pytest.raises(LaunchError):
        await copy.pause()
    with pytest.raises(LaunchError):
        await copy.resume()


# A compose copy, with Docker -----------------------------------------------------------------


def _ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        if subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode:
            return False
        for image in ("postgres:16-alpine", "alpine:3.20"):
            found = subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, timeout=20
            )
            if found.returncode:
                return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


needs_docker = pytest.mark.skipif(
    not _ready(), reason="needs Docker, postgres:16-alpine and alpine:3.20"
)

COMPOSE = """\
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: throwaway
      POSTGRES_DB: app
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d app"]
      interval: 1s
      retries: 60
  app:
    image: alpine:3.20
    command: ["sleep", "infinity"]
    init: true
    volumes:
      - notes:/data
volumes:
  pgdata:
  notes:
"""


def _docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True, timeout=120
    ).stdout


def _sql(container: str, sql: str) -> str:
    return _docker(
        "exec", container, "psql", "-U", "postgres", "-d", "app", "-X", "-q", "-A", "-t",
        "-v", "ON_ERROR_STOP=1", "-c", sql,
    ).strip()  # fmt: skip


class Probe:
    """The app's part, played by hand: a message lands in the database, its trace table and a
    file in the app's volume."""

    def __init__(self, project: str) -> None:
        self.db = f"{project}-db-1"
        self.app = f"{project}-app-1"

    async def __call__(self, canary: str) -> None:
        _sql(self.db, f"INSERT INTO items (body) VALUES ('{canary}')")
        _sql(self.db, f"INSERT INTO spans (body) VALUES ('{canary}')")
        _docker("exec", self.app, "sh", "-c", f"echo {canary} >> /data/memory_index")


@pytest.fixture
async def copy(tmp_path):
    (tmp_path / "compose.yaml").write_text(COMPOSE)
    made = ComposeCopy([tmp_path / "compose.yaml"], None, f"test-{uuid.uuid4().hex[:8]}", 0,
                       tmp_path / "eval")  # fmt: skip
    await made.start()
    try:
        db = f"{made.project}-db-1"
        _sql(db, "CREATE TABLE items (id serial PRIMARY KEY, body text)")
        _sql(db, "CREATE TABLE spans (id serial PRIMARY KEY, body text)")
        _sql(db, "INSERT INTO items (body) VALUES ('an earlier person')")
        _docker("exec", f"{made.project}-app-1", "sh", "-c", "echo earlier > /data/memory_index")
        yield made
    finally:
        await iso.take_down(made)
    assert made.project not in await copies()


def _plan(project: str, *, volume: bool) -> Plan:
    stores = [PlannedStore("postgres-app", "postgres", f"{project}-db-1:app", strategy="relocate")]
    if volume:
        stores.append(PlannedStore("volume-notes", "volume", "volume:notes", strategy="relocate"))
    stores.append(PlannedStore("memory-x", "memory", "env:CACHE", strategy="relocate"))
    return Plan(target="t", stores=stores, exclude=["app.public.spans"])


@needs_docker
async def test_pause_keeps_the_database_up_and_resume_brings_the_app_back(copy):
    stopped = await copy.pause(keep=[f"{copy.project}-db-1"])
    assert stopped == ["app"]
    assert _sql(f"{copy.project}-db-1", "SELECT count(*) FROM items") == "1"
    await copy.resume()
    out = _docker("exec", f"{copy.project}-app-1", "cat", "/data/memory_index")
    assert out.strip() == "earlier"  # the volume outlived the pause


@needs_docker
async def test_drivers_are_mapped_onto_the_copy(copy):
    notes: list[str] = []
    drivers = await iso.drivers_for(_plan(copy.project, volume=True), copy, notes=notes)
    pg, vol = drivers
    assert isinstance(pg, PostgresStore) and pg.container == f"{copy.project}-db-1"
    assert pg.db == "app" and pg.exclude == ["public.spans"]
    assert isinstance(vol, VolumeStore) and vol.volume == f"{copy.project}_notes"
    assert notes == ["memory-x: in the process's memory; a restart clears it"]


@needs_docker
async def test_a_plan_covering_every_store_is_proven_on_the_copy(copy, tmp_path):
    plan = _plan(copy.project, volume=True)
    drivers = await iso.drivers_for(plan, copy)
    runner = iso.ComposeRunner(copy, Probe(copy.project), [f"{copy.project}-db-1"])
    result = await rehearse(drivers, runner, tmp_path / "kept", ignore=plan.exclude)
    assert result.ok, result.report()
    db = f"{copy.project}-db-1"
    assert _sql(db, "SELECT string_agg(body, ',') FROM items") == "an earlier person"
    assert _sql(db, "SELECT count(*) FROM spans") == "1"  # the trace survived the reset
    goldens = _sql(db, "SELECT count(*) FROM pg_database WHERE datname LIKE 'eval_golden_%'")
    assert goldens == "0"


@needs_docker
async def test_a_plan_missing_the_volume_fails_and_names_where_the_canary_went(copy, tmp_path):
    plan = _plan(copy.project, volume=False)
    drivers = await iso.drivers_for(plan, copy)
    runner = iso.ComposeRunner(copy, Probe(copy.project), [f"{copy.project}-db-1"])
    result = await rehearse(drivers, runner, tmp_path / "kept", ignore=plan.exclude)
    assert not result.ok
    assert result.steps[2].name == "the app wrote only to planned stores"
    assert result.leaks == [f"{copy.project}-app-1:volume:{copy.project}_notes/memory_index"]
    assert "memory_index" in result.report()
    assert result.steps[-1].name == "left as it was found" and result.steps[-1].ok


def test_plan_path(tmp_path):
    class C:
        data_dir = tmp_path

    assert iso.plan_path(C, "app") == Path(tmp_path, "isolation", "app", "plan.json")  # type: ignore[arg-type]
