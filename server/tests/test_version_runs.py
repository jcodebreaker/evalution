"""A run of a version other than what's on disk (ADR 0011, plan phase 4), below the runner: refused
before anything starts without a proven recipe, checked out with real git in throwaway repos,
built with Docker faked, its copy started from the worktree's files with the recipe's names, a
built tag reused, a failed build said with its last lines, the version block vouched for, the
cost said before Run, a remote's branches listed with ls-remote, and the person's checkout
byte-identical throughout. The runner's own run of a version is in
``proof/test_runs_of_a_version``."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.adapters.base import Capabilities, Isolation
from evalution.api.app import create_app
from evalution.config import load
from evalution.core.version import Image, built, described, same_played, set_up
from evalution.isolation import build
from evalution.isolation.build import Built
from evalution.isolation.plan import Plan, PlannedStore, PlanStore, hashes
from evalution.isolation.recipe import BuildRecipe, RecipeStore, SetupStep, propose
from evalution.project import building, versions
from evalution.project.versions import VersionError, Versions
from evalution.runs import isolated, versioned

ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

COMPOSE = """\
services:
  postgres:
    image: postgres:16-alpine
  engine:
    image: ${IMAGE_REGISTRY:-relay}/engine:${IMAGE_TAG:-local}
    build:
      context: ..
      dockerfile: deployment/engine.Dockerfile
"""
FIXED = """\
services:
  api:
    image: acme/api:latest
    build: ..
"""
DB = "DATABASE_URL = 'postgres://db'\n"


def git(folder: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(folder), *args], capture_output=True, text=True, env=ENV, check=True
    )
    return done.stdout


def commit(folder: Path, files: dict[str, str], message: str = "change") -> str:
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(folder, "add", rel)
    git(folder, "commit", "-q", "-m", message)
    return git(folder, "rev-parse", "HEAD").strip()


def state(folder: Path) -> str:
    """The person's checkout as a hash: index, status, refs, stash, HEAD and every file."""
    git_dir = Path(git(folder, "rev-parse", "--absolute-git-dir").strip())
    parts = [
        (git_dir / "index").read_bytes(),
        git(
            folder, "--no-optional-locks", "status", "--porcelain=v2", "--branch", "-uall"
        ).encode(),
        git(
            folder, "for-each-ref", "refs/heads", "refs/remotes", "refs/tags", "refs/stash"
        ).encode(),
        (git_dir / "HEAD").read_bytes(),
        *(
            p.read_bytes()
            for p in sorted(folder.rglob("*"))
            if p.is_file() and ".git" not in p.parts
        ),
    ]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


@pytest.fixture(autouse=True)
def _plain_git(monkeypatch):
    for key, value in ENV.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)


