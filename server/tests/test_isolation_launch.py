"""Evalution's own copy of an app: the override it writes, the name guard on removal, values
from the env file kept out of everything shown, a process app started and stopped, and (with
Docker) a compose app brought up as ``eval-*`` and removed whole."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from itertools import count
from pathlib import Path

import httpx
import pytest

from evalution.isolation import launch
from evalution.isolation.launch import (
    ComposeCopy,
    LaunchError,
    ProcessCopy,
    copies,
    down,
    extras,
    guard,
    override_for,
    project_name,
    scrub,
    secrets_of,
    supports_tag,
)

TOY = Path(__file__).parent / "fixtures" / "toy_store_app"
SECRET = "hunter2-very-secret-value"

#: What ``docker compose config --format json`` gives for an app like relay's.
CONFIG = {
    "name": "eval-app-0",
    "services": {
        "engine": {
            "image": "app/engine:local",
            "container_name": "engine",
            "environment": {
                "PUBLIC_URL": "http://host.docker.internal:8012",
                "DATABASE_URL": f"postgres://u:{SECRET}@postgres:5432/app",
                "TRACES": "http://host.docker.internal:6006/v1/traces",
                "API_KEY": SECRET,
                "WORKERS": None,
            },
            "ports": [
                {
                    "mode": "ingress",
                    "host_ip": "127.0.0.1",
                    "target": 8000,
                    "published": "8012",
                    "protocol": "tcp",
                },
                {
                    "mode": "ingress",
                    "host_ip": "127.0.0.1",
                    "target": 8000,
                    "published": "5173",
                    "protocol": "tcp",
                },
            ],
            "volumes": [
                {"type": "volume", "source": "data", "target": "/data"},
                {
                    "type": "bind",
                    "source": "/var/run/docker.sock",
                    "target": "/var/run/docker.sock",
                },
            ],
        },
        "postgres": {
            "image": "postgres:16-alpine",
            "environment": {"POSTGRES_PASSWORD": SECRET},
            "volumes": [
                {"type": "volume", "source": "pg", "target": "/var/lib/postgresql/data"},
                {"type": "bind", "source": "/src/schemas.sql", "target": "/init/schemas.sql"},
            ],
        },
    },
    "volumes": {
        "data": {"name": "eval-app-0_data"},
        "pg": {"name": "legacy-pg"},
        "shared": {"name": "people", "external": True},
    },
}


def picker():
    ports = count(40001)
    return lambda: next(ports)


def test_override_moves_ports_and_points_settings_at_the_copy():
    out = override_for(CONFIG, "eval-app-0", app_url="http://localhost:8012/api", pick=picker())

    assert out.ports == {"engine": {8000: 40001}}
    assert out.url == "http://127.0.0.1:40001/api"
    assert '    ports: !override\n      - "127.0.0.1:40001:8000"\n' in out.text
    assert out.text.count("127.0.0.1:40001:8000") == 1  # one host port per container port
    assert '    container_name: "eval-app-0-engine-1"' in out.text
    assert '      PUBLIC_URL: "http://host.docker.internal:40001"' in out.text
    assert out.rewired == ["engine.PUBLIC_URL"]
    assert out.fixed == ["engine.TRACES"]  # reaches the person's trace viewer, not the copy's


def test_override_leaves_project_volumes_and_reports_shared_ones():
    out = override_for(CONFIG, "eval-app-0", pick=picker())

    assert "data:" not in out.text  # already the copy's own
    assert '  pg:\n    name: "eval-app-0_pg"' in out.text  # a fixed name made the copy's
    assert "people" not in out.text
    shared = {(m.service, m.source) for m in out.shared}
    assert shared == {
        ("engine", "/var/run/docker.sock"),
        ("postgres", "/src/schemas.sql"),
        ("*", "volume:people"),
    }
    assert any("Docker socket" in n for n in out.notes)


def test_override_holds_no_value_from_the_env_file():
    out = override_for(CONFIG, "eval-app-0", pick=picker())
    assert SECRET not in out.text
    assert SECRET not in repr(out)


def test_without_the_tag_the_resolved_file_is_rewritten():
    out = override_for(CONFIG, "eval-app-0", pick=picker(), tag=False)
    whole = json.loads(out.text)
    engine = whole["services"]["engine"]
    assert engine["ports"] == [
        {"target": 8000, "published": "40001", "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]
    assert engine["environment"]["PUBLIC_URL"] == "http://host.docker.internal:40001"
    assert whole["volumes"]["pg"]["name"] == "eval-app-0_pg"
    assert whole["volumes"]["shared"] == {"name": "people", "external": True}


def test_the_app_url_defaults_to_the_first_port_that_isnt_a_database():
    config = {
        "services": {
            "db": {"ports": [{"target": 5432, "published": "5432"}]},
            "web": {"ports": [{"target": 3000, "published": "3000"}]},
        }
    }
    assert override_for(config, "eval-x-0", pick=picker()).url == "http://127.0.0.1:40002"


def test_names_and_versions():
    assert project_name("Relay Lite", 2) == "eval-relay-lite-2"
    assert supports_tag("2.24.4") and supports_tag("v5.5.1")
    assert not supports_tag("2.24.3")


@pytest.mark.parametrize("name", ["relay-evals", "relay", "eval", "evalx-1", "eval-", "x"])
async def test_down_refuses_anything_not_named_eval(name, monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("Docker was asked")

    monkeypatch.setattr(launch, "_run", never)
    with pytest.raises(LaunchError, match="isn't evalution's"):
        await down(name)
    with pytest.raises(LaunchError):
        guard(name)


async def test_env_values_are_scrubbed_from_what_compose_prints(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"API_KEY={SECRET}\nexport OTHER='{SECRET}-2'\nSHORT=1\n")
    secrets = secrets_of(env)
    assert secrets == [f"{SECRET}-2", SECRET]
    assert scrub(f"bad {SECRET}-2 and {SECRET}", secrets) == "bad *** and ***"
    code, _, err = await launch._run(
        "sh", "-c", f"echo 'oops {SECRET}' >&2; exit 3", secrets=secrets
    )
    assert code == 3 and SECRET not in err and "oops ***" in err


# A process app


def toy(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "app"
    shutil.copytree(TOY, app)
    (app / "data").mkdir()
    return app, tmp_path / "hidden.db"


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # A zombie still answers; ask ps whether it is one.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return state.stdout.strip().startswith("Z") or not state.stdout.strip()


async def test_process_copy_starts_answers_and_stops_with_its_children(tmp_path):
    app, hidden = toy(tmp_path)
    child = tmp_path / "child.pid"
    command = f"sleep 300 & echo $! > {child}; exec {sys.executable} app.py {{port}} {hidden}"
    copy = ProcessCopy(["sh", "-c", command], app, env={"DATABASE_URL": "sqlite:///x"},
                       folder=tmp_path / "logs")  # fmt: skip
    pid = await copy.start()
    assert copy.pid == pid
    async with httpx.AsyncClient() as client:
        r = await client.post(copy.url, json={"message": "hi"})
    assert r.json() == {"reply": "noted"}
    sleeper = int(child.read_text())

    await copy.stop()
    assert gone(pid)
    assert gone(sleeper)
    assert copy.pid is None


async def test_process_copy_that_exits_is_an_error(tmp_path):
    copy = ProcessCopy([sys.executable, "-c", "raise SystemExit(4)"], tmp_path)
    with pytest.raises(LaunchError, match="exited with code 4"):
        await copy.start()


async def test_process_copy_that_never_answers_is_stopped(tmp_path):
    copy = ProcessCopy(["sleep", "300"], tmp_path, start_seconds=0.5, stop_seconds=0.5)
    with pytest.raises(LaunchError, match="didn't answer"):
        await copy.start()
    assert copy.pid is None


# A compose app, with Docker


def docker_up() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        ok = subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
        image = subprocess.run(
            ["docker", "image", "inspect", "postgres:16-alpine"], capture_output=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ok and image.returncode == 0


needs_docker = pytest.mark.skipif(
    not docker_up(), reason="needs Docker running and postgres:16-alpine"
)

COMPOSE = """\
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD:?set it}
    ports:
      - "5432"
      - "127.0.0.1:${DB_PORT:-55432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 1s
      retries: 30
