"""Comparing across versions (ADR 0011, plan "Phase 6"): a run of a branch is compared with the
latest run of the same tests on the repository's base branch, and says so ("feature-x vs main");
when there is none, it says that. Runs of what was on disk keep today's rule, and old runs, which
didn't record their version, are never on the base branch. The base branch is read from the
repository: ``origin/HEAD``, else ``main``, else ``master``."""

import os
import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.code import Code
from evalution.config import CodeConfig, load
from evalution.core.version import not_recorded
from evalution.results.compare import (
    base_branch_said,
    comparison,
    default_base,
    of_another_branch,
)
from evalution.results.migrate import upcast
from evalution.results.store import RunStore
from evalution.storage import Database

DAY = 24 * 3600
NOW = 100 * DAY


def _played(run):
    """*run* as read back, its tries judged as an older run's are, its version as it recorded it."""
    return {**upcast(run), "version": run["version"]}


def _version(ref="disk", branch=None, commit="c" * 40):
    return {
        "ref": ref,
        "branch": branch,
        "commit": commit,
        "dirty": False,
        "played": None,
        "verified": True,
        "why_unverified": None,
    }


def _run(run, at, version, *, passed=True, commit="c" * 40):
    """A finished run of set ``main`` of app ``bot``, one test, three tries."""
    return {
        "run": run,
        "target": "bot",
        "set": "main",
        "status": "done",
        "started_at": at,
        "prompt": "p1",
        "commit": commit,
        "version": version,
        "cases": {
            "a": {
                "name": "A",
                "source": None,
                "expected": {},
                "tries": [
                    {
                        "n": n,
                        "passed": passed,
                        "seconds": 10.0,
                        "tokens": 10,
                        "tools": 1.0,
                        "reply": 1.0,
                        "error": None if passed else "boom",
                        "analysis": None,
                    }
                    for n in (1, 2, 3)
                ],
            }
        },
    }


MAIN_OLD = _run("m1", NOW - 3 * DAY, _version("main", "main", "1" * 40))
MAIN_NEW = _run("m2", NOW - 2 * DAY, _version("disk", "main", "2" * 40))
FEATURE_OLD = _run("f1", NOW - DAY, _version("feature-x", "feature-x", "3" * 40))
FEATURE = _run("f2", NOW, _version("feature-x", "feature-x", "4" * 40), passed=False)


def test_a_branch_run_is_compared_with_the_latest_run_of_the_set_on_the_base_branch() -> None:
    runs = [FEATURE, FEATURE_OLD, MAIN_NEW, MAIN_OLD]
    # Not the run just before it (feature-x's own), nor the older main: the newest on main, whether
    # it played main as a ref or main as it was on disk.
    assert default_base(FEATURE, runs, "main") == "m2"
    said = base_branch_said(FEATURE, runs, "main")
    assert said == {"name": "main", "run": "m2", "missing": None}
    found = comparison(_played(FEATURE), _played(MAIN_NEW))
    assert found["versions"]["heading"] == "feature-x vs main"
    assert found["versions"]["now"] == {"name": "feature-x", "said": "feature-x @ 4444444"}
    assert found["versions"]["before"] == {
        "name": "main",
        "said": "what was on disk: main @ 2222222",
    }
    # A ref named as origin names it is on main too.
    remote = _run("m3", NOW - DAY / 2, _version("origin/main", "origin/main", "5" * 40))
    assert default_base(FEATURE, [*runs, remote], "main") == "m3"


def test_a_branch_run_by_its_ref_alone_counts_as_of_that_branch() -> None:
    by_ref = _run("f3", NOW, _version("feature-y", None, "6" * 40))
    assert of_another_branch(by_ref, "main")
    assert default_base(by_ref, [by_ref, FEATURE_OLD, MAIN_OLD], "main") == "m1"


def test_with_no_run_on_the_base_branch_it_says_so_and_keeps_todays_rule() -> None:
    runs = [FEATURE, FEATURE_OLD]
    said = base_branch_said(FEATURE, runs, "main")
    assert said == {"name": "main", "run": None, "missing": "No run of these tests on main yet"}
    # Still compared with something: the run before it, as today.
    assert default_base(FEATURE, runs, "main") == "f1"
    assert base_branch_said(FEATURE, [FEATURE], "main")["run"] is None
    assert default_base(FEATURE, [FEATURE], "main") is None


def test_a_later_run_on_the_base_branch_counts_when_there_is_no_earlier_one() -> None:
    later = _run("m9", NOW + DAY, _version("main", "main", "9" * 40))
    assert default_base(FEATURE, [FEATURE, FEATURE_OLD, later], "main") == "m9"


