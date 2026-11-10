"""Putting a copy back on real Docker (plan B1, B2, B5, B8): what ``test_isolation_hot.py`` shows
with fakes, shown on a compose app with Postgres, an HTTP app and a named volume
(``fixtures/toy_compose_app.py``). Every project, container and volume is ``eval-test-*`` and is
taken down by the test. Skipped where Docker or the two images aren't here.

Run with ``-s`` to see the measured put back times."""

from __future__ import annotations

import importlib.util
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from evalution.config import load
from evalution.isolation import hot
from evalution.isolation import isolate as iso
from evalution.isolation.drivers import PostgresStore, VolumeStore
from evalution.isolation.launch import ComposeCopy, copies
from evalution.isolation.plan import Plan, PlannedStore
from evalution.isolation.rehearse import rehearse
from evalution.runs import isolated

_spec = importlib.util.spec_from_file_location(
    "toy_compose_app", Path(__file__).parent / "fixtures" / "toy_compose_app.py"
)
assert _spec is not None and _spec.loader is not None
toy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(toy)

pytestmark = pytest.mark.skipif(
    not toy.ready(), reason="needs Docker, postgres:16-alpine and alpine:3.20"
)

VOLUME = PlannedStore("volume-notes", "volume", "volume:notes", strategy="relocate")


def _pg(project: str) -> PlannedStore:
    return PlannedStore("postgres-app", "postgres", f"{project}-db-1:app", strategy="relocate")


def _plan(*stores: PlannedStore, hot_: str | None = "proven") -> Plan:
    return Plan(target="bot", stores=list(stores), status="proven", hot=hot_)


class Adapter:
    """The copy's adapter: the tests talk to the app themselves."""

    async def open(self, run: str) -> str:
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


class Rig:
    """Real compose copies, and the run's :class:`~evalution.runs.isolated.Copies` on them."""

    def __init__(self, config, monkeypatch, tmp_path: Path) -> None:
        self.config = config
        self.monkeypatch = monkeypatch
        self.tmp = tmp_path
        self.made: list[ComposeCopy] = []

    def copy(self, mode: str = "fresh", mounts: tuple[str, ...] = ()) -> ComposeCopy:
        folder = self.tmp / f"app-{len(self.made)}"
        folder.mkdir()
        (folder / "compose.yaml").write_text(toy.compose(mode, mounts))
        made = ComposeCopy(
            [folder / "compose.yaml"], None, f"test-{uuid.uuid4().hex[:8]}", 0,
            self.tmp / "eval", health="/health",
        )  # fmt: skip
        assert made.project.startswith("eval-test-")
        self.made.append(made)
        return made

    async def copies(self, plan: Plan, copy: ComposeCopy, **settings: Any) -> isolated.Copies:
        conf = self.config
        if settings:
            conf = replace(conf, isolation=replace(conf.isolation, **settings))
        self.monkeypatch.setattr(isolated, "copy_of", lambda *a, **k: copy)
        out = isolated.Copies(conf, "bot", isolated.Footing("proven", plan, 1))
        await out.start(1)
        return out


@pytest.fixture
async def rig(config, monkeypatch, tmp_path):
    async def no_sweep(gone):  # only this test's copies are ever touched
        return []

    monkeypatch.setattr(isolated, "sweep", no_sweep)
    monkeypatch.setattr(isolated, "adapter_on", lambda *a, **k: Adapter())
    monkeypatch.setattr(isolated, "work_folder", lambda *a: tmp_path / "work")
    monkeypatch.setattr(isolated, "plan_spec", lambda *a: {"kind": "compose"})
    made = Rig(config, monkeypatch, tmp_path)
    try:
        yield made
    finally:
        running = await copies()
        for copy in made.made:
            if copy.project in running:
                await iso.take_down(copy)
            else:
                await iso.clear_extras(copy.project)  # a golden volume a failed test left
        running = await copies()
        assert not [c.project for c in made.made if c.project in running]


