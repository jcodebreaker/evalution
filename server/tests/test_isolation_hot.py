"""Putting a copy back without stopping the app (B1), checking it cheaply between full hashes
(B2), and skipping the put back before a slot's first try (B3). Offline: a compose copy that only
says what was done to it, and drivers that keep their state in memory."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from evalution.config import load
from evalution.isolation import hot
from evalution.isolation import isolate as iso
from evalution.isolation.drivers import FileStore, PostgresStore, VolumeStore
from evalution.isolation.drivers.base import needs_stop, widest
from evalution.isolation.launch import ComposeCopy, ProcessCopy
from evalution.isolation.plan import Plan, PlannedStore, holds_state
from evalution.isolation.rehearse import rehearse
from evalution.runs import isolated

# What each driver needs ------------------------------------------------------------------------


def test_each_driver_says_how_far_the_copy_stops(tmp_path) -> None:
    pg = PostgresStore("eval-bot-0-postgres-1", "app", "postgres")
    vol = VolumeStore("eval-bot-0_data")
    file = FileStore(tmp_path / "app.db")
    assert [needs_stop(d) for d in (pg, vol, file, object())] == ["none", "services", "all", "all"]
    assert widest([pg]) == "none"
    assert widest([pg, vol]) == "services"
    assert widest([pg, vol, file]) == "all"
    # The app's own memory, or no driver at all: only a restart clears it.
    assert widest([pg], holds_state=True) == "all"
    assert widest([]) == "all"


def test_a_file_stores_cheap_check_sees_a_file_grow_without_reading_it(tmp_path) -> None:
    folder = tmp_path / "memory"
    (folder / "deep").mkdir(parents=True)
    (folder / "deep" / "log").write_text("one\n")
    store = FileStore(folder)
    before = store.shape()
    (folder / "deep" / "log").write_text("one\ntwo\n")
    assert store.shape() != before
    (folder / "deep" / "log").write_text("six\n")  # same size: only the full hash sees it
    assert store.shape() == before


def _plan(*stores: PlannedStore, hot_: str | None = "proven") -> Plan:
    return Plan(target="bot", stores=list(stores), status="proven", hot=hot_)


PG = PlannedStore("postgres-app", "postgres", "eval-bot-postgres-1:app", strategy="relocate")
MEMORY = PlannedStore("memory-app", "memory", "process", strategy="relocate")


def test_a_plan_says_the_app_holds_state_when_a_store_is_its_memory() -> None:
    assert not holds_state(_plan(PG))
    assert holds_state(_plan(PG, MEMORY))
    assert holds_state(Plan(target="bot", app_holds_state=True))


def test_less_than_a_whole_stop_only_once_the_rehearsal_proved_it(tmp_path) -> None:
    pg = PostgresStore("eval-bot-0-postgres-1", "app", "postgres")
    compose = ComposeCopy([], None, "bot", 0, tmp_path)
    process = ProcessCopy(["true"], tmp_path)
    assert hot.level(_plan(PG), [pg], compose) == "none"
    assert hot.level(_plan(PG, hot_=None), [pg], compose) == "all"  # a plan from before
    assert hot.level(_plan(PG, hot_="failed"), [pg], compose) == "all"
    assert hot.level(_plan(PG, MEMORY), [pg], compose) == "all"
    assert hot.level(_plan(PG), [pg], process) == "all"
    assert hot.wanted(_plan(PG, hot_=None), [pg], compose) == "none"


def test_an_old_plan_reads_with_no_hot_proof() -> None:
    text = (
        Plan(target="bot")
        .to_json()
        .replace('"hot": null,', "")
        .replace('"app_holds_state": false,', "")
    )
    assert '"hot"' not in text
    read = Plan.from_json(text)
    assert read.hot is None and read.app_holds_state is False


# Fakes ----------------------------------------------------------------------------------------


class FakeCompose(ComposeCopy):
    """A compose copy that only says what was done to it."""

    def __init__(self, events: list[str], folder: Path) -> None:
        super().__init__([], None, "bot", 0, folder)
        self.events = events
        self.answer = True
        self.copy = SimpleNamespace(url="http://127.0.0.1:9", project=self.project)  # type: ignore[assignment]

    async def start(self):  # type: ignore[override]
        self.events.append("copy start")

    async def pause(self, keep=()):  # type: ignore[override]
        self.events.append("pause")
        return []

    async def resume(self) -> None:
        self.events.append("resume")

    async def stop_services(self, services):  # type: ignore[override]
        self.events.append(f"stop {','.join(services)}")
        return list(services)

    async def mounting(self, volumes):  # type: ignore[override]
        return ["worker"]

    async def answers(self, seconds: float = 60) -> bool:
        self.events.append("answers")
        return self.answer


class MemoryDriver:
    """A store whose content is a string; ``broken`` makes put back do nothing."""

    def __init__(self, events: list[str], name: str, needs: str, *, generation: bool) -> None:
        self.events = events
        self.name = self.store_id = name
        self.needs_stop = needs
        self.content = "start"
        self.oid = 1
        self.broken = False
        if not generation:
            self.generation = None  # type: ignore[assignment]

    def digest(self) -> str:
        self.events.append(f"digest {self.name}")
        return self.content

    def shape(self) -> str:
        self.events.append(f"shape {self.name}")
        return "tables"

    def generation(self) -> str:
        return str(self.oid)

    def keep(self, into: Path) -> None:
        self.events.append(f"keep {self.name}")

    def put_back(self, kept: Path) -> None:
        self.events.append(f"put_back {self.name}")
        if not self.broken:
            self.content = "start"
            self.oid += 1


class FakeAdapter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def open(self, run: str) -> str:
        self.events.append("open")
        return run


@pytest.fixture
def config(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    return load(tmp_path / "evalution.yaml")


@pytest.fixture
def rig(config, monkeypatch, tmp_path):
    """Copies on a fake compose copy whose drivers the test chooses."""
    events: list[str] = []
    made: dict[str, Any] = {"drivers": []}

    async def stores_on(plan, copy, *, roots=(), notes=None, seed=None, stage=None):
        return made["drivers"]

    monkeypatch.setattr(isolated, "stores_on", stores_on)
    monkeypatch.setattr(isolated, "copy_of", lambda *a, **k: FakeCompose(events, tmp_path / "w"))
    monkeypatch.setattr(isolated, "adapter_on", lambda *a, **k: FakeAdapter(events))
    monkeypatch.setattr(isolated, "work_folder", lambda *a: tmp_path / "work")
    monkeypatch.setattr(isolated, "plan_spec", lambda *a: {"kind": "compose"})

    async def take_down(copy):
        events.append("take down")
        return []

    monkeypatch.setattr(isolated, "take_down", take_down)

    async def copies(plan: Plan, drivers: list[Any], **config_fields: Any) -> isolated.Copies:
        made["drivers"] = drivers
        conf = config
        if config_fields:
            from dataclasses import replace

            conf = replace(config, isolation=replace(config.isolation, **config_fields))
        found = isolated.Footing("proven", plan, 1)
        out = isolated.Copies(conf, "bot", found)
        await out.start(1)
        events.clear()
        return out

    return SimpleNamespace(events=events, copies=copies)


# B1: put back without stopping the app ----------------------------------------------------------


async def test_a_postgres_only_plan_restores_with_no_container_stop(rig) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG), [db])
    copies.slots[0].fresh = False
    label = await copies.restore(0)
    assert "pause" not in rig.events and "resume" not in rig.events
    assert not [e for e in rig.events if e.startswith("stop")]
    assert rig.events.count("answers") == 1 and "put_back postgres-app" in rig.events
    assert label["problem"] is None and label["checked"] == ["postgres-app"]
    assert "note" not in label


async def test_a_volume_stops_only_the_services_that_mount_it(rig) -> None:
    vol = MemoryDriver(rig.events, "volume-data", "services", generation=False)
    vol.volume = "eval-bot-0_data"  # type: ignore[attr-defined]
    copies = await rig.copies(_plan(PG), [vol])
    copies.slots[0].fresh = False
    await copies.restore(0)
    assert rig.events[:3] == ["stop worker", "put_back volume-data", "resume"]
    assert "pause" not in rig.events


async def test_a_plan_not_proven_hot_stops_and_starts_the_whole_copy(rig) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG, hot_=None), [db])
    copies.slots[0].fresh = False
    await copies.restore(0)
    assert rig.events[:3] == ["pause", "put_back postgres-app", "resume"]


async def test_an_app_that_doesnt_answer_falls_back_to_a_restart_for_the_rest_of_the_run(
    rig,
) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG), [db])
    copies.slots[0].fresh = False
    copies.slots[0].copy.answer = False  # type: ignore[attr-defined]
    label = await copies.restore(0)
    assert rig.events[:4] == ["put_back postgres-app", "answers", "pause", "resume"]
    assert "stopped and started" in label["note"] and label["problem"] is None
    rig.events.clear()
    second = await copies.restore(0)
    assert rig.events[:3] == ["pause", "put_back postgres-app", "resume"]
    assert "answers" not in rig.events and "note" not in second


# B3: no put back before a slot's first try -------------------------------------------------------


async def test_a_slots_first_try_plays_on_the_copy_as_kept(rig) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG), [db])
    first = await copies.restore(0)
    assert rig.events == []
    assert first["check"] == "kept" and first["restore_seconds"] is None
    assert first["checked"] == ["postgres-app"] and first["problem"] is None
    second = await copies.restore(0)
    assert "put_back postgres-app" in rig.events and second["check"] == "full"


async def test_a_session_opened_before_the_first_try_means_it_is_put_back(rig) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG), [db])
    await copies.slots[0].adapter.open("check")  # type: ignore[union-attr]
    first = await copies.restore(0)
    assert "put_back postgres-app" in rig.events and first["check"] == "full"


# B2: hashed whole twice a run, a cheap check between -------------------------------------------


async def _run(copies: isolated.Copies, tries: int) -> list[dict[str, Any]]:
    labels = []
    for _ in range(tries):
        labels.append(await copies.restore(0))
        for d in copies.slots[0].drivers:
            d.content = "written by a try"  # type: ignore[attr-defined]
    return labels


async def test_a_run_of_ten_tries_hashes_twice(rig) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG), [db])
    labels = await _run(copies, 10)
    await copies.close()
    assert [label["check"] for label in labels] == ["kept", "full"] + ["quick"] * 8
    assert rig.events.count("digest postgres-app") == 2  # after the first put back, and the end
    assert all(label["problem"] is None for label in labels) and copies.late == []


async def test_every_try_hashes_each_put_back_when_set(rig) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG), [db], verify="every_try")
    labels = await _run(copies, 4)
    await copies.close()
    assert [label["check"] for label in labels] == ["kept", "full", "full", "full"]
    assert rig.events.count("digest postgres-app") == 3


async def test_a_database_not_made_again_fails_the_cheap_check(rig) -> None:
    db = MemoryDriver(rig.events, "postgres-app", "none", generation=True)
    copies = await rig.copies(_plan(PG), [db])
    await _run(copies, 2)
    db.broken = True
    (label,) = await _run(copies, 1)
    assert label["check"] == "quick" and label["problem"] == "reset didn't hold: postgres-app"


async def test_a_broken_restore_is_caught_at_the_end_and_its_tries_read_unheld(rig) -> None:
    vol = MemoryDriver(rig.events, "volume-data", "services", generation=False)
    copies = await rig.copies(_plan(PG), [vol])
    good = await _run(copies, 2)
    vol.broken = True
    cheap = await _run(copies, 3)
    # The cheap check can't see inside a file: each one passed.
    assert all(label["problem"] is None for label in cheap)
    await copies.close()
    assert all("end-of-run check found volume-data" in label["problem"] for label in cheap)
    assert copies.late == cheap
    assert all(label["problem"] is None for label in good)


# The rehearsal proves the hot path, once ---------------------------------------------------------


class Runner:
    def __init__(self, drivers: list[MemoryDriver], *, breaks: bool = False) -> None:
        self.drivers = drivers
        self.breaks = breaks
        self.events: list[str] = []
        self.hot_puts = 0

    async def start(self) -> None:
        self.events.append("start")

    async def stop(self) -> None:
        self.events.append("stop")

    async def probe(self, canary: str) -> None:
        self.events.append("probe")
        for d in self.drivers:
            d.content = canary

    def watch_roots(self) -> list[Path]:
        return []

    async def search(self, canary: str) -> list[str]:
        return []

    async def hot_stop(self) -> None:
        self.events.append("hot stop")

    async def hot_start(self) -> None:
        self.events.append("hot start")
        if self.breaks:
            raise hot.NoAnswer("the app didn't answer")


class Holding(MemoryDriver):
    def holding(self, canary: str) -> list[str]:
        return [self.name] if canary in self.content else []

    def covers(self, path: str) -> bool:
        return path == self.name


async def test_the_rehearsal_proves_the_put_back_with_the_app_up(tmp_path) -> None:
    db = Holding([], "postgres-app", "none", generation=True)
    runner = Runner([db])
    result = await rehearse([db], runner, tmp_path / "kept", hot=True)
    assert result.ok and result.hot == "proven"
    assert runner.events.count("probe") == 2 and runner.events.count("hot start") == 2
    assert "runs don't restart it" in result.report()


async def test_an_app_that_breaks_under_a_hot_put_back_is_recorded_as_needing_a_restart(
    tmp_path,
) -> None:
    db = Holding([], "postgres-app", "none", generation=True)
    result = await rehearse([db], Runner([db], breaks=True), tmp_path / "kept", hot=True)
    assert result.ok and result.hot == "failed"
    assert "stop and start it instead" in result.report()
    plan = _plan(PG, hot_=None)
    iso._proven(plan, result)
    assert plan.status == "proven" and plan.hot == "failed"
    assert hot.level(plan, [db], FakeCompose([], tmp_path)) == "all"


async def test_without_hot_the_rehearsal_is_as_it_was(tmp_path) -> None:
    db = Holding([], "postgres-app", "none", generation=True)
    runner = Runner([db])
    result = await rehearse([db], runner, tmp_path / "kept")
    assert result.ok and result.hot is None and runner.events.count("probe") == 1
    plan = _plan(PG, hot_=None)
    iso._proven(plan, result)
    assert plan.hot is None


# B4: copies start side by side ----------------------------------------------------------------


async def test_copies_start_together_three_at_a_time(config, monkeypatch, tmp_path) -> None:
    """Deterministic: the copies' starts wait on a gate the test opens, so what is asserted is
    how many start at once and in what order, never how long it took."""
    import asyncio

    going = {"now": 0, "most": 0}
    started: list[int] = []
    gate = asyncio.Event()

    class Slow:
        url = "http://127.0.0.1:9"

        def __init__(self, slot: int) -> None:
            self.slot = slot
            self.up = False

        async def start(self) -> None:
            if self.up:  # started again after its state was kept: not a copy starting
                return
            self.up = True
            started.append(self.slot)
            going["now"] += 1
            going["most"] = max(going["most"], going["now"])
            await gate.wait()
            going["now"] -= 1
            if self.slot == 3:
                raise isolated.LaunchError("slot 3 didn't start")

        async def stop(self) -> None:
            pass

    async def stores_on(*a: Any, **k: Any) -> list[Any]:
        return []

    async def until(check: Any) -> None:
        for _ in range(2000):
            if check():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("never got there")

    monkeypatch.setattr(isolated, "copy_of", lambda spec, target, slot, *a, **k: Slow(slot))
    monkeypatch.setattr(isolated, "stores_on", stores_on)
    monkeypatch.setattr(isolated, "adapter_on", lambda *a, **k: FakeAdapter([]))
    monkeypatch.setattr(isolated, "work_folder", lambda *a: tmp_path / "work")
    monkeypatch.setattr(isolated, "plan_spec", lambda *a: {"kind": "process"})
    copies = isolated.Copies(config, "bot", isolated.Footing("proven", _plan(PG), 1))
    going_on = asyncio.create_task(copies.start(6))
    # The first three start together, and no fourth starts while they are going.
    await until(lambda: len(started) == 3)
    for _ in range(10):
        await asyncio.sleep(0.005)
    assert started == [0, 1, 2] and going["now"] == 3
    gate.set()
    with pytest.raises(isolated.LaunchError, match="slot 3"):
        await going_on
    assert going["most"] == 3
    assert started == [0, 1, 2, 3, 4, 5]
    # Every copy that was started is there for close, in slot order, the failed one too.
    assert [s.index for s in copies.slots] == [0, 1, 2, 3, 4, 5]


def test_isolation_verify_is_run_or_every_try(tmp_path) -> None:
    from evalution.config import ConfigError

    def with_verify(value: str) -> Any:
        (tmp_path / "evalution.yaml").write_text(
            yaml.safe_dump({"targets": {}, "isolation": {"verify": value}})
        )
        return load(tmp_path / "evalution.yaml")

    assert with_verify("every_try").isolation.verify == "every_try"
    assert with_verify("run").isolation.verify == "run"
    with pytest.raises(ConfigError, match=r"isolation\.verify"):
        with_verify("sometimes")


# The copy stays up between runs ------------------------------------------------------------------


@pytest.fixture
def parked(rig, config, monkeypatch):
    """Runs on the rig whose copies are parked at close; the images each copy runs are chosen."""
    images = {"now": frozenset({("app", "sha256:one")})}

    async def image_ids(copy):
        return getattr(copy, "ran", images["now"])

    monkeypatch.setattr(isolated, "image_ids", image_ids)

    async def stores_on(plan, copy, *, roots=(), notes=None, seed=None, stage=None):
        copy.ran = images["now"]  # what the started copy runs
        return [MemoryDriver(rig.events, "postgres-app", "none", generation=True)]

    monkeypatch.setattr(isolated, "stores_on", stores_on)

    async def run(version: int = 1, slots: int = 1) -> isolated.Copies:
        copies = isolated.Copies(config, "bot", isolated.Footing("proven", _plan(PG), version))
        await copies.start(slots)
        return copies

    yield SimpleNamespace(run=run, images=images, events=rig.events)
    isolated._PARKED.clear()


async def test_a_second_run_reuses_the_copy_left_running(parked) -> None:
    await (await parked.run()).close(keep=True)
    assert "take down" not in parked.events
    parked.events.clear()
    second = await parked.run()
    assert "copy start" not in parked.events and "take down" not in parked.events
    assert "put_back postgres-app" in parked.events  # put back first, then played
    assert second.slots[0].adapter is not None and second.slots[0].fresh
    await second.close(keep=True)
    assert "take down" not in parked.events


async def test_new_image_ids_take_the_copy_down_and_start_a_new_one(parked) -> None:
    await (await parked.run()).close(keep=True)
    parked.events.clear()
    parked.images["now"] = frozenset({("app", "sha256:two")})
    await parked.run()
    assert parked.events.index("take down") < parked.events.index("copy start")


async def test_a_new_plan_version_takes_the_copy_down_and_starts_a_new_one(parked) -> None:
    await (await parked.run(version=1)).close(keep=True)
    parked.events.clear()
    await parked.run(version=2)
    assert parked.events.index("take down") < parked.events.index("copy start")


async def test_two_copies_are_reused_slot_by_slot(parked) -> None:
    await (await parked.run(slots=2)).close(keep=True)
    parked.events.clear()
    second = await parked.run(slots=2)
    assert "copy start" not in parked.events and "take down" not in parked.events
    assert [s.index for s in second.slots] == [0, 1]
    await second.close(keep=True)


async def test_the_server_stopping_takes_the_copy_down(parked) -> None:
    await (await parked.run()).close(keep=True)
    parked.events.clear()
    await isolated.drop_all()
    assert parked.events.count("take down") == 1 and not isolated._PARKED


# The page's browser stays with a parked copy ---------------------------------------------------

PAGE_MAP = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {"in": {"role": "log", "name": "Messages"}},
}


class FakeBrowser:
    """A browser that says when it was closed."""

    def __init__(self, events: list[str], n: int) -> None:
        self.events, self.n, self.open = events, n, True

    def is_connected(self) -> bool:
        return self.open

    async def close(self) -> None:
        self.open = False
        self.events.append(f"browser {self.n} closed")


@pytest.fixture
def paged(parked, config, monkeypatch):
    """The parked rig's target played through its page alone, in browsers that are only counted."""
    from dataclasses import replace

    from evalution.runs.page import target as page_target

    launched: list[FakeBrowser] = []

    class Chromium:
        executable_path = __file__  # a file that is there: Playwright's own Chromium

        async def launch(self, **_: Any) -> FakeBrowser:
            launched.append(FakeBrowser(parked.events, len(launched) + 1))
            parked.events.append(f"browser {len(launched)} launched")
            return launched[-1]

    class Playwright:
        chromium = Chromium()

        async def stop(self) -> None:
            return None

    class Driver:
        async def start(self) -> Playwright:
            return Playwright()

    monkeypatch.setattr(
        page_target, "_api", lambda: SimpleNamespace(async_playwright=Driver, Error=Exception)
    )
    bot = config.targets["bot"]

    def mapped(page: dict[str, Any]) -> None:
        options = {**bot.options, "adapter": "page", "page": page}
        config.targets["bot"] = replace(bot, adapter="page", options=options)

    mapped(PAGE_MAP)

    async def run_once(**kwargs: Any) -> isolated.Copies:
        """A run whose page target opens its browser, as a page try would."""
        copies = await parked.run(**kwargs)
        for slot in copies.slots:
            await slot.adapter._launched()
        return copies

    yield SimpleNamespace(run=run_once, launched=launched, mapped=mapped, parked=parked)
    config.targets["bot"] = bot


