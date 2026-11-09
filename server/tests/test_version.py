"""What a run played, by name (ADR 0011, phase 1): the checkout it was of, what actually played
(a compose copy's images, a process copy's working tree, or the person's own app), whether
evalution can vouch for it, how old runs read, and how two runs compare by it. Docker is never
asked: its answers are made up here, as the launch tests do."""

import json
import os
import subprocess
from pathlib import Path

from evalution.code import Code
from evalution.config import CodeConfig
from evalution.core.version import (
    NOT_RECORDED,
    NOT_STARTED,
    Checkout,
    Image,
    described,
    on_images,
    on_tree,
    same_played,
    unstarted,
)
from evalution.isolation import launch
from evalution.isolation.launch import ComposeCopy, Copy, ProcessCopy
from evalution.results.compare import comparison
from evalution.results.migrate import upcast
from evalution.results.record import SCHEMA
from evalution.results.views import summary
from evalution.runs import isolated

HEAD = "e535c4d89abb688afc9be53a0dcc3291b2573a52"
#: When HEAD was committed.
AT = 1_793_626_412.0
CHECKOUT = Checkout(branch="page-testing", commit=HEAD, committed_at=AT, dirty=False)


def _image(service: str, created: float, *, built: bool = True, id_: str = "") -> Image:
    name = f"app/{service}:local" if built else "postgres:16-alpine"
    return Image(service, name, id_ or f"sha256:{service}", created, built)


# What is vouched for


def test_images_built_before_the_last_commit_may_be_an_earlier_version() -> None:
    found = on_images(CHECKOUT, [_image("engine", AT - 180), _image("postgres", 0, built=False)])
    assert found.verified is False
    assert found.why_unverified == (
        "app/engine:local was built before the last commit (e535c4d), "
        "so it may be an earlier version"
    )
    assert found.commit == HEAD and found.branch == "page-testing" and found.ref == "disk"
    images = found.played["images"]
    assert [(i["service"], i["built"]) for i in images] == [("engine", True), ("postgres", False)]


def test_images_built_after_the_last_commit_are_vouched_for_and_a_pulled_one_never_counts() -> None:
    found = on_images(CHECKOUT, [_image("engine", AT + 60), _image("postgres", 0, built=False)])
    assert (found.verified, found.why_unverified) == (True, None)


def test_files_changed_since_the_images_were_built() -> None:
    dirty = Checkout("main", HEAD, AT, dirty=True, changed_at=AT + 600)
    found = on_images(dirty, [_image("engine", AT + 60), _image("host", AT + 60)])
    assert found.verified is False and found.dirty is True
    assert (
        found.why_unverified == "files changed since app/engine:local and app/host:local were built"
    )
    # Changed before they were built: they hold the change.
    built_after = on_images(dirty, [_image("engine", AT + 900)])
    assert built_after.verified is True


def test_what_cant_be_told_is_said_and_not_vouched() -> None:
    assert on_images(Checkout(), [_image("engine", AT)]).verified is None
    pulled = on_images(CHECKOUT, [_image("postgres", 0, built=False)])
    assert pulled.verified is None and "build no image" in pulled.why_unverified


def test_a_process_copy_plays_its_working_tree_and_the_persons_app_is_not_vouched() -> None:
    dirty = Checkout("main", HEAD, AT, dirty=True, changed_at=AT + 5)
    tree = on_tree(dirty, "abc123")
    assert tree.played == {"kind": "tree", "code": "abc123"}
    assert (tree.verified, tree.dirty) == (True, True)
    own = unstarted(CHECKOUT)
    assert (own.played, own.verified, own.why_unverified) == (None, None, NOT_STARTED)


def test_the_version_in_words_leaves_out_what_doesnt_apply() -> None:
    dirty = {"branch": "page-testing", "commit": HEAD, "dirty": True}
    assert described(dirty, now=True) == (
        "what's on disk now: page-testing @ e535c4d, with uncommitted changes"
    )
    assert (
        described({"branch": None, "commit": HEAD, "dirty": False}) == "what was on disk: e535c4d"
    )
    assert described({"commit": None}, now=True) == "what's on disk now"


# Where it comes from