volumes:
  pgdata:
"""


@needs_docker
async def test_compose_copy_runs_on_its_own_ports_and_volumes_and_goes_away(tmp_path):
    (tmp_path / "compose.yaml").write_text(COMPOSE)
    env = tmp_path / ".env"
    env.write_text(f"DB_PASSWORD={SECRET}\n")
    target = f"test-{uuid.uuid4().hex[:8]}"
    copy = ComposeCopy([tmp_path / "compose.yaml"], env, target, 0, tmp_path / "eval")
    assert copy.project == f"eval-{target}-0"

    started = await copy.start()
    try:
        assert started.project in await copies()
        port = started.ports["db"][5432]
        assert started.url == f"http://127.0.0.1:{port}"
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        assert started.volumes == [f"{started.project}_pgdata"]
        assert started.containers == [f"{started.project}-db-1"]
        assert started.shared == []
        assert SECRET not in started.override.read_text()
        assert SECRET not in repr(started)
        # Other Docker activity on this computer may show as unclaimed extras; none is the copy's.
        assert [e for e in await extras(started) if e.name.startswith(started.project)] == []
    finally:
        left = await copy.stop()
    assert [e for e in left if e.name.startswith(copy.project)] == []
    assert started.project not in await copies()
    _, names, _ = await launch._run("docker", "ps", "-aq", "--filter", f"name={started.project}")
    _, volumes, _ = await launch._run(
        "docker", "volume", "ls", "-q", "--filter", f"name={started.project}"
    )
    assert names.strip() == "" and volumes.strip() == ""


@needs_docker
async def test_a_missing_image_is_named_and_nothing_is_started(tmp_path):
    (tmp_path / "compose.yaml").write_text(
        "services:\n  app:\n    image: evalution-test/never-built:0\n"
        "    environment:\n      KEY: ${KEY}\n"
    )
    env = tmp_path / ".env"
    env.write_text(f"KEY={SECRET}\n")
    copy = ComposeCopy([tmp_path / "compose.yaml"], env, "test-missing", 0, tmp_path / "eval")
    with pytest.raises(LaunchError, match="never-built") as raised:
        await copy.start()
    assert SECRET not in str(raised.value)
    assert copy.project not in await copies()