@pytest.fixture
def repo(tmp_path):
    """An origin, and the person's clone on ``main`` with an uncommitted change and a git-ignored
    ``.env``; ``feature`` changes the compose file and the database file the reset plan cites;
    ``remote-only`` is pushed after the clone."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    commit(
        seed,
        {
            "deployment/compose.yaml": COMPOSE,
            "deployment/engine.Dockerfile": "FROM python:3.12\n",
            "app/db.py": DB,
            "app/serve.py": "print('main')\n",
            ".gitignore": ".env\n",
        },
    )
    git(seed, "push", "-q", "origin", "HEAD:main")
    git(seed, "checkout", "-q", "-b", "feature")
    feature = commit(
        seed,
        {
            "app/serve.py": "print('feature')\n",
            "deployment/compose.yaml": COMPOSE + "  # feature\n",
        },
    )
    git(seed, "push", "-q", "origin", "feature")
    person = tmp_path / "person"
    git(tmp_path, "clone", "-q", str(origin), str(person))
    git(person, "branch", "-q", "feature", "origin/feature")
    (person / ".env").write_text("POSTGRES_PASSWORD=hunter2\n")
    (person / "app" / "serve.py").write_text("print('work in progress')\n")
    git(seed, "checkout", "-q", "-b", "remote-only", "main")
    only = commit(seed, {"app/serve.py": "print('remote only')\n"})
    git(seed, "push", "-q", "origin", "remote-only")
    return {"person": person, "feature": feature, "remote_only": only, "origin": origin}


def _config(tmp_path: Path, person: Path):
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "targets": {
                    "app": {
                        "adapter": "http",
                        "url": "http://127.0.0.1:8012",
                        "http": {"send": {"url": "{url}/chat"}, "reply": "$.reply"},
                        "code": {"path": str(person)},
                    }
                }
            }
        )
    )
    return load(tmp_path / "evalution.yaml")


def _plan(person: Path, launch: dict) -> Plan:
    store = PlannedStore("db", "postgres", "eval-app-0-postgres-1:app", strategy="relocate")
    store.evidence = ["app/db.py:1"]
    return Plan(
        target="app",
        stores=[store],
        launch=launch,
        status="proven",
        evidence_hashes=hashes(person, store.evidence),
    )


def _compose(person: Path) -> dict:
    return {
        "kind": "compose",
        "files": [str(person / "deployment" / "compose.yaml")],
        "env_file": str(person / ".env"),
        "app_url": "http://127.0.0.1:8012",
        "health": None,
    }


@pytest.fixture
def setup(tmp_path, repo):
    """The person's app with a proven reset plan and a proven compose_env recipe."""
    person = repo["person"]
    config = _config(tmp_path, person)
    PlanStore(config.data_dir).save(_plan(person, _compose(person)))
    recipe = propose(person, "app", _compose(person))
    recipe.status = "proven"
    RecipeStore(config.data_dir).save(recipe)
    return config


def _found(config) -> isolated.Footing:
    return isolated.footing(config, "app", Capabilities(Isolation.SESSION, ""))


class Docker:
    """Docker, faked: compose resolves the images from the variables it is given, a build
    prints a line, images are there once built."""

    def __init__(self) -> None:
        self.builds: list[tuple[list[str], dict | None, Path | None]] = []
        self.build_code = 0
        self.images: set[str] = set()
        self.removed: list[str] = []

    async def run(self, *args, env=None, limit=60, cwd=None, secrets=None):
        args = list(args)
        if args[1:2] == ["compose"] and "config" in args:
            reg = (env or {}).get("IMAGE_REGISTRY", "relay")
            tag = (env or {}).get("IMAGE_TAG", "local")
            return 0, json.dumps({"services": {"engine": {"image": f"{reg}/engine:{tag}"}}}), ""
        if args[1:3] == ["image", "inspect"]:
            if args[-1] in self.images:
                return 0, f"sha256:{hashlib.sha256(args[-1].encode()).hexdigest()}\t1500\n", ""
            return 1, "", "No such image"
        if args[1:3] == ["image", "rm"]:
            self.removed.append(args[-1])
            self.images.discard(args[-1])
            return 0, "", ""
        return 0, "", ""

    async def stream(self, args, said, *, cwd=None, env=None, secrets=None, limit=0, watch=None):
        self.builds.append((list(args), env, cwd))
        said("#1 [engine] building")
        if self.build_code:
            return self.build_code, ["#1 [engine] building", "ERROR: pip install failed"]
        reg, tag = (env or {}).get("IMAGE_REGISTRY"), (env or {}).get("IMAGE_TAG")
        self.images.add(f"{reg}/engine:{tag}")
        return 0, ["#1 [engine] done"]


@pytest.fixture
def docker(monkeypatch):
    fake = Docker()
    monkeypatch.setattr(build, "_run", fake.run)
    monkeypatch.setattr(build, "_docker", lambda: "docker")
    monkeypatch.setattr(build, "stream", fake.stream)
    monkeypatch.setattr(build, "free_bytes", lambda _folder: 60 * 1000**3)

    async def trim():
        return None, ""

    monkeypatch.setattr(build, "trim", trim)
    return fake


# Refused before anything starts


