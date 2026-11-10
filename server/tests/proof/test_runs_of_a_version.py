"""The runner playing a version other than what's on disk (ADR 0011, plan phase 4), on the rig: its
copy starts in evalution's worktree of the branch, never the person's folder; a version built
before is reused; a version that isn't built ends the run before any try, unjudged and never a
fail; one with no proven recipe is refused before it starts. Real git in a throwaway repo; the
copy is the rig's own small server, so no Docker."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import scripted_agent
from scripted_agent import COPY_APP, SET, TARGET

from evalution.isolation import build
from evalution.isolation.build import BuildFailed
from evalution.isolation.plan import PlanStore
from evalution.isolation.recipe import BuildRecipe, RecipeStore
from evalution.results.views import detail
from evalution.runs import isolated, versioned

MEMORY_FILE = "app-data/memory.json"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(folder: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(folder), *args], capture_output=True, text=True, env=ENV, check=True
    )
    return done.stdout


def fingerprint(folder: Path) -> str:
    git_dir = Path(git(folder, "rev-parse", "--absolute-git-dir").strip())
    parts = [
        (git_dir / "index").read_bytes(),
        (git_dir / "HEAD").read_bytes(),
        git(folder, "--no-optional-locks", "status", "--porcelain=v2", "-uall").encode(),
        git(folder, "for-each-ref").encode(),
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
def app(tmp_path) -> dict[str, Any]:
    """The app's repository: ``main`` and ``feature``, each with the copy's server and a reply
    file that differs, the person on ``main`` with work in progress."""
    folder = tmp_path / "app"
    folder.mkdir()
    git(folder, "init", "-q", "-b", "main")
    (folder / "copy_app.py").write_text(COPY_APP)
    (folder / "reply.txt").write_text("main\n")
    git(folder, "add", ".")
    git(folder, "commit", "-q", "-m", "main")
    git(folder, "checkout", "-q", "-b", "feature")
    (folder / "reply.txt").write_text("feature\n")
    git(folder, "commit", "-q", "-am", "feature")
    feature = git(folder, "rev-parse", "HEAD").strip()
    git(folder, "checkout", "-q", "main")
    (folder / "reply.txt").write_text("work in progress\n")
    return {"folder": folder, "feature": feature}


def _rig(make_rig, app, monkeypatch, *, recipe: bool = True) -> scripted_agent.Rig:
    """The rig on *app*: a reset plan whose copy is started from the app's folder, and a proven
    process recipe with nothing to set up."""
    folder = app["folder"]
    rig = make_rig(options={"memory_file": MEMORY_FILE, "code": {"path": str(folder)}})
    rig.manage()
    store = PlanStore(rig.config.data_dir)
    plan = store.load(TARGET)
    assert plan is not None
    command = [sys.executable, str(folder / "copy_app.py"), "{port}"]
    plan.launch = {**plan.launch, "command": command, "cwd": str(folder)}
    store.save(plan)
    if recipe:
        RecipeStore(rig.config.data_dir).save(
            BuildRecipe(target=TARGET, kind="process", command=command, status="proven")
        )
    monkeypatch.setattr(build, "free_bytes", lambda _folder: 60 * 1000**3)
    rig.fill("memory-recalls-fact")
    return rig


async def _run(rig: scripted_agent.Rig, ref: str) -> dict[str, Any]:
    estimate = rig.runs.estimate(TARGET, SET, None, 1, ref)
    started = await rig.runs.start(estimate["token"])
    await rig.runs.wait()
    doc = rig.runs.load(started["run"])
    assert doc is not None
    return doc


async def test_a_run_of_a_branch_plays_its_worktree_and_reuses_what_was_built(
    make_rig, app, monkeypatch
) -> None:
    rig = _rig(make_rig, app, monkeypatch)
    folder = app["folder"]
    before = fingerprint(folder)
    started: list[dict[str, Any]] = []
    copy_of = isolated.copy_of

    def spy(spec, target, slot, work, *, env=None):
        started.append(spec)
        return copy_of(spec, target, slot, work, env=env)

    built: list[Path] = []
    build_version = build.build_version

    async def counted(recipe, source, tag, **kw):
        built.append(source)
        return await build_version(recipe, source, tag, **kw)

    monkeypatch.setattr(isolated, "copy_of", spy)
    monkeypatch.setattr(build, "build_version", counted)
    released: list[str] = []
    release = versioned.Ready.release

    def let_go(ready):
        released.append(ready.ref)
        release(ready)

    monkeypatch.setattr(versioned.Ready, "release", let_go)

    estimate = rig.runs.estimate(TARGET, SET, None, 1, "feature")
    short = app["feature"][:7]
    assert estimate["version"]["said"] == f"feature @ {short}"
    assert estimate["version"]["cost"].startswith(f"Checks out feature @ {short} and starts it")
    doc = await _run(rig, "feature")

    worktree = (rig.config.data_dir / "versions" / TARGET / app["feature"][:12]).resolve()
    assert doc["status"] == "done", doc.get("stop_reason")
    [spec] = started
    assert spec["cwd"] == str(worktree) and spec["command"][1] == str(worktree / "copy_app.py")
    assert (worktree / "reply.txt").read_text() == "feature\n"
    assert built == [worktree]
    version = doc["version"]
    assert version["ref"] == "feature" and version["branch"] == "feature"
    assert version["commit"] == app["feature"] and version["dirty"] is False
    assert version["verified"] is True and version["played"]["code"] == f"commit:{app['feature']}"
    assert detail(doc, [doc], None)["version"]["said"] == f"feature @ {short}"
    assert [t for c in doc["cases"].values() for t in c["tries"]]
    assert fingerprint(folder) == before, "the person's checkout isn't touched"
    assert released == ["feature"], "the run let go of its version as it ended"

    assert rig.runs.estimate(TARGET, SET, None, 1, "feature")["version"]["cost"] == (
        f"Set up already: feature @ {short}. Your checkout isn't touched."
    )
    again = await _run(rig, "feature")
    assert again["status"] == "done" and built == [worktree], "built once, reused after"
    assert again["version"]["notes"] == [] and again["version"]["verified"] is True
    assert released == ["feature", "feature"]
    assert fingerprint(folder) == before


async def test_a_version_that_isnt_built_ends_the_run_before_any_try_unjudged(
    make_rig, app, monkeypatch
) -> None:
    rig = _rig(make_rig, app, monkeypatch)

    async def fails(recipe, source, tag, **kw):
        kw["said"]("Step 4/9 : RUN uv sync")
        raise BuildFailed("The build stopped:\nStep 4/9 : RUN uv sync\nerror: no such package")

    started: list[Any] = []
    monkeypatch.setattr(build, "build_version", fails)
    monkeypatch.setattr(isolated, "copy_of", lambda *a, **k: started.append(a))
    doc = await _run(rig, "feature")
    assert doc["status"] == "failed" and doc["failure"] == "version_not_built"
    assert doc["stop_reason"].startswith(f"feature @ {app['feature'][:7]} wasn't built.")
    assert doc["stop_reason"].endswith("Step 4/9 : RUN uv sync\nerror: no such package")
    assert started == [], "no copy started"
    assert not [t for c in doc["cases"].values() for t in c["tries"]]
    assert doc["version"]["ref"] == "feature" and doc["version"]["played"] is None
    view = detail(doc, [doc], None)
    assert view["failure"] == "version_not_built"
    [test] = view["tests"]
    assert test["verdict"] is None and test["reason"] == "its version wasn't checked out or built"
    assert view["totals"]["passed"] == 0 and not any(t["verdict"] == "fail" for t in view["tests"])


async def test_a_version_with_no_proven_recipe_is_refused_before_it_starts(
    make_rig, app, monkeypatch
) -> None:
    rig = _rig(make_rig, app, monkeypatch, recipe=False)
    with pytest.raises(versioned.VersionRefused) as caught:
        rig.runs.estimate(TARGET, SET, None, 1, "feature")
    assert str(caught.value).endswith(f"Prove how {TARGET} is built first, on Test environment.")
    assert rig.runs.estimate(TARGET, SET, None, 1, "disk")["ref"] == "disk"
