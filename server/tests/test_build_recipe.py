"""How a version is built into evalution's own names (ADR 0011, phase 3): the recipe proposed by
rule, the build's command lines and env, the guards (5 GB free, names under ``evalution/``),
staleness, undo, removal and trim, proving, the API and the test writer's tools. Offline: Docker
is never run, every call to it is faked."""

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.accounts.context import LOCAL
from evalution.api.app import create_app
from evalution.config import load
from evalution.isolation import build
from evalution.isolation.build import BuildFailed, BuildRefused, Built
from evalution.isolation.launch import LaunchError
from evalution.isolation.recipe import (
    OWN,
    BuildRecipe,
    RecipeStore,
    SetupStep,
    build_env,
    check,
    image_names,
    override_text,
    propose,
    stale,
)
from evalution.project import building
from evalution.targets import Targets
from evalution.writer import building as writer_building

#: relay-lite's compose file, as phase 0 read it: both built images named through
#: IMAGE_REGISTRY and IMAGE_TAG, the host image build-only under the ``build`` profile.
RELAY = """\
name: relay

x-proxy-env: &proxy-env
  HTTP_PROXY: ${HTTP_PROXY:-}

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}

  engine:
    image: ${IMAGE_REGISTRY:-relay}/engine:${IMAGE_TAG:-local}
    build:
      context: ..
      dockerfile: deployment/engine.Dockerfile
    environment:
      <<: *proxy-env
      ATRIUM_HOST_IMAGE: ${IMAGE_REGISTRY:-relay}/host:${IMAGE_TAG:-local}
    ports:
      - "127.0.0.1:${RELAY_ENGINE_PORT:-8012}:8000"

  host:
    image: ${IMAGE_REGISTRY:-relay}/host:${IMAGE_TAG:-local}
    profiles: ["build"]
    build:
      context: ..
      dockerfile: deployment/host.Dockerfile
    command: ["true"]

  phoenix:
    image: arizephoenix/phoenix:21.0.0
    profiles: ["observability"]

volumes:
  postgres-data:
"""

FIXED = """\
services:
  db:
    image: postgres:16
  api:
    image: acme/api:latest
    build: .
  worker:
    build:
      context: worker
"""

TAG = "e535c4d89abb"