def _app(copy: ComposeCopy, page: str) -> str:
    """What the copy's app answers on *page*; a status of 400 or more fails the test."""
    r = toy.get(f"{copy.url}/{page}")
    assert r.status_code < 400, f"{page}: {r.status_code} {r.text}"
    return r.text


def _container(copy: ComposeCopy, service: str) -> str:
    return f"{copy.project}-{service}-1"


def _left(left: list[str]) -> list[str]:
    """What a copy's take down left of its own (others' containers aren't counted)."""
    return [x for x in left if "not removed" not in x]


# B1: put back with the app up -------------------------------------------------------------------


async def test_postgres_is_put_back_with_the_app_up_and_the_app_answers_unrestarted(rig) -> None:
    copy = rig.copy("fresh")
    runs = await rig.copies(_plan(_pg(copy.project)), copy)
    slot = runs.slots[0]
    assert slot.how == "none"
    (pg,) = slot.drivers
    assert isinstance(pg, PostgresStore)
    assert (await runs.restore(0))["check"] == "kept"
    app, db = _container(copy, "app"), _container(copy, "db")
    was = toy.started(app), toy.started(db)
    oid = pg.generation()

    assert _app(copy, "write/marker-a") == "kept" and _app(copy, "has/marker-a") == "yes"
    label = await runs.restore(0)
    assert label["problem"] is None and label["check"] == "full" and "note" not in label
    assert pg.generation() != oid  # dropped, its connections ended, and made again
    # The marker is gone and the app answers its next request: no restart.
    assert _app(copy, "has/marker-a") == "no"
    assert (toy.started(app), toy.started(db)) == was

    # Three put backs with the app up, then three with the copy stopped and started.
    seconds: dict[str, list[float]] = {"none": [], "all": []}
    for how in ("none", "all"):
        slot.how = how
        for i in range(3):
            _app(copy, f"write/timed-{how}-{i}")
            started = time.monotonic()
            label = await runs.restore(0)
            took = time.monotonic() - started
            assert label["problem"] is None, label
            assert _app(copy, f"has/timed-{how}-{i}") == "no"
            seconds[how].append(took)
    assert toy.started(app) != was[0]  # the stop and start did restart it
    print(
        "\nput back with the app up (s): "
        + ", ".join(f"{s:.2f}" for s in seconds["none"])
        + "\nput back with a stop and start (s): "
        + ", ".join(f"{s:.2f}" for s in seconds["all"])
    )
    assert _left(await runs.close()) == []