def _git(folder: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def test_the_checkout_says_its_branch_commit_and_uncommitted_changes(tmp_path) -> None:
    code = Code(CodeConfig(tmp_path, ()))
    assert code.checkout() == Checkout(), "not a checkout: nothing to say"
    (tmp_path / "app.py").write_text("x = 1")
    _git(tmp_path, "init", "-q", "-b", "feature-x")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-qm", "first")
    found = code.checkout()
    assert (found.branch, found.commit, found.dirty) == ("feature-x", code.commit(), False)
    assert found.committed_at and found.changed_at is None
    (tmp_path / "app.py").write_text("x = 2")
    os.utime(tmp_path / "app.py", (found.committed_at + 100, found.committed_at + 100))
    changed = code.checkout()
    assert changed.dirty is True and changed.changed_at == found.committed_at + 100
    _git(tmp_path, "checkout", "-q", "--detach")
    assert code.checkout().branch is None


#: ``docker compose config`` under every profile: an engine and a host the app builds (the host
#: only to start it itself, under a profile), and a database it pulls.
EVERY = {
    "services": {
        "engine": {"image": "app/engine:local", "build": {"context": "."}},
        "host": {"image": "app/host:local", "build": {"context": "."}, "profiles": ["build"]},
        "postgres": {"image": "postgres:16-alpine"},
    }
}
CREATED = {
    "sha256:engine-run": "2026-11-02T13:30:41.123456789Z",
    "app/host:local": "2026-11-01T13:49:41.5Z",
    "sha256:pg": "2026-09-17T21:33:33.1Z",
    "app/engine:local": "2026-11-02T13:30:41.123456789Z",
    "postgres:16-alpine": "2026-09-17T21:33:33.1Z",
}


def _docker(calls: list[tuple[str, ...]]):
    async def run(*args, **kwargs):
        calls.append(args)
        if "config" in args:
            return 0, json.dumps(EVERY), ""
        if args[1] == "inspect":  # the copy's containers
            lines = [
                "engine\tapp/engine:local\tsha256:engine-run",
                "postgres\tpostgres:16\tsha256:pg",
            ]
            return 0, "\n".join(lines), ""
        if args[1:3] == ("image", "inspect"):
            ref = args[-1]
            if ref not in CREATED:
                return 1, "", "No such image"
            image = ref if ref.startswith("sha256:") else f"sha256:{ref.split('/')[-1]}"
            return 0, f"{image}\t{CREATED[ref]}\n", ""
        raise AssertionError(f"unexpected docker call {args}")

    return run


def _started(tmp_path: Path) -> ComposeCopy:
    copy = ComposeCopy([tmp_path / "compose.yaml"], None, "app", 0, tmp_path)
    copy.copy = Copy(
        project="eval-app-0",
        ports={},
        url="http://127.0.0.1:1",
        containers=["eval-app-0-engine-1", "eval-app-0-postgres-1"],
        volumes=[],
        shared=[],
        rewired=[],
        fixed=[],
        notes=[],
        started=0,
        start_seconds=0,
        override=tmp_path / "o.yaml",
    )
    return copy


async def test_a_compose_copy_says_the_images_its_containers_run(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(launch, "_run", _docker(calls))
    monkeypatch.setattr(launch, "_docker", lambda: "docker")
    found = await _started(tmp_path).images()
    assert [(i.service, i.image, i.id, i.built) for i in found] == [
        ("engine", "app/engine:local", "sha256:engine-run", True),
        # Built by the app's files and started by the app itself: it plays too.
        ("host", "app/host:local", "sha256:host:local", True),
        ("postgres", "postgres:16", "sha256:pg", False),
    ]
    assert found[0].created == 1793626241.123456
    assert any("--profile" in c and "*" in c for c in calls), "every profile's services read"
    assert not any({"up", "build", "pull", "run"} & set(c) for c in calls), "read-only"


async def test_before_it_starts_a_compose_copy_says_what_it_would_play(
    tmp_path, monkeypatch
) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(launch, "_run", _docker(calls))
    monkeypatch.setattr(launch, "_docker", lambda: "docker")
    copy = ComposeCopy([tmp_path / "compose.yaml"], None, "app", 0, tmp_path)
    found = await copy.images()
    assert [(i.service, i.image) for i in found] == [
        ("engine", "app/engine:local"),
        ("host", "app/host:local"),
        ("postgres", "postgres:16-alpine"),
    ]
    # The engine was built at 13:30:41, before a commit made three minutes later.
    said = on_images(Checkout("evals-w0", HEAD, AT, dirty=False), found)
    assert said.verified is False
    assert said.why_unverified.startswith("app/engine:local and app/host:local were built before")


async def test_a_run_on_a_process_copy_records_its_working_tree(tmp_path) -> None:
    copy = ProcessCopy(["true"], tmp_path)
    found = await isolated._version(copy, CHECKOUT, "c0ffee")
    assert found.played == {"kind": "tree", "code": "c0ffee"} and found.verified is True


async def test_images_that_dont_read_leave_the_run_unvouched(tmp_path, monkeypatch) -> None:
    async def broken(*args, **kwargs):
        raise launch.LaunchError("Docker isn't installed on this computer")

    monkeypatch.setattr(launch, "_run", broken)
    found = await isolated._version(_started(tmp_path), CHECKOUT, None)
    assert found.verified is None and "couldn't read the copy's images" in found.why_unverified


# How it is read and compared


def _run(name: str, commit: str, version: dict | None, started: float) -> dict:
    return {
        "schema": SCHEMA,
        "run": name,
        "target": "app",
        "set": "main",
        "started_at": started,
        "status": "done",
        "commit": commit,
        "version": version,
        "cases": {},
        "totals": {},
    }


def _on(ids: dict[str, str], commit: str, branch: str = "main") -> dict:
    images = [_image(s, AT, id_=i) for s, i in ids.items()] + [_image("pg", 0, built=False)]
    return on_images(Checkout(branch, commit, AT - 1, dirty=False), images).as_dict()


def test_the_same_images_are_the_same_code_whatever_the_checkout_said() -> None:
    other = "a1b2c3d" + "0" * 33
    base = _run("r1", other, _on({"engine": "sha256:1"}, other, "feature-x"), 1)
    run = _run("r2", HEAD, _on({"engine": "sha256:1"}, HEAD), 2)
    said = comparison(run, base)
    assert not [row for row in said["changed"] if row["what"] == "Code"]
    assert said["code"] == (
        "Same code played: the same images ran in both, "
        "though the checkout moved from a1b2c3d to e535c4d"
    )
    # A pulled image that differs isn't the app's code.
    base["version"]["played"]["images"][-1]["id"] = "sha256:other-pg"
    assert same_played(base["version"], run["version"]) is True


def test_other_images_are_other_code_on_one_commit() -> None:
    base = _run("r1", HEAD, _on({"engine": "sha256:1"}, HEAD), 1)
    run = _run("r2", HEAD, _on({"engine": "sha256:2"}, HEAD), 2)
    said = comparison(run, base)
    assert {"what": "Code", "before": "main @ e535c4d", "now": "main @ e535c4d"} in said["changed"]
    assert (
        said["code"] == "Different code played: other images ran, though both runs were of e535c4d"
    )


def test_without_what_played_the_commits_decide_as_before() -> None:
    other = "a1b2c3d" + "0" * 33
    base = upcast({**_run("r1", other, None, 1), "schema": 3})
    run = _run("r2", HEAD, _on({"engine": "sha256:1"}, HEAD), 2)
    said = comparison(run, base)
    assert {"what": "Code", "before": "a1b2c3d", "now": "main @ e535c4d"} in said["changed"]
    assert said["code"] is None


def test_an_old_run_reads_as_what_was_on_disk_not_recorded() -> None:
    old = {**_run("r1", HEAD, None, 1), "schema": 3}
    del old["version"]
    read = upcast(old)
    assert read["schema"] == SCHEMA and "version" not in old
    assert read["version"] == {
        "ref": "disk",
        "branch": None,
        "commit": HEAD,
        "dirty": None,
        "played": None,
        "verified": None,
        "why_unverified": NOT_RECORDED,
    }
    assert upcast(read) is read
    shown = summary({**read, "totals": {}})["version"]
    assert shown["said"] == "what was on disk: e535c4d"