def _app(folder: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return folder


def _relay(folder: Path) -> Path:
    return _app(
        folder,
        {
            "deployment/compose.yaml": RELAY,
            "deployment/engine.Dockerfile": "FROM python:3.12\n",
            "deployment/host.Dockerfile": "FROM ubuntu\n",
            ".env": "POSTGRES_PASSWORD=hunter2-very-secret\n",
        },
    )


def _compose_spec(folder: Path, rel: str = "deployment/compose.yaml") -> dict:
    return {"kind": "compose", "files": [str(folder / rel)], "env_file": None}


# Proposing by rule


def test_relay_shape_is_two_variables(tmp_path) -> None:
    folder = _relay(tmp_path)
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    assert recipe.kind == "compose_env" and recipe.status == "proposed"
    assert (recipe.name_var, recipe.tag_var) == ("IMAGE_REGISTRY", "IMAGE_TAG")
    assert recipe.services == ["engine", "host"] and recipe.profiles == ["build"]
    assert recipe.files == ["deployment/compose.yaml"]
    assert image_names(recipe, TAG) == {
        "engine": f"evalution/relay-lite/engine:{TAG}",
        "host": f"evalution/relay-lite/host:{TAG}",
    }
    assert build_env(recipe, TAG) == {
        "IMAGE_REGISTRY": "evalution/relay-lite",
        "IMAGE_TAG": TAG,
    }
    claims = {c.at: c.says for c in recipe.evidence}
    assert (
        "engine's image is ${IMAGE_REGISTRY:-relay}/engine:${IMAGE_TAG:-local}"
        in (claims["deployment/compose.yaml:13"])
    )
    assert "deployment/host.Dockerfile:1" in claims
    assert set(recipe.evidence_hashes) == {
        "deployment/compose.yaml",
        "deployment/engine.Dockerfile",
        "deployment/host.Dockerfile",
    }
    assert "IMAGE_REGISTRY and IMAGE_TAG" in recipe.report
    assert "Your own images aren't touched" in recipe.report


def test_fixed_names_get_an_override(tmp_path) -> None:
    folder = _app(tmp_path, {"compose.yaml": FIXED, "Dockerfile": "FROM x\n"})
    recipe = propose(folder, "Acme_App", _compose_spec(folder, "compose.yaml"))
    assert recipe.kind == "compose_override" and recipe.name_var is None
    assert recipe.services == ["api", "worker"] and recipe.profiles == []
    names = image_names(recipe, TAG)
    assert names == {
        "api": f"evalution/acme-app-api:{TAG}",
        "worker": f"evalution/acme-app-worker:{TAG}",
    }
    override = json.loads(override_text(recipe, TAG) or "")
    assert override == {"services": {s: {"image": n} for s, n in names.items()}}
    assert build_env(recipe, TAG) == {}


def test_a_tag_variable_alone_or_a_shared_variable_is_an_override(tmp_path) -> None:
    only_tag = "services:\n  api:\n    image: acme/api:${TAG:-dev}\n    build: .\n"
    folder = _app(tmp_path / "a", {"compose.yaml": only_tag})
    assert propose(folder, "a", _compose_spec(folder, "compose.yaml")).kind == "compose_override"
    shared = (
        "services:\n  api:\n    image: ${REG:-acme}/api:${TAG:-dev}\n    build: .\n"
        "  cache:\n    image: ${REG:-acme}/cache:7\n"
    )
    folder = _app(tmp_path / "b", {"compose.yaml": shared})
    recipe = propose(folder, "b", _compose_spec(folder, "compose.yaml"))
    assert recipe.kind == "compose_override" and "cache" in recipe.report


def test_process_setup_comes_from_lockfiles(tmp_path) -> None:
    folder = _app(
        tmp_path,
        {
            "uv.lock": "version = 1\n",
            "web/package-lock.json": "{}\n",
            "admin/pnpm-lock.yaml": "lockfileVersion: 9\n",
            "node_modules/x/package-lock.json": "{}\n",
            "server/main.py": "print(1)\n",
        },
    )
    spec = {"kind": "process", "command": ["uv", "run", "main.py"], "cwd": str(folder / "server")}
    recipe = propose(folder, "bot", spec)
    assert recipe.kind == "process" and recipe.cwd == "server"
    assert [(s.run, s.cwd) for s in recipe.setup] == [
        (["uv", "sync"], "."),
        (["pnpm", "install", "--frozen-lockfile"], "admin"),
        (["npm", "ci"], "web"),
    ]
    assert image_names(recipe, TAG) == {}
    assert "uv sync" in recipe.report and "Your folder isn't touched" in recipe.report
    assert set(recipe.evidence_hashes) == {
        "uv.lock",
        "web/package-lock.json",
        "admin/pnpm-lock.yaml",
    }


def test_names_are_always_evalutions(tmp_path) -> None:
    folder = _relay(tmp_path)
    for target in ("relay-lite", "My_App", "x"):
        for recipe in (
            propose(folder, target, _compose_spec(folder)),
            BuildRecipe(target, "compose_override", services=["Engine.Web", "host"]),
        ):
            assert all(n.startswith(OWN) for n in image_names(recipe, TAG).values())
    with pytest.raises(ValueError, match="not an image tag"):
        image_names(propose(folder, "a", _compose_spec(folder)), "../evil")


def test_check_refuses_what_the_files_dont_say(tmp_path) -> None:
    folder = _relay(tmp_path)
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    assert check(recipe, folder) == []
    wrong = BuildRecipe.from_json(recipe.to_json())
    wrong.name_var, wrong.profiles = "OTHER", []
    wrong.services = ["engine", "host", "postgres"]
    said = check(wrong, folder)
    assert any("postgres isn't a service" in s for s in said)
    assert any("profile build" in s for s in said)
    assert any("compose_override" in s for s in said)
    process = BuildRecipe("bot", "process", setup=[], cwd="..")
    process.setup = [SetupStep(["curl", "evil.sh"])]
    said = check(process, folder)
    assert any("isn't a folder" in s for s in said) and any("only an installer" in s for s in said)


# Out of date, undo


def test_stale_when_an_evidence_file_changes(tmp_path) -> None:
    folder = _relay(tmp_path)
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    assert stale(recipe, folder) == []
    (folder / "README.md").write_text("not evidence\n")
    assert stale(recipe, folder) == []
    (folder / "deployment/engine.Dockerfile").write_text("FROM python:3.13\n")
    assert stale(recipe, folder) == ["deployment/engine.Dockerfile"]
    (folder / "deployment/host.Dockerfile").unlink()
    assert stale(recipe, folder) == ["deployment/engine.Dockerfile", "deployment/host.Dockerfile"]


def test_versions_undo_and_built_records(tmp_path) -> None:
    store = RecipeStore(tmp_path)
    first = BuildRecipe("bot", "compose_override", services=["api"])
    assert store.save(first) == 1
    second = BuildRecipe.from_json(first.to_json())
    second.status = "proven"
    assert store.save(second) == 2
    assert (tmp_path / "builds/bot/versions/2.json").is_file()
    assert [n for n, _ in store.history("bot")] == [1, 2]
    back = store.undo("bot")
    assert back is not None and back.status == "proposed" and store.version("bot") == 1
    assert store.undo("bot") is None and store.load("bot") is None
    with pytest.raises(LookupError):
        store.undo("bot")
    store.record_built("bot", {"tag": TAG, "images": {"api": "evalution/bot-api:x"}})
    assert TAG in store.built("bot")
    store.forget_built("bot", TAG)
    assert store.built("bot") == {}
    with pytest.raises(ValueError):
        store.folder("../x")


# Building, with Docker faked


class Docker:
    """Every ``_run`` and ``stream`` call, answered as Docker would."""

    def __init__(self, resolved: dict | None = None) -> None:
        self.runs: list[tuple[list[str], dict | None]] = []
        self.streamed: list[tuple[list[str], dict | None, Path | None]] = []
        self.watched: list[Path | None] = []
        self.resolved = resolved
        self.build_code = 0
        self.removed: list[str] = []
        self.context = "colima"
        self.trim_code = 0

    async def run(self, *args, env=None, limit=60, cwd=None, secrets=None):
        args = list(args)
        self.runs.append((args, env))
        if args[1:3] == ["compose", "-f"] and "config" in args:
            if self.resolved is not None:
                return 0, json.dumps({"services": self.resolved}), ""
            # As compose resolves it: the variables in env name the images.
            reg = (env or {}).get("IMAGE_REGISTRY", "relay")
            tag = (env or {}).get("IMAGE_TAG", "local")
            services = {s: {"image": f"{reg}/{s}:{tag}"} for s in ("engine", "host")}
            return 0, json.dumps({"services": services}), ""
        if args[1:3] == ["image", "inspect"]:
            return 0, f"sha256:{abs(hash(args[-1])):x}\t1500000000\n", ""
        if args[1:3] == ["image", "rm"]:
            self.removed.append(args[-1])
            return 0, "", ""
        if args[1:3] == ["image", "ls"]:
            return 0, "evalution/bot/engine:old\tabc\nrelay/engine:local\tdef\n", ""
        if args[1:3] == ["context", "show"]:
            return 0, self.context + "\n", ""
        if "fstrim" in args:
            return self.trim_code, "", "fstrim: permission denied" if self.trim_code else ""
        return 0, "", ""

    async def stream(self, args, said, *, cwd=None, env=None, secrets=None, limit=0, watch=None):
        self.streamed.append((list(args), env, cwd))
        self.watched.append(watch)
        said("#1 [engine] building")
        return self.build_code, ["#1 [engine] building", "ERROR: failed"]


@pytest.fixture
def docker(monkeypatch):
    fake = Docker()
    monkeypatch.setattr(build, "_run", fake.run)
    monkeypatch.setattr(build, "_docker", lambda: "docker")
    monkeypatch.setattr(build, "stream", fake.stream)
    monkeypatch.setattr(build.shutil, "which", lambda name: f"/usr/bin/{name}")
    frees = iter([60 * 1000**3, 40 * 1000**3])
    monkeypatch.setattr(build, "free_bytes", lambda _folder: next(frees, 40 * 1000**3))
    return fake


def test_compose_env_builds_with_two_variables_and_the_persons_env_file(tmp_path, docker) -> None:
    folder = _relay(tmp_path / "app")
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    said: list[str] = []
    built = asyncio.run(
        build.build_version(
            recipe, folder, TAG, data_dir=tmp_path / "data", env_file=folder / ".env",
            said=said.append,
        )
    )  # fmt: skip
    args, env, cwd = docker.streamed[0]
    compose = f"docker compose -f {folder}/deployment/compose.yaml --env-file {folder}/.env"
    assert " ".join(args) == (
        f"{compose} -p eval-build-relay-lite --profile build build engine host"
    )
    assert env == {"IMAGE_REGISTRY": "evalution/relay-lite", "IMAGE_TAG": TAG}
    assert cwd == folder.resolve()
    assert docker.watched == [tmp_path / "data"], "the build watches the disk it fills"
    # The names were resolved, with the same env, before anything was built.
    config = [a for a, _ in docker.runs if "config" in a]
    assert config and docker.runs[0][1] == env
    assert built.images == {
        "engine": f"evalution/relay-lite/engine:{TAG}",
        "host": f"evalution/relay-lite/host:{TAG}",
    }
    assert set(built.ids) == {"engine", "host"} and built.image_bytes == 3_000_000_000
    assert built.disk_bytes == 20 * 1000**3 and built.seconds >= 0
    assert "#1 [engine] building" in said
    assert list((tmp_path / "data/builds/relay-lite").glob("*.override.json")) == []


def test_compose_override_writes_its_override_in_evalutions_folder(tmp_path, docker) -> None:
    folder = _app(tmp_path / "app", {"compose.yaml": FIXED, "Dockerfile": "FROM x\n"})
    recipe = propose(folder, "acme", _compose_spec(folder, "compose.yaml"))
    names = image_names(recipe, TAG)
    docker.resolved = {s: {"image": n} for s, n in names.items()}
    asyncio.run(build.build_version(recipe, folder, TAG, data_dir=tmp_path / "data"))
    override = tmp_path / "data/builds/acme" / f"build-{TAG}.override.json"
    assert json.loads(override.read_text())["services"]["api"]["image"] == names["api"]
    args, env, _ = docker.streamed[0]
    assert args[:7] == [
        "docker",
        "compose",
        "-f",
        f"{folder}/compose.yaml",
        "-f",
        str(override),
        "-p",
    ]
    assert args[-3:] == ["build", "api", "worker"] and env == {}
    assert not any(str(folder) in str(p) for p in (tmp_path / "data").rglob("*.json"))
    assert {p.name for p in folder.iterdir()} == {"compose.yaml", "Dockerfile"}


def test_a_name_that_isnt_evalutions_is_refused_before_building(tmp_path, docker) -> None:
    folder = _relay(tmp_path / "app")
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    # Say the compose file hard-codes a name the variables don't reach.
    docker.resolved = {"engine": {"image": "relay/engine:local"}, "host": {"image": "x"}}
    with pytest.raises(BuildRefused, match="only into its own names"):
        asyncio.run(build.build_version(recipe, folder, TAG, data_dir=tmp_path / "data"))
    assert docker.streamed == []


def test_refused_under_five_gb_free_saying_what_to_remove(tmp_path, docker, monkeypatch) -> None:
    folder = _relay(tmp_path / "app")
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    monkeypatch.setattr(build, "free_bytes", lambda _folder: 3 * 1000**3)
    with pytest.raises(BuildRefused) as refused:
        asyncio.run(build.build_version(recipe, folder, TAG, data_dir=tmp_path / "data"))
    said = str(refused.value)
    assert "Only 3.0 GB is free" in said and "evalution/bot/engine:old" in said
    assert "relay/engine:local" not in said
    assert docker.streamed == [] and not [a for a, _ in docker.runs if "config" in a]


def test_free_bytes_reads_this_computers_disk(tmp_path) -> None:
    assert build.free_bytes(tmp_path / "not" / "made" / "yet") > 0


def test_a_failed_build_says_its_last_lines(tmp_path, docker) -> None:
    folder = _relay(tmp_path / "app")
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    docker.build_code = 1
    with pytest.raises(BuildFailed, match="ERROR: failed"):
        asyncio.run(build.build_version(recipe, folder, TAG, data_dir=tmp_path / "data"))


def test_a_process_version_is_set_up_only_in_evalutions_folder(tmp_path, docker) -> None:
    folder = _app(tmp_path / "app", {"uv.lock": "", "web/package-lock.json": "{}"})
    recipe = propose(folder, "bot", {"kind": "process", "command": ["uv", "run", "app"]})
    with pytest.raises(BuildRefused, match="never in your own folder"):
        asyncio.run(build.build_version(recipe, folder, TAG, data_dir=tmp_path / "data"))
    version = _app(
        tmp_path / "data/versions/bot/abc", {"uv.lock": "", "web/package-lock.json": "{}"}
    )
    asyncio.run(build.build_version(recipe, version, TAG, data_dir=tmp_path / "data"))
    assert docker.watched and all(w == version for w in docker.watched)
    assert [(a, c) for a, _, c in docker.streamed] == [
        (["/usr/bin/uv", "sync"], version.resolve()),
        (["/usr/bin/npm", "ci"], (version / "web").resolve()),
    ]


def test_stream_passes_lines_on_scrubbed(tmp_path) -> None:
    said: list[str] = []
    code, last = asyncio.run(
        build.stream(["sh", "-c", "echo one; echo the hunter2-secret-x; exit 3"], said.append,
                     secrets=["hunter2-secret-x"])
    )  # fmt: skip
    assert code == 3 and said[0] == "one" and "hunter2-secret-x" not in " ".join(said + last)


# Removing


def test_remove_takes_only_evalutions_tags_and_trims_colima(tmp_path, docker) -> None:
    folder = _relay(tmp_path / "app")
    recipe = propose(folder, "relay-lite", _compose_spec(folder))
    store = RecipeStore(tmp_path / "data")
    store.record_built("relay-lite", {"tag": TAG, "images": image_names(recipe, TAG)})
    done = asyncio.run(build.remove_version(recipe, TAG, tmp_path / "data"))
    assert docker.removed == list(image_names(recipe, TAG).values())
    assert all(n.startswith(OWN) for n in docker.removed)
    trim = next(a for a, _ in docker.runs if "fstrim" in a)
    assert trim == ["/usr/bin/colima", "ssh", "--", "sudo", "fstrim", "/var/lib/docker"]
    assert done.trimmed is True and store.built("relay-lite") == {}
    with pytest.raises(ValueError, match="removes only its own"):
        asyncio.run(build.remove_images(["relay/engine:local"]))
    assert "relay/engine:local" not in docker.removed


def test_trim_says_when_it_cant(docker, monkeypatch) -> None:
    docker.trim_code = 1
    trimmed, said = asyncio.run(build.trim())
    assert trimmed is False and "permission denied" in said
    docker.context = "desktop-linux"
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    trimmed, said = asyncio.run(build.trim())
    assert trimmed is None and "isn't Colima's" in said
    docker.context = "colima-work"
    asyncio.run(build.trim())
    assert next(a for a, _ in reversed(docker.runs) if "fstrim" in a)[2:4] == ["-p", "work"]


# Proving


@pytest.fixture
def config(tmp_path):
    repo = _relay(tmp_path / "app")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "targets": {
                    "bot": {
                        "adapter": "http",
                        "http": {"url": "http://127.0.0.1:8012"},
                        "code": {"path": "app"},
                    }
                }
            }
        )
    )
    return load(tmp_path / "evalution.yaml")