def test_a_version_without_a_proven_recipe_is_refused_with_the_next_step(setup) -> None:
    config = setup
    found = _found(config)
    assert versioned.refusal(config, "app", "disk", found) is None
    assert versioned.refusal(config, "app", "feature", found) is None
    store = RecipeStore(config.data_dir)
    recipe = store.load("app")
    assert recipe is not None
    recipe.status = "proposed"
    store.save(recipe)
    said = versioned.refusal(config, "app", "feature", found)
    assert said is not None
    assert said.endswith("Prove how app is built first, on Test environment.")
    store.undo("app")
    store.undo("app")
    said = versioned.refusal(config, "app", "feature", found)
    assert said is not None and "doesn't know how to build app" in said
    # The person's own app, as it runs, can't be another version.
    said = versioned.refusal(config, "app", "feature", isolated.Footing("none"))
    assert said is not None and "plays only on evalution's own copy of app" in said
    assert versioned.refusal(config, "app", "disk", isolated.Footing("none")) is None


# Checked out, built, started from the worktree


def test_a_version_plays_the_worktrees_compose_files_and_the_persons_env_file(
    setup, repo, docker
) -> None:
    config, person = setup, repo["person"]
    before = state(person)
    lines: list[str] = []
    steps: list[str] = []
    ready = asyncio.run(
        versioned.prepare(
            config, "app", "feature", _found(config).plan, said=lines.append, on_step=steps.append
        )
    )
    tag = repo["feature"][:12]
    worktree = (config.data_dir / "versions" / "app" / tag).resolve()
    assert ready.commit == repo["feature"] and ready.name == "feature" and not ready.reused
    assert ready.folder == worktree
    assert ready.spec["files"] == [str(worktree / "deployment" / "compose.yaml")]
    assert ready.spec["files"][0].endswith("compose.yaml")
    assert (worktree / "deployment" / "compose.yaml").read_text().endswith("# feature\n")
    # The env file stays the person's: a worktree has no .env.
    assert ready.spec["env_file"] == str(person / ".env")
    assert ready.env == {"IMAGE_REGISTRY": "evalution/app", "IMAGE_TAG": tag}
    [(args, env, cwd)] = docker.builds
    assert args[args.index("-f") + 1] == str(worktree / "deployment" / "compose.yaml")
    assert env == ready.env and cwd == worktree
    assert lines[-1] == "#1 [engine] building"
    assert steps == ["Checking out feature", f"Building feature @ {tag[:7]}"]
    record = RecipeStore(config.data_dir).built("app")[tag]
    assert record["commit"] == repo["feature"] and record["images"] == {
        "engine": f"evalution/app/engine:{tag}"
    }
    assert state(person) == before

    # Built already: not built again, and the worktree is the same.
    again = asyncio.run(versioned.prepare(config, "app", "feature", _found(config).plan))
    assert again.reused and len(docker.builds) == 1 and again.folder == worktree
    # Its images gone from this computer: built again.
    docker.images.clear()
    third = asyncio.run(versioned.prepare(config, "app", "feature", _found(config).plan))
    assert not third.reused and len(docker.builds) == 2
    assert state(person) == before


def test_a_fixed_names_app_gets_its_override_after_the_worktrees_files(
    tmp_path, repo, docker, monkeypatch
) -> None:
    person = repo["person"]
    commit(person, {"deployment/fixed.yaml": FIXED})
    config = _config(tmp_path, person)
    spec = {**_compose(person), "files": [str(person / "deployment" / "fixed.yaml")]}
    PlanStore(config.data_dir).save(_plan(person, spec))
    recipe = propose(person, "app", spec)
    assert recipe.kind == "compose_override"
    recipe.status = "proven"
    RecipeStore(config.data_dir).save(recipe)
    head = git(person, "rev-parse", "HEAD").strip()

    async def fake_build(recipe, source, tag, **kw):
        build.override_file(kw["data_dir"], "app", tag).parent.mkdir(parents=True, exist_ok=True)
        build.override_file(kw["data_dir"], "app", tag).write_text("{}")
        return Built(tag, str(source), {"api": f"evalution/app-api:{tag}"}, {"api": "sha256:1"})

    monkeypatch.setattr(build, "build_version", fake_build)
    ready = asyncio.run(versioned.prepare(config, "app", head, _found(config).plan))
    worktree = config.data_dir.resolve() / "versions" / "app" / head[:12]
    assert ready.name is None, "a bare commit names no branch"
    assert ready.spec["files"] == [
        str(worktree / "deployment" / "fixed.yaml"),
        str(build.override_file(config.data_dir, "app", head[:12])),
    ]
    assert ready.env == {}


