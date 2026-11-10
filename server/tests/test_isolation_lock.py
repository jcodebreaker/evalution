"""One holder of a target's copies at a time, and a rehearsal's work folder gone once it ends
(B6, its parts that need no server). Offline: fakes in place of copies of the app."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evalution.config import load
from evalution.isolation import isolate as iso
from evalution.isolation.launch import ComposeCopy, LaunchError
from evalution.isolation.lock import Busy, TargetLock
from evalution.isolation.plan import Plan, PlanStore
from evalution.runs import isolated


def test_a_second_holder_is_told_at_once(tmp_path) -> None:
    first = TargetLock(tmp_path, "bot")
    first.acquire()
    with pytest.raises(Busy, match=r"^bot's copy is in use by a run: wait for it or stop it$"):
        TargetLock(tmp_path, "bot", "search").acquire()
    with TargetLock(tmp_path, "other") as other:  # another target's copies are its own
        assert other.held
    first.release()
    with TargetLock(tmp_path, "bot") as again:
        assert again.held
    assert not again.held
    with pytest.raises(ValueError, match="not a target name"):
        TargetLock(tmp_path, "../bot")
    with TargetLock(tmp_path, "bot", "rehearsal"), pytest.raises(Busy, match="by a rehearsal:"):
        TargetLock(tmp_path, "bot").acquire()


@pytest.fixture
def config(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    return load(tmp_path / "evalution.yaml")


class Copy:
    url = "http://127.0.0.1:9"
    pid = None

    def __init__(self, fails: bool) -> None:
        self.fails = fails

    async def start(self) -> None:
        if self.fails:
            raise LaunchError("the copy didn't start (logs: work/eval-bot-0.log)")


@pytest.fixture
def fakes(config, monkeypatch, tmp_path):
    made: dict = {"fails": False, "started": 0}
    work = tmp_path / "work"

    def copy_of(*a, **k):
        made["started"] += 1
        return Copy(made["fails"])

    def work_folder(*a):
        work.mkdir(parents=True, exist_ok=True)
        (work / "eval-bot-0.override.yaml").write_text("services: {}\n")
        return work

    async def rehearse_on(*a, **k):
        return SimpleNamespace(ok=True, leaks=[], seconds=1.0, report=lambda: "Proven."), []

    async def take_down(copy):
        return []

    monkeypatch.setattr(iso, "copy_of", copy_of)
    monkeypatch.setattr(iso, "work_folder", work_folder)
    monkeypatch.setattr(iso, "plan_spec", lambda *a: {"kind": "process"})
    monkeypatch.setattr(iso, "adapter_on", lambda *a, **k: object())
    monkeypatch.setattr(iso, "_rehearse_on", rehearse_on)
    monkeypatch.setattr(iso, "take_down", take_down)
    made["work"] = work
    return made


async def test_a_rehearsal_waits_for_no_one_and_starts_nothing_while_another_holds(
    config, fakes
) -> None:
    with TargetLock(config.data_dir, "bot"), pytest.raises(Busy):
        await iso.rehearse_plan(config, "bot", Plan(target="bot"))
    assert fakes["started"] == 1  # made, never started
    plan = await iso.rehearse_plan(config, "bot", Plan(target="bot"))
    assert plan.status == "proven"
    assert not TargetLock(config.data_dir, "bot").held  # let go once it ended


async def test_a_rehearsals_work_folder_goes_when_it_ends_and_stays_when_the_copy_failed(
    config, fakes
) -> None:
    await iso.rehearse_plan(config, "bot", Plan(target="bot"))
    work: Path = fakes["work"]
    assert not work.exists()  # noqa: ASYNC240 - a test's own folder
    fakes["fails"] = True
    with pytest.raises(LaunchError, match="logs"):
        await iso.rehearse_plan(config, "bot", Plan(target="bot"))
    # Kept, beside the logs the error names.
    assert (work / "eval-bot-0.override.yaml").is_file()


async def test_a_run_finding_the_copy_busy_doesnt_fail_the_plan(config, fakes) -> None:
    PlanStore(config.data_dir).save(Plan(target="bot", status="stale"))
    found = isolated.Footing("none", Plan(target="bot", status="stale"), 1)
    with TargetLock(config.data_dir, "bot"), pytest.raises(Busy):
        await isolated.rehearse_again(config, "bot", found)
    assert PlanStore(config.data_dir).load("bot").status == "stale"  # type: ignore[union-attr]


async def test_a_runs_copies_hold_the_lock_until_they_close(config, monkeypatch, tmp_path) -> None:
    async def stores_on(*a, **k):
        return []

    class Process:
        url = "http://127.0.0.1:9"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    async def take_down(copy):
        return []

    monkeypatch.setattr(isolated, "copy_of", lambda *a, **k: Process())
    monkeypatch.setattr(isolated, "stores_on", stores_on)
    monkeypatch.setattr(isolated, "adapter_on", lambda *a, **k: object())
    monkeypatch.setattr(isolated, "work_folder", lambda *a: tmp_path / "w")
    monkeypatch.setattr(isolated, "plan_spec", lambda *a: {"kind": "process"})
    monkeypatch.setattr(isolated, "take_down", take_down)
    found = isolated.Footing("proven", Plan(target="bot", status="proven"), 1)
    copies = isolated.Copies(config, "bot", found)
    await copies.start(1)
    with pytest.raises(Busy):
        await isolated.Copies(config, "bot", found).start(1)
    await copies.close()
    with TargetLock(config.data_dir, "bot") as lock:
        assert lock.held


async def test_a_copy_a_server_parked_goes_for_the_command_line_unless_a_run_holds_it(
    config, fakes, monkeypatch
) -> None:
    """``evalution isolate`` or a rehearsal from the command line, while the server keeps the
    target's copy up between runs: no run holds the lock, so the idle copy is taken down and the
    new one starts; while a run holds it, the person is told so and nothing is touched."""
    events: list[str] = []

    class Parked(ComposeCopy):
        def __init__(self) -> None:
            self.project = "eval-bot-0"
            self.copy = SimpleNamespace(url="http://127.0.0.1:9")

        async def start(self) -> None:
            events.append(f"start {self.project}")

    async def down_idle(project: str, data_dir, app: str | None = None) -> bool:
        events.append(f"down {project} of {app}")
        return True

    monkeypatch.setattr(iso, "copy_of", lambda *a, **k: Parked())
    monkeypatch.setattr(iso, "down_idle", down_idle)
    with (
        TargetLock(config.data_dir, "bot"),
        pytest.raises(Busy, match="bot's copy is in use by a run: wait for it or stop it"),
    ):
        await iso.rehearse_plan(config, "bot", Plan(target="bot"))
    assert events == []
    plan = await iso.rehearse_plan(config, "bot", Plan(target="bot"))
    assert plan.status == "proven"
    assert events == ["down eval-bot-0 of bot", "start eval-bot-0"]