class FakeCopy:
    def __init__(self, spec, env, fail=None, plays=None):
        self.spec, self.env, self.fail, self.plays = spec, env, fail, plays
        self.stopped = False

    @property
    def url(self):
        return "http://127.0.0.1:9999"

    async def start(self):
        if self.fail:
            raise LaunchError(self.fail)

    async def stop(self):
        self.stopped = True
        return []

    async def images(self):
        return self.plays or []


def _prove_with(monkeypatch, config, built=None, fail=None, plays=None):
    made: list[FakeCopy] = []

    async def fake_build(recipe, source, tag, **kw):
        if isinstance(built, Exception):
            raise built
        names = image_names(recipe, tag)
        return Built(tag, str(source), names, {"engine": "sha256:1"}, 233, 19_500_000_000)

    def fake_copy(spec, target, slot, work, env=None):
        copy = FakeCopy(spec, env, fail, plays)
        made.append(copy)
        return copy

    async def answers(*_a, **_k):
        return True

    monkeypatch.setattr(build, "build_version", fake_build)
    monkeypatch.setattr(build, "copy_of", fake_copy)
    monkeypatch.setattr(build, "ComposeCopy", FakeCopy)
    monkeypatch.setattr(build, "_healthy", answers)
    folder = config.targets["bot"].code.path
    recipe = propose(folder, "bot", _compose_spec(folder))
    stages: list[str] = []
    proved = asyncio.run(build.prove(config, "bot", recipe, on_stage=stages.append))
    return proved, made, stages