async def test_an_app_holding_its_connection_fails_the_hot_proof_and_runs_restart_it(
    rig, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(hot, "ANSWER_SECONDS", 6)
    copy = rig.copy("held")
    await copy.start()
    plan = _plan(_pg(copy.project), hot_=None)
    drivers = await iso.drivers_for(plan, copy)
    how = hot.wanted(plan, drivers, copy)
    assert how == "none"

    async def probe(canary: str) -> None:
        assert _app(copy, f"write/{canary}") == "kept"

    keep = [d.container for d in drivers if isinstance(d, PostgresStore)]
    runner = iso.ComposeRunner(copy, probe, keep, how)
    result = await rehearse(drivers, runner, tmp_path / "kept", ignore=plan.exclude, hot=True)
    assert result.ok, result.report()
    assert result.hot == "failed", result.report()
    assert "stop and start it instead" in result.report()
    iso._proven(plan, result)
    assert plan.status == "proven" and plan.hot == "failed"
    assert _app(copy, "health") == "ok"  # the rehearsal left it started again
    await iso.take_down(copy)

    # A run on that plan stops and starts the app around every put back.
    again = rig.copy("held")
    runs = await rig.copies(plan, again)
    assert runs.slots[0].how == "all"
    await runs.restore(0)
    app = _container(again, "app")
    was = toy.started(app)
    _app(again, "write/marker-b")
    label = await runs.restore(0)
    assert label["problem"] is None and "note" not in label
    assert toy.started(app) != was
    assert _app(again, "has/marker-b") == "no"
    await runs.close()


async def test_a_run_whose_app_breaks_under_a_hot_put_back_restarts_it_from_then_on(
    rig, monkeypatch
) -> None:
    monkeypatch.setattr(hot, "ANSWER_SECONDS", 6)
    copy = rig.copy("held")
    # Proven with the app up before the app came to hold its connection.
    runs = await rig.copies(_plan(_pg(copy.project)), copy)
    slot = runs.slots[0]
    assert slot.how == "none"
    await runs.restore(0)
    app = _container(copy, "app")
    was = toy.started(app)
    _app(copy, "write/marker-c")
    label = await runs.restore(0)
    assert label["problem"] is None
    assert "stopped and started" in label["note"]
    assert slot.how == "all" and toy.started(app) != was
    assert _app(copy, "has/marker-c") == "no"
    _app(copy, "write/marker-d")
    second = await runs.restore(0)
    assert second["problem"] is None and "note" not in second
    assert _app(copy, "has/marker-d") == "no"
    await runs.close()


# B5: a volume kept inside Docker ----------------------------------------------------------------


def _files(copy: ComposeCopy) -> str:
    return toy.docker(
        "exec", _container(copy, "worker"), "sh", "-c",
        "cd /data && find . | sort && cat a.txt sub/c.txt 2>&1; true",
    )  # fmt: skip


async def test_a_volume_is_kept_in_a_golden_volume_and_only_its_services_stop(rig) -> None:
    copy = rig.copy("fresh")
    runs = await rig.copies(_plan(_pg(copy.project), VOLUME), copy)
    slot = runs.slots[0]
    assert slot.how == "services" and slot.services == ["worker"]
    vol = next(d for d in slot.drivers if isinstance(d, VolumeStore))
    assert vol.volume == f"{copy.project}_notes"
    assert vol.golden == f"{copy.project}_notes_golden"
    toy.docker("volume", "inspect", vol.golden)  # kept inside Docker
    await runs.restore(0)
    kept = _files(copy)
    assert "./a.txt" in kept and "inner" in kept
    names = [_container(copy, s) for s in ("app", "db", "worker")]
    was = {n: toy.started(n) for n in names}

    toy.docker(
        "exec", _container(copy, "worker"), "sh", "-c",
        "echo changed > /data/a.txt && rm -r /data/sub && echo new > /data/b.txt"
        " && echo x > /data/.hidden",
    )  # fmt: skip
    _app(copy, "write/marker-e")
    assert _files(copy) != kept
    label = await runs.restore(0)
    assert label["problem"] is None and label["check"] == "full"
    assert _files(copy) == kept
    assert _app(copy, "has/marker-e") == "no"
    app, db, worker = names
    assert toy.started(app) == was[app] and toy.started(db) == was[db]
    assert toy.started(worker) != was[worker]  # only the service that mounts it stopped
    await runs.close()
    with pytest.raises(subprocess.CalledProcessError):
        toy.docker("volume", "inspect", vol.golden)


async def test_the_rehearsal_proves_a_put_back_that_stops_only_the_volumes_services(
    rig, tmp_path
) -> None:
    copy = rig.copy("fresh")
    await copy.start()
    plan = _plan(_pg(copy.project), VOLUME, hot_=None)
    drivers = await iso.drivers_for(plan, copy)
    how = hot.wanted(plan, drivers, copy)
    services = await hot.services_for(copy, drivers)
    assert (how, services) == ("services", ["worker"])
    app = _container(copy, "app")
    was = toy.started(app)

    async def probe(canary: str) -> None:
        _app(copy, f"write/{canary}")
        line = f"echo {canary} > /data/memory_index"
        toy.docker("exec", _container(copy, "worker"), "sh", "-c", line)

    keep = [d.container for d in drivers if isinstance(d, PostgresStore)]
    runner = iso.ComposeRunner(copy, probe, keep, how, services)
    result = await rehearse(drivers, runner, tmp_path / "kept", ignore=plan.exclude, hot=True)
    assert result.ok and result.hot == "proven", result.report()
    assert "runs don't restart it" in result.report()
    assert toy.started(app) != was  # the rehearsal's own stops restart it, as they always did
    golden = f"{copy.project}_notes_golden"
    with pytest.raises(subprocess.CalledProcessError):
        toy.docker("volume", "inspect", golden)  # the rehearsal dropped what it kept
    await iso.take_down(copy)


# B2: the quick check, and the full hash at the end ----------------------------------------------


async def test_a_skipped_store_is_caught_by_the_quick_check_or_the_end_of_run_hash(rig) -> None:
    copy = rig.copy("fresh")
    runs = await rig.copies(_plan(_pg(copy.project), VOLUME), copy)
    slot = runs.slots[0]
    pg = next(d for d in slot.drivers if isinstance(d, PostgresStore))
    vol = next(d for d in slot.drivers if isinstance(d, VolumeStore))
    worker = _container(copy, "worker")

    def a_try(marker: str, word: str) -> None:
        _app(copy, f"write/{marker}")
        # The same size as what was kept ("kept\n"): only a full hash sees the change.
        toy.docker("exec", worker, "sh", "-c", f"echo {word} > /data/a.txt")

    assert (await runs.restore(0))["check"] == "kept"
    a_try("m1", "KEPT")
    full = await runs.restore(0)
    assert full["check"] == "full" and full["problem"] is None

    # The volume isn't put back: its top-level listing is as kept, so the quick check passes.
    vol.put_back = lambda kept: None  # type: ignore[method-assign]
    a_try("m2", "kxpt")
    quick = await runs.restore(0)
    assert quick["check"] == "quick" and quick["problem"] is None

    # The database isn't put back either: it wasn't made again, so the quick check fails.
    pg.put_back = lambda kept: None  # type: ignore[method-assign]
    a_try("m3", "kypt")
    caught = await runs.restore(0)
    assert caught["check"] == "quick"
    assert caught["problem"] == "reset didn't hold: postgres-app"
    del pg.put_back

    await runs.close()  # the end-of-run full hash
    assert "end-of-run check found volume-notes" in quick["problem"]
    assert runs.late == [quick]
    assert full["problem"] is None


# B8: a folder shared with the person's app ------------------------------------------------------


async def test_a_probe_writing_into_a_shared_writable_folder_refuses_the_plan(
    rig, tmp_path
) -> None:
    share, source = tmp_path / "share", tmp_path / "src"
    share.mkdir()
    source.mkdir()
    (source / "main.txt").write_text("the app's code\n")
    copy = rig.copy("fresh", (f"{share}:/share", f"{source}:/src"))
    await copy.start()
    shared = iso.shared_of(copy)
    assert sorted((s["path"], s["service"], s["writable"]) for s in shared) == [
        (str(share), "worker", True),
        (str(source), "worker", True),
    ]
    assert "Shared with your app (not copied)" in iso.summary(copy)
    writable = [s["path"] for s in shared if s["writable"]]
    plan = _plan(_pg(copy.project), hot_=None)
    drivers = await iso.drivers_for(plan, copy)
    keep = [d.container for d in drivers if isinstance(d, PostgresStore)]

    # A source folder mounted for live reload, which no test writes in: said, never refused.
    async def into_db(canary: str) -> None:
        _app(copy, f"write/{canary}")

    runner = iso.ComposeRunner(copy, into_db, keep)
    fine = await rehearse(drivers, runner, tmp_path / "k1", ignore=plan.exclude, shared=writable)
    assert fine.ok and fine.shared == [], fine.report()

    # A test that writes into the shared folder: refused, the place named.
    async def into_share(canary: str) -> None:
        _app(copy, f"write/{canary}")
        line = f"echo {canary} >> /share/log"
        toy.docker("exec", _container(copy, "worker"), "sh", "-c", line)

    runner = iso.ComposeRunner(copy, into_share, keep)
    no = await rehearse(drivers, runner, tmp_path / "k2", ignore=plan.exclude, shared=writable)
    assert not no.ok
    assert no.shared == [f"{share}/log"] and no.leaks == [f"{share}/log"]
    assert "Refused: a test wrote into a folder your app shares" in no.report()
    iso._proven(plan, no)
    assert plan.status == "failed" and plan.leaks == [f"{share}/log"]
    await iso.take_down(copy)