async def test_two_runs_on_a_parked_copy_launch_the_browser_once(paged) -> None:
    first = await paged.run()
    await first.close(keep=True)
    assert len(paged.launched) == 1 and paged.launched[0].open, "kept with the copy"
    second = await paged.run()
    assert len(paged.launched) == 1, "the second run launched another browser"
    assert second.slots[0].adapter._browser is paged.launched[0]
    await second.close(keep=True)
    # The server stopping takes the browser down with the copy.
    await isolated.drop_all()
    assert not paged.launched[0].open


async def test_a_replaced_copy_or_a_changed_map_launches_a_new_browser(paged) -> None:
    await (await paged.run()).close(keep=True)
    # New images: the copy is taken down, and its browser with it.
    paged.parked.images["now"] = frozenset({("app", "sha256:two")})
    await (await paged.run()).close(keep=True)
    assert len(paged.launched) == 2 and not paged.launched[0].open and paged.launched[1].open
    # The same copy, another page map: another browser.
    paged.mapped({**PAGE_MAP, "url": "{copy_page}/other"})
    await (await paged.run()).close(keep=True)
    assert len(paged.launched) == 3 and not paged.launched[1].open and paged.launched[2].open
    # A run that doesn't keep its copy closes its browser.
    await (await paged.run()).close()
    assert not paged.launched[2].open and len(paged.launched) == 3