def test_prove_builds_starts_a_copy_with_the_variables_and_takes_it_down(
    config, monkeypatch
) -> None:
    from evalution.core.version import Image

    plays = [Image("engine", "evalution/bot/engine:x", "sha256:1", time.time(), True)]
    proved, made, stages = _prove_with(monkeypatch, config, plays=plays)
    assert proved.status == "proven", proved.report
    assert stages == ["build", "copy", "probe", "stop"]
    (copy,) = made
    assert copy.env["IMAGE_REGISTRY"] == "evalution/bot" and copy.env["IMAGE_TAG"].startswith(
        "disk-"
    )
    assert copy.stopped and proved.last_build["disk_bytes"] == 19_500_000_000
    assert "Proved on" in proved.report


def test_prove_fails_when_the_copy_plays_the_persons_images(config, monkeypatch) -> None:
    from evalution.core.version import Image

    plays = [Image("engine", "relay/engine:local", "sha256:2", time.time(), True)]
    proved, made, _ = _prove_with(monkeypatch, config, plays=plays)
    assert proved.status == "failed" and "not the image just built" in proved.report
    assert made[0].stopped


def test_prove_fails_when_the_copy_doesnt_start_or_the_build_stops(config, monkeypatch) -> None:
    proved, made, _ = _prove_with(monkeypatch, config, fail="engine exited")
    assert proved.status == "failed" and "engine exited" in proved.report and made[0].stopped
    proved, made, _ = _prove_with(monkeypatch, config, built=BuildFailed("ERROR: no space"))
    assert proved.status == "failed" and "no space" in proved.report and made == []
    with pytest.raises(BuildRefused):
        _prove_with(monkeypatch, config, built=BuildRefused("Only 3 GB"))


