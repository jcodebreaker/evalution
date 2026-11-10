"""Copies a server left behind (B6's cleanup): each compose copy evalution starts for a run is
labelled with its owner and its run, and on server start and before any start, ``eval-*``
projects whose owner no longer runs are removed. Never one without evalution's owner label, never
one not named ``eval-*``, never one whose owner runs or isn't known here. Offline: Docker is a
fake that answers what was asked of it."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import yaml

from evalution.config import load
from evalution.isolation import launch
from evalution.isolation.launch import (
    OWNER_LABEL,
    RUN_LABEL,
    ComposeCopy,
    LaunchError,
    override_for,
    owner_gone,
    owner_id,
    sweep,
)
from evalution.isolation.plan import Plan
from evalution.runs import isolated

CONFIG = {
    "services": {
        "api": {"image": "shop/api", "ports": [{"target": 8000, "published": "8000"}]},
        "db": {"image": "postgres:16", "labels": {"keep": "yes"}},
    }
}
LABELS = {OWNER_LABEL: "0123456789abcdef", RUN_LABEL: "20261109-120000-ab12"}


def test_a_copy_for_a_run_carries_its_owner_and_its_run() -> None:
    tagged = override_for(CONFIG, "eval-shop-0", pick=lambda: 4000, tag=True, labels=LABELS)
    for service in ("api", "db"):
        block = tagged.text.split(f"  {service}:\n", 1)[1]
        assert '      evalution.owner: "0123456789abcdef"' in block.split("\n  ")[0] + block
    assert tagged.text.count("evalution.run") == 2
    whole = json.loads(override_for(CONFIG, "eval-shop-0", tag=False, labels=LABELS).text)
    assert whole["services"]["db"]["labels"] == {"keep": "yes", **LABELS}
    assert whole["services"]["api"]["labels"] == LABELS
    # Without labels (a rehearsal), nothing is added.
    assert "labels" not in override_for(CONFIG, "eval-shop-0", pick=lambda: 4000).text


def test_an_owner_is_gone_only_when_this_data_folder_knows_it_and_nothing_holds_it(
    tmp_path,
) -> None:
    mine = owner_id(tmp_path)
    assert owner_id(tmp_path) == mine, "one owner a process"
    assert not owner_gone(tmp_path, mine), "this process runs"
    # A server that stopped left its file, held by nothing now.
    left = "fedcba9876543210"
    (tmp_path / "isolation" / "owners" / f"{left}.lock").write_text("")
    assert owner_gone(tmp_path, left)
    # Another data folder's, or no evalution's at all: not known here, never gone.
    assert not owner_gone(tmp_path, "aaaaaaaaaaaaaaaa")
    assert not owner_gone(tmp_path, "../../etc/passwd")
    assert not owner_gone(tmp_path, "")


class FakeDocker:
    """``docker ps`` listing each container's project and owner label; records ``down``."""

    def __init__(self, containers: list[tuple[str, str]]) -> None:
        self.containers = containers
        self.removed: list[str] = []

    async def run(self, *args: str, **_kw) -> tuple[int, str, str]:
        if args[1:3] == ("ps", "-a") and "--format" in args and "--filter" in args:
            lines = [f"{p}\t{o}" for p, o in self.containers]
            return 0, "\n".join(lines) + "\n", ""
        if args[1:3] == ("ps", "-aq"):
            return 0, "", ""
        if args[1] == "compose" and "down" in args:
            self.removed.append(args[args.index("-p") + 1])
            return 0, "", ""
        if args[1:3] == ("volume", "ls"):
            return 0, "", ""
        raise AssertionError(f"Docker was asked {args}")


@pytest.fixture
def docker(monkeypatch) -> FakeDocker:
    fake = FakeDocker([])
    monkeypatch.setattr(launch, "_run", fake.run)
    monkeypatch.setattr(launch, "_docker", lambda: "docker")
    monkeypatch.setattr(launch.shutil, "which", lambda _name: "docker")
    return fake


async def test_the_sweep_removes_only_labelled_eval_projects_whose_owner_is_gone(
    docker, tmp_path: Path
) -> None:
    gone, alive = "fedcba9876543210", owner_id(tmp_path)
    (tmp_path / "isolation" / "owners" / f"{gone}.lock").write_text("")
    docker.containers = [
        ("eval-shop-0", gone),
        ("eval-shop-0", gone),
        ("eval-shop-1", alive),  # this server's own: it runs
        ("eval-bare-0", ""),  # named eval-*, with no owner label: not evalution's to judge
        ("eval-mixed-0", gone),  # one container unlabelled: left alone too
        ("eval-mixed-0", ""),
        ("eval-other-0", "aaaaaaaaaaaaaaaa"),  # another data folder's: not known here
        ("shop", gone),  # the person's own, whatever its labels say
    ]
    removed = await sweep(lambda owner: owner_gone(tmp_path, owner))
    assert removed == ["eval-shop-0"] and docker.removed == ["eval-shop-0"]


async def test_without_docker_the_sweep_does_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(launch.shutil, "which", lambda _name: None)
    assert await sweep(lambda _owner: True) == []


async def test_a_runs_compose_copies_are_labelled_and_left_copies_swept_first(
    tmp_path, monkeypatch
) -> None:
    events: list[str] = []

    async def swept(gone) -> list[str]:
        events.append("sweep")
        return []

    async def start(self: ComposeCopy):
        events.append(f"start {self.project} {sorted(self.labels.items())}")
        raise LaunchError("only looking")

    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"shop": {"adapter": "http", "http": {}}}})
    )
    config = load(tmp_path / "evalution.yaml")
    monkeypatch.setattr(isolated, "sweep", swept)
    monkeypatch.setattr(ComposeCopy, "start", start)
    monkeypatch.setattr(isolated, "plan_spec", lambda *a: {"kind": "compose", "files": []})
    plan = Plan(target="shop", stores=[], status="proven")
    copies = isolated.Copies(config, "shop", isolated.Footing("proven", plan, 1), run="r-1")
    with pytest.raises(LaunchError):
        await copies.start(1)
    await copies.close()
    labels = sorted({OWNER_LABEL: owner_id(config.data_dir), RUN_LABEL: "r-1"}.items())
    assert events == ["sweep", f"start eval-shop-0 {labels}"]


async def test_an_idle_copy_of_this_data_folders_evalution_is_taken_down_and_no_other(
    docker, tmp_path: Path, caplog
) -> None:
    # A server of this data folder runs and parked its copy: its owner file is there, held.
    server = "0123456789abcdef"
    (tmp_path / "isolation" / "owners").mkdir(parents=True)
    (tmp_path / "isolation" / "owners" / f"{server}.lock").write_text("")
    docker.containers = [
        ("eval-shop-0", server),
        ("eval-shop-0", server),
        ("eval-bare-0", ""),  # no owner label: not known to be evalution's
        ("eval-other-0", "aaaaaaaaaaaaaaaa"),  # another data folder's
    ]
    with caplog.at_level(logging.WARNING, logger="evalution.isolation.launch"):
        assert await launch.down_idle("eval-shop-0", tmp_path, "shop")
        for project in ("eval-bare-0", "eval-other-0", "eval-none-0"):
            assert not await launch.down_idle(project, tmp_path)
    assert docker.removed == ["eval-shop-0"]
    # The command line says what it did, so the server's next run starting a new copy is no news.
    assert [r.getMessage() for r in caplog.records] == [
        "evals: took down the server's idle copy of shop to start this one"
    ]
    with pytest.raises(LaunchError, match="isn't evalution's"):
        await launch.down_idle("shop", tmp_path)