def test_a_process_version_starts_in_the_worktree(tmp_path, repo, docker, monkeypatch) -> None:
    person = repo["person"]
    config = _config(tmp_path, person)
    launch = {
        "kind": "process",
        "command": ["/usr/bin/python3", str(person / "app" / "serve.py"), "{port}"],
        "cwd": str(person / "app"),
        "roots": [str(person / "app" / "data")],
        "health": None,
    }
    plan = _plan(person, launch)
    plan.stores = [PlannedStore("files", "dir", str(person / "app" / "data"), strategy="snapshot")]
    PlanStore(config.data_dir).save(plan)
    RecipeStore(config.data_dir).save(
        BuildRecipe(
            target="app",
            kind="process",
            setup=[SetupStep(["uv", "sync"], "app")],
            command=launch["command"],
            status="proven",
        )
    )
    monkeypatch.setattr(build.shutil, "which", lambda name: f"/usr/bin/{name}")
    before = state(person)
    ready = asyncio.run(versioned.prepare(config, "app", "feature", _found(config).plan))
    worktree = ready.folder
    assert worktree.is_relative_to(config.data_dir.resolve())
    assert ready.spec["cwd"] == str(worktree / "app")
    # The interpreter isn't in the checkout: it stays; the app's own file is the version's.
    assert ready.spec["command"] == [
        "/usr/bin/python3",
        str(worktree / "app" / "serve.py"),
        "{port}",
    ]
    assert ready.spec["roots"] == [str(worktree / "app" / "data")]
    assert ready.plan.stores[0].locator == str(worktree / "app" / "data")
    # Its setup ran in the worktree, never in the person's folder.
    [(args, _env, cwd)] = docker.builds
    assert args == ["/usr/bin/uv", "sync"] and cwd == worktree / "app"
    assert (worktree / "app" / "serve.py").read_text() == "print('feature')\n"
    assert state(person) == before
    version = asyncio.run(versioned.played(ready, object()))  # type: ignore[arg-type]
    assert version.played == {
        "kind": "tree",
        "code": f"commit:{repo['feature']}",
        "folder": str(worktree),
    }
    assert version.verified is True and version.dirty is False


def test_a_failed_build_says_its_last_lines_and_what_was_asked(setup, repo, docker) -> None:
    config = setup
    docker.build_code = 1
    with pytest.raises(versioned.NotBuilt) as caught:
        asyncio.run(versioned.prepare(config, "app", "feature", _found(config).plan))
    said = str(caught.value)
    assert said.startswith(f"feature @ {repo['feature'][:7]} wasn't built. The build stopped:")
    assert said.endswith("ERROR: pip install failed")
    version = caught.value.version
    assert (version.ref, version.commit, version.played, version.verified) == (
        "feature",
        repo["feature"],
        None,
        None,
    )
    assert RecipeStore(config.data_dir).built("app") == {}
    with pytest.raises(versioned.NotBuilt, match="nope couldn't be checked out: nope: no branch"):
        asyncio.run(versioned.prepare(config, "app", "nope", _found(config).plan))


def test_a_remote_branch_not_here_is_fetched_into_evalutions_refs_only(setup, repo, docker) -> None:
    config, person = setup, repo["person"]
    before = state(person)
    ready = asyncio.run(versioned.prepare(config, "app", "origin/remote-only", _found(config).plan))
    assert ready.commit == repo["remote_only"] and ready.name == "origin/remote-only"
    assert "remote-only" not in git(person, "for-each-ref", "refs/heads", "refs/remotes")
    assert state(person) == before


# The reset plan on the version