# The page's API


class FakeProve:
    def __init__(self) -> None:
        self.go = False
        self.calls = 0
        self.outcome = "proven"

    async def __call__(self, config, target, recipe, *, said, on_stage):
        self.calls += 1
        on_stage("build")
        said("#5 [engine 3/9] RUN uv sync")
        while not self.go:  # noqa: ASYNC110 (set from the test's thread)
            await asyncio.sleep(0.01)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        on_stage("copy")
        on_stage("probe")
        on_stage("stop")
        recipe.status = self.outcome
        recipe.report += (
            "\n\nNot proven: the copy didn't answer" if self.outcome == "failed" else ""
        )
        recipe.last_build = {"tag": "disk-1", "seconds": 158, "disk_bytes": 9_400_000_000}
        return recipe


@pytest.fixture
def client(config, monkeypatch):
    fake = FakeProve()
    monkeypatch.setattr(building.Builds, "__init__", lambda self, prove=None: _init(self, fake))
    monkeypatch.setattr(build, "free_bytes", lambda _folder: 60 * 1000**3)
    with TestClient(create_app(config)) as made:
        made.fake = fake  # type: ignore[attr-defined]
        yield made


def _init(self, prove) -> None:
    self.prove, self.jobs, self._tasks = prove, {}, {}