def test_disk_runs_keep_todays_rule() -> None:
    # On the base branch: today's rule, the newest earlier run on another setup.
    on_main = _run("m4", NOW, _version("disk", "main", "7" * 40))
    runs = [on_main, FEATURE_OLD, MAIN_NEW]
    assert not of_another_branch(on_main, "main")
    assert default_base(on_main, runs, "main") == "f1"
    assert base_branch_said(on_main, runs, "main") is None
    # Off any branch (detached), or no base branch known: today's rule too.
    detached = _run("d1", NOW, _version("disk", None, "8" * 40))
    assert default_base(detached, [detached, FEATURE_OLD, MAIN_NEW], "main") == "f1"
    assert default_base(FEATURE, [FEATURE, FEATURE_OLD, MAIN_NEW], None) == "f1"
    assert base_branch_said(FEATURE, [FEATURE, MAIN_NEW], None) is None


def test_old_runs_never_count_as_on_the_base_branch() -> None:
    old = _run("o1", NOW - DAY, not_recorded("2" * 40))
    unversioned = {**_run("o2", NOW - DAY, None)}
    del unversioned["version"]
    for stale in (old, unversioned):
        assert default_base(FEATURE, [FEATURE, stale], "main") == stale["run"], "today's rule"
        said = base_branch_said(FEATURE, [FEATURE, stale], "main")
        assert said["run"] is None and said["missing"]
        # Nor of another branch: an old run keeps today's rule.
        assert not of_another_branch(stale, "main")
        assert base_branch_said(stale, [stale, MAIN_OLD], "main") is None
    assert (
        comparison(_played(FEATURE), _played(old))["versions"]["heading"] == "feature-x vs 2222222"
    )


# The base branch, from the repository


def _git(folder: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    done = subprocess.run(
        ["git", "-C", str(folder), *args], capture_output=True, text=True, check=True, env=env
    )
    return done.stdout


def _repo(folder: Path, branch: str) -> Path:
    folder.mkdir()
    _git(folder, "init", "-q", "-b", branch)
    _git(folder, "config", "user.email", "t@example.com")
    _git(folder, "config", "user.name", "t")
    (folder / "app.py").write_text("print('hi')\n")
    _git(folder, "add", "app.py")
    _git(folder, "commit", "-q", "-m", "first")
    return folder


def _base(folder: Path) -> str | None:
    return Code(CodeConfig(folder, ())).base_branch()


def test_the_base_branch_is_origin_head_else_main_else_master(tmp_path: Path) -> None:
    # Neither: no base branch.
    trunk = _repo(tmp_path / "trunk", "trunk")
    assert _base(trunk) is None
    # master, then main over it.
    _git(trunk, "branch", "master")
    assert _base(trunk) == "master"
    _git(trunk, "branch", "main")
    assert _base(trunk) == "main"
    # origin/HEAD wins over both, as the remote names it; read only.
    upstream = _repo(tmp_path / "upstream", "develop")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(upstream), str(clone))
    _git(clone, "branch", "main")
    before = _git(clone, "for-each-ref")
    assert _base(clone) == "develop"
    assert _git(clone, "for-each-ref") == before
    # main only on origin counts too.
    _git(clone, "branch", "-D", "main")
    _git(clone, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    _git(clone, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(clone, "checkout", "-q", "-b", "feature-x")
    _git(clone, "branch", "-D", "develop")
    assert _base(clone) == "main"


def test_no_checkout_has_no_base_branch(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')\n")
    assert _base(tmp_path) is None


# Through the API


def test_the_comparison_names_both_versions_and_the_base_branch(tmp_path: Path) -> None:
    _repo(tmp_path / "app", "main")
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "targets": {
                    "bot": {
                        "adapter": "http",
                        "http": {"send": {"url": "x"}},
                        "code": {"path": "app"},
                    }
                }
            }
        )
    )
    config = load(tmp_path / "evalution.yaml")
    store = RunStore(Database.open(config.data_dir).docs())
    for run in (FEATURE_OLD, FEATURE):
        store.write(_played(run))
    with TestClient(create_app(config)) as client:
        alone = client.get("/v1/evals/runs/f2/comparison").json()
        assert alone["base"] == "f1", "today's rule, with nothing on main"
        assert alone["base_branch"] == {
            "name": "main",
            "run": None,
            "missing": "No run of these tests on main yet",
        }
        store.write(_played(MAIN_NEW))
        found = client.get("/v1/evals/runs/f2/comparison").json()
        assert found["base"] == "m2"
        assert found["versions"]["heading"] == "feature-x vs main"
        assert found["base_branch"] == {"name": "main", "run": "m2", "missing": None}
        # Another base, picked, still says which run is on main.
        picked = client.get("/v1/evals/runs/f2/comparison", params={"base": "f1"}).json()
        assert picked["base"] == "f1" and picked["base_branch"]["run"] == "m2"
        # A run on main is compared as today, and says nothing of a base branch.
        on_main = client.get("/v1/evals/runs/m2/comparison").json()
        assert on_main["base"] is None and on_main["base_branch"] is None