def test_the_reset_plan_is_rehearsed_on_the_version_when_its_files_differ(
    setup, repo, docker, monkeypatch
) -> None:
    config, person = setup, repo["person"]
    found = _found(config)
    rehearsed: list[dict] = []

    async def rehearse(config, target, plan, *, slot=0, spec=None, env=None, folder=None):
        rehearsed.append({"spec": spec, "env": env, "folder": folder})
        plan.status = "proven"
        return plan

    monkeypatch.setattr(isolated, "rehearse_plan", rehearse)
    ready = asyncio.run(versioned.prepare(config, "app", "feature", found.plan))
    on = asyncio.run(versioned.footing_on(config, "app", found, ready))
    assert on.managed and rehearsed == [] and ready.notes == []

    git(person, "stash", "-q")
    git(person, "checkout", "-q", "-b", "db-change")
    commit(person, {"app/db.py": "DATABASE_URL = 'sqlite:///x'\n"})
    git(person, "checkout", "-q", "main")
    git(person, "stash", "pop", "-q")
    ready = asyncio.run(versioned.prepare(config, "app", "db-change", found.plan))
    on = asyncio.run(versioned.footing_on(config, "app", found, ready))
    assert on.managed and len(rehearsed) == 1
    assert rehearsed[0]["spec"] == ready.spec and rehearsed[0]["env"] == ready.env
    assert ready.notes == [
        "The reset plan was proven on another version (app/db.py differ in "
        f"db-change @ {ready.commit[:7]}): rehearsed again on this version's copy before the "
        "first try, and it held."
    ]
    # Not saved: the disk's plan stays as it was.
    assert PlanStore(config.data_dir).version("app") == 1

    async def fails(config, target, plan, **_kw):
        plan.status = "failed"
        plan.report = "Rehearsed: the probe was found in eval-app-0-cache-1"
        return plan

    monkeypatch.setattr(isolated, "rehearse_plan", fails)
    with pytest.raises(isolated.NotIsolated, match="doesn't hold on db-change"):
        asyncio.run(versioned.footing_on(config, "app", found, ready))


# What it records, and says before Run


def test_the_version_block_of_a_built_version() -> None:
    images = [
        Image("engine", "evalution/app/engine:abc", "sha256:1", 1.0, True),
        Image("postgres", "postgres:16", "sha256:9", 1.0, False),
    ]
    version = built("feature", "feature", "a1b2c3d4e5f6", images, {"engine": "sha256:1"})
    assert (version.ref, version.branch, version.dirty, version.verified) == (
        "feature",
        "feature",
        False,
        True,
    )
    assert version.played == {"kind": "images", "images": [i.as_dict() for i in images]}
    assert described(version.as_dict()) == "feature @ a1b2c3d"
    assert described({**version.as_dict(), "branch": None}) == "a1b2c3d"
    other = built("feature", "feature", "a1b2c3d4e5f6", images, {"engine": "sha256:2"})
    assert other.verified is False
    assert "evalution/app/engine:abc was not what evalution built" in (other.why_unverified or "")
    persons = [Image("engine", "relay/engine:local", "sha256:1", 1.0, True)]
    assert built("x", "x", "a1b2c3d4e5f6", persons).verified is False
    # Two runs of one version played the same thing.
    assert same_played(version.as_dict(), version.as_dict()) is True
    tree = set_up("v1", "v1", "a1b2c3d4e5f6", "/data/versions/app/a1b2c3d4e5f6").as_dict()
    assert same_played(tree, tree) is True


def test_the_estimate_says_the_build_and_its_cost_or_built_already(setup, repo) -> None:
    config = setup
    short = repo["feature"][:7]
    said = versioned.preview(config, "app", "feature")
    assert said["commit"] == repo["feature"] and said["said"] == f"feature @ {short}"
    assert said["cost"] == (
        f"Checks out feature @ {short} and builds it: about 4 min and up to 20 GB, once. "
        "Your checkout isn't touched."
    )
    store = RecipeStore(config.data_dir)
    store.record_built(
        "app",
        {"tag": "older", "seconds": 158, "disk_bytes": 9_400_000_000, "images": {}, "at": 5},
    )
    assert (
        "about 3 min and up to 10 GB, once" in versioned.preview(config, "app", "feature")["cost"]
    )
    store.record_built("app", {"tag": repo["feature"][:12], "images": {}, "at": 6})
    assert versioned.preview(config, "app", "feature")["cost"] == (
        f"Built already: feature @ {short}. Your checkout isn't touched."
    )
    later = versioned.preview(config, "app", "origin/remote-only")
    assert later["commit"] is None
    assert later["cost"].startswith("Fetches origin/remote-only, checks it out and builds it")
    with pytest.raises(versioned.VersionRefused, match="no branch, tag or commit"):
        versioned.preview(config, "app", "nope")