def _until_ended(client) -> dict:
    for _ in range(300):
        found = client.get("/v1/targets/bot/builds").json()
        if found["job"] and found["job"]["state"] != "running":
            return found
        time.sleep(0.02)
    raise AssertionError("the job never ended")


def test_api_propose_prove_undo(client) -> None:
    found = client.get("/v1/targets/bot/builds").json()
    assert found["status"] == "none" and found["recipe"] is None and found["can_propose"]
    assert not found["can_prove"] and "up to 20 GB" in found["cost"]

    proposed = client.post("/v1/targets/bot/builds/propose").json()
    assert proposed["status"] == "proposed" and proposed["version"] == 1
    recipe = proposed["recipe"]
    assert recipe["kind"] == "compose_env" and recipe["name_var"] == "IMAGE_REGISTRY"
    assert recipe["images"]["engine"] == "evalution/bot/engine:<commit>"
    assert any(e["at"] == "deployment/compose.yaml:13" for e in recipe["evidence"])
    # The same again changes nothing.
    assert client.post("/v1/targets/bot/builds/propose").json()["version"] == 1

    refused = client.post("/v1/targets/bot/builds/prove", json={})
    assert refused.status_code == 400 and "confirm" in refused.json()["detail"]
    started = client.post("/v1/targets/bot/builds/prove", json={"confirm": True})
    assert started.status_code == 202, started.text
    assert started.json()["job"]["steps"][0] == {
        "key": "build", "label": "Build bot's code into evalution's names", "state": "running",
        "seconds": None, "detail": None,
    }  # fmt: skip
    assert client.post("/v1/targets/bot/builds/prove", json={"confirm": True}).status_code == 409
    assert client.post("/v1/targets/bot/builds/undo").status_code == 409
    for _ in range(100):
        if client.get("/v1/targets/bot/builds").json()["job"]["lines"]:
            break
        time.sleep(0.02)
    assert client.get("/v1/targets/bot/builds").json()["job"]["lines"] == [
        "#5 [engine 3/9] RUN uv sync"
    ]
    client.fake.go = True
    found = _until_ended(client)
    assert found["status"] == "proven" and found["version"] == 2
    assert [s["state"] for s in found["job"]["steps"]] == ["done"] * 4
    assert found["last_build"]["seconds"] == 158
    assert found["cost"] == "builds bot's code once: about 3 min and up to 10 GB"

    # A changed Dockerfile makes it out of date.
    folder = Path(client.app.state.workspaces.get(LOCAL).targets.code("bot").root)
    (folder / "deployment/engine.Dockerfile").write_text("FROM python:3.13\n")
    found = client.get("/v1/targets/bot/builds").json()
    assert found["status"] == "stale" and found["stale_files"] == ["deployment/engine.Dockerfile"]

    undone = client.post("/v1/targets/bot/builds/undo").json()
    assert undone["status"] == "proposed" and undone["version"] == 1


def test_api_a_failed_proof_marks_the_step_and_says_why(client) -> None:
    client.post("/v1/targets/bot/builds/propose")
    client.fake.outcome, client.fake.go = "failed", True
    client.post("/v1/targets/bot/builds/prove", json={"confirm": True})
    found = _until_ended(client)
    assert found["status"] == "failed" and "didn't answer" in found["job"]["error"]
    assert {s["key"]: s["state"] for s in found["job"]["steps"]}["probe"] == "failed"


def test_api_a_refused_build_is_no_verdict(client) -> None:
    client.post("/v1/targets/bot/builds/propose")
    client.fake.outcome, client.fake.go = BuildRefused("Only 3.0 GB is free"), True
    client.post("/v1/targets/bot/builds/prove", json={"confirm": True})
    found = _until_ended(client)
    assert found["job"]["state"] == "failed" and "3.0 GB" in found["job"]["error"]
    assert found["status"] == "proposed"


def test_api_refusals(client, monkeypatch) -> None:
    assert client.post("/v1/targets/bot/builds/undo").status_code == 409
    assert client.post("/v1/targets/bot/builds/prove", json={"confirm": True}).status_code == 400
    client.post("/v1/targets/bot/builds/propose")
    monkeypatch.setattr(build, "free_bytes", lambda _folder: 2 * 1000**3)
    low = client.post("/v1/targets/bot/builds/prove", json={"confirm": True})
    assert low.status_code == 400 and "5 GB" in low.json()["detail"]
    monkeypatch.setattr(build, "free_bytes", lambda _folder: 60 * 1000**3)
    runs = client.app.state.runs
    monkeypatch.setattr(runs, "busy", lambda: True)
    busy = client.post("/v1/targets/bot/builds/prove", json={"confirm": True})
    assert busy.status_code == 409 and "A run is going" in busy.json()["detail"]
    monkeypatch.setattr(runs, "busy", lambda: False)
    nothing = client.post("/v1/targets/bot/builds/remove", json={"tag": "disk-1"})
    assert nothing.status_code == 400 and "Nothing was built" in nothing.json()["detail"]
    client.app.state.hosted = True
    for method, path in [
        ("get", "/v1/targets/bot/builds"),
        ("post", "/v1/targets/bot/builds/propose"),
        ("post", "/v1/targets/bot/builds/prove"),
        ("post", "/v1/targets/bot/builds/undo"),
        ("post", "/v1/targets/bot/builds/remove"),
    ]:
        refused = getattr(client, method)(
            path, **({"json": {"confirm": True}} if method == "post" else {})
        )
        assert refused.status_code == 400 and "hosted" in refused.json()["detail"]
    assert client.fake.calls == 0