# A remote's branches, and what's built counted with its version


def test_a_remotes_branches_are_listed_with_ls_remote_and_nothing_fetched(repo, tmp_path) -> None:
    person = repo["person"]
    before = state(person)
    found = Versions("app", person, tmp_path / "data")
    listed = {b.name: b for b in found.remote_branches("origin")}
    assert set(listed) == {"main", "feature", "remote-only"}
    assert listed["remote-only"].commit == repo["remote_only"] and not listed["remote-only"].fetched
    assert state(person) == before
    found.fetch("origin", "remote-only")
    fetched = {b.name: b for b in found.remote_branches("origin")}["remote-only"]
    assert fetched.fetched and fetched.fetched_commit == repo["remote_only"]
    assert fetched.as_dict()["ref"] == "origin/remote-only"
    with pytest.raises(VersionError, match="nope: not a remote of person"):
        found.remote_branches("nope")
    git(person, "remote", "add", "gone", str(tmp_path / "no-such-repo.git"))
    with pytest.raises(VersionError, match=r"Listing gone's branches failed: .*git ls-remote gone"):
        found.remote_branches("gone")


def test_what_was_built_counts_with_its_version_and_goes_with_it(setup, repo, docker) -> None:
    config = setup
    ready = asyncio.run(versioned.prepare(config, "app", "feature", _found(config).plan))
    store = RecipeStore(config.data_dir)
    tag = ready.commit[:12]
    store.record_built("app", {**store.built("app")[tag], "disk_bytes": 7_000_000_000})
    found = versions.of(
        "app",
        repo["person"],
        config.data_dir,
        parts=[building.BuiltPart(config.data_dir, "app")],
    )
    [kept] = found.kept()
    assert kept.size > 7_000_000_000 and kept.parts == {"BuiltPart": 7_000_000_000}
    found.remove(ready.commit)
    assert docker.removed == [f"evalution/app/engine:{tag}"]
    assert store.built("app") == {} and found.kept() == []


# Through the API


def test_api_lists_a_remotes_branches_and_estimates_a_version(setup, repo, monkeypatch) -> None:
    config = setup
    from evalution.storage import Database
    from evalution.suite.store import save_case, save_set

    docs = Database.open(config.data_dir).docs()
    save_set(docs, "app", "main", {"name": "Main"})
    save_case(docs, "app", "main", {"name": "Hello", "turns": [{"message": "hi"}]})
    client = TestClient(create_app(config))
    found = client.get("/v1/targets/app/versions/remote")
    assert found.status_code == 200, found.text
    assert found.json()["remote"] == "origin"
    assert {b["name"] for b in found.json()["branches"]} == {"main", "feature", "remote-only"}
    estimate = client.post(
        "/v1/evals/runs/estimate", json={"target": "app", "set": "main", "ref": "feature"}
    )
    assert estimate.status_code == 200, estimate.text
    said = estimate.json()
    assert said["ref"] == "feature"
    assert said["version"]["said"] == f"feature @ {repo['feature'][:7]}"
    assert said["version"]["cost"].startswith(f"Checks out feature @ {repo['feature'][:7]}")
    RecipeStore(config.data_dir).undo("app")
    refused = client.post(
        "/v1/evals/runs/estimate", json={"target": "app", "set": "main", "ref": "feature"}
    )
    assert refused.status_code == 400
    assert refused.json()["detail"].endswith("Prove how app is built first, on Test environment.")
    disk = client.post("/v1/evals/runs/estimate", json={"target": "app", "set": "main"})
    assert disk.status_code == 200 and disk.json()["ref"] == "disk"
    assert disk.json()["version"]["said"].startswith("what's on disk now")