def test_api_remove_a_built_version(client, monkeypatch) -> None:
    client.post("/v1/targets/bot/builds/propose")
    store = RecipeStore(client.app.state.workspaces.get(LOCAL).targets.config.data_dir)
    store.record_built(
        "bot", {"tag": "disk-1", "images": {"engine": "evalution/bot/engine:disk-1"}}
    )
    removed: list[list[str]] = []

    async def fake_remove(names):
        removed.append(names)
        return build.Removed(removed=names, trimmed=True, note="Trimmed Colima's disk")

    monkeypatch.setattr(build, "remove_images", fake_remove)
    found = client.post("/v1/targets/bot/builds/remove", json={"tag": "disk-1"}).json()
    assert removed == [["evalution/bot/engine:disk-1"]] and found["built"] == []
    assert found["removed"]["trimmed"] is True


# The test writer


def test_writer_reads_and_adjusts_the_recipe(config) -> None:
    targets = Targets(config)
    session = {"target": "bot"}
    read = asyncio.run(writer_building.read(targets, session))
    assert read["saved"] is None and read["rule_proposes"]["kind"] == "compose_env"
    refused = asyncio.run(
        writer_building.propose_recipe(
            targets, session, {"kind": "compose_env", "name_var": "IMAGE_TAG", "tag_var": "X"}
        )
    )
    assert refused.startswith("Refused") and "compose_override" in refused
    refused = asyncio.run(writer_building.propose_recipe(targets, session, {"kind": "process"}))
    assert refused.startswith("Refused") and "docker compose" in refused
    saved = asyncio.run(
        writer_building.propose_recipe(
            targets, session, {"kind": "compose_override", "services": ["engine"], "why": "host"}
        )
    )
    assert saved["saved_as_version"] == 1 and saved["status"] == "proposed"
    kept = RecipeStore(config.data_dir).load("bot")
    assert kept is not None and kept.by == "agent" and kept.services == ["engine"]
    assert kept.profiles == [] and kept.kind == "compose_override"
    again = asyncio.run(
        writer_building.propose_recipe(
            targets, session, {"kind": "compose_override", "services": ["engine"]}
        )
    )
    assert "already builds this way" in again


# The disk while a build runs


_SLOW = [
    "python3",
    "-c",
    "import time\nfor i in range(400):\n    print(i, flush=True)\n    time.sleep(0.01)",
]


def test_a_build_that_fills_the_disk_is_stopped_while_it_runs(tmp_path, monkeypatch) -> None:
    """The check before a build isn't enough: a build from cache still added 6.5 GB and left
    2.9 GB free. Below the limit while it runs, the build is stopped and says why."""
    free = iter([build.MIN_FREE + 10**9, build.MIN_FREE - 10**9])
    monkeypatch.setattr(build, "DISK_EVERY", 0.05)
    monkeypatch.setattr(build, "free_bytes", lambda _folder: next(free, build.MIN_FREE - 10**9))

    async def nothing_built() -> list[tuple[str, int]]:
        return []

    monkeypatch.setattr(build, "own_images", nothing_built)
    lines: list[str] = []
    with pytest.raises(build.DiskLow) as stopped:
        asyncio.run(build.stream(_SLOW, lines.append, watch=tmp_path))
    said = str(stopped.value)
    assert said.startswith("Stopped the build: only 4.4 GB was left free")
    assert "stops a build below 5.4 GB" in said and "free space elsewhere" in said
    assert isinstance(stopped.value, BuildFailed), "a stopped build is a failed build: unjudged"
    assert len(lines) < 400 and lines[-1] == "stopped: only 4.4 GB left free"


def test_a_build_with_room_runs_to_its_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(build, "DISK_EVERY", 0.05)
    monkeypatch.setattr(build, "free_bytes", lambda _folder: build.MIN_FREE + 10**9)
    lines: list[str] = []
    code, last = asyncio.run(build.stream(_SLOW, lines.append, watch=tmp_path))
    assert code == 0 and len(lines) == 400 and last[-1] == "399"
