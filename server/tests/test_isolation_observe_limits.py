"""The probe's reach on this computer: a broad system folder (the temp folder, the home folder) is
never watched whole, the file budget is shared out per folder watched, and what was cut short is
said, on the observation, the plan's report and the API."""

import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load
from evalution.isolation import discover, observe
from evalution.isolation import isolate as iso
from evalution.isolation.discover import Store
from evalution.isolation.plan import Plan, PlanStore
from evalution.isolation.rehearse import Rehearsal, Step

TOY = Path(__file__).parent / "fixtures" / "toy_store_app"


def _fill(folder: Path, count: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (folder / f"unrelated-{i:05d}.tmp").write_bytes(b"")
    return folder


@pytest.fixture
def temp_folder(tmp_path, monkeypatch):
    """A folder of the test's own made this process's system temp folder."""
    folder = (tmp_path / "tmp").resolve()
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    monkeypatch.setenv("TMPDIR", str(folder))
    return folder


# Broad folders ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/tmp",
        "/private/tmp",
        "/var/tmp",
        "/usr",
        "/etc",
        "/dev",
        "/Library",
        "~",
        "~/Library",
        "~/Library/Caches",
        "~/.cache",
        "~/Downloads",
    ],
)
def test_system_and_home_folders_are_broad(path: str) -> None:
    assert observe.broad(Path(path).expanduser())


def test_the_temp_folder_and_the_folders_above_it_are_broad(temp_folder) -> None:
    assert observe.broad(temp_folder)
    assert observe.broad(temp_folder.parent)
    assert observe.broad(Path(tempfile.gettempdir()))
    # macOS's per-user temp folders, whoever's they are.
    assert observe.broad(Path("/private/var/folders/qv/abc123/T"))
    assert observe.broad(Path("/var/folders/qv"))


def test_a_folder_of_the_apps_own_is_not_broad(temp_folder, tmp_path) -> None:
    assert not observe.broad(temp_folder / "myapp")
    assert not observe.broad(tmp_path / "app")
    assert not observe.broad(Path.home() / "projects" / "app")
    assert not observe.broad(Path("/private/var/folders/qv/abc123/T/myapp"))


def test_a_file_held_open_in_a_broad_folder_is_watched_alone(temp_folder, tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (temp_folder / "pytest-capture.txt").write_text("x")
    (temp_folder / "myapp").mkdir()
    (temp_folder / "myapp" / "app.lock").write_text("")
    opened = [str(temp_folder / "pytest-capture.txt"), str(temp_folder / "myapp" / "app.lock")]
    roots = observe.roots_for(app, opened, [])
    assert temp_folder not in roots
    assert temp_folder / "pytest-capture.txt" in roots  # the file only
    assert temp_folder / "myapp" in roots  # an app's own folder in the temp folder: whole
    assert roots[0] == app.resolve()


def test_a_store_named_in_a_broad_folder_is_watched_as_that_file(temp_folder, tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    named = Store("sqlite", f"sqlite:///{temp_folder}/memory.db", ["app.py:3"])
    roots = observe.roots_for(app, [], [named])
    assert roots == [app.resolve(), temp_folder / "memory.db"]


def test_the_project_and_named_stores_come_before_open_files(tmp_path) -> None:
    app, named, opened = tmp_path / "app", tmp_path / "named", tmp_path / "opened"
    for folder in (app, named, opened):
        folder.mkdir()
    (opened / "x.log").write_text("")
    roots = observe.roots_for(app, [str(opened / "x.log")], [Store("dir", str(named / "f"))])
    assert roots == [app.resolve(), named.resolve(), opened.resolve()]


# The budget, per folder -------------------------------------------------------------------------


def test_every_root_gets_its_share_and_a_cut_one_is_named(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(observe, "MAX_FILES", 30)
    big = _fill(tmp_path / "big", 50)
    small = _fill(tmp_path / "small", 3)
    middle = _fill(tmp_path / "middle", 10)
    snap = observe.snapshot([big, small, middle])
    assert sum(1 for p in snap if p.startswith(str(small) + "/")) == 3
    assert sum(1 for p in snap if p.startswith(str(middle) + "/")) == 10
    assert sum(1 for p in snap if p.startswith(str(big) + "/")) == 17  # what the others left
    assert snap.unread == [{"root": str(big), "seen": 50, "limit": 17}]


def test_a_root_read_whole_is_not_unread(tmp_path) -> None:
    snap = observe.snapshot([_fill(tmp_path / "a", 5)])
    assert len(snap) == 5 and snap.unread == []


def test_a_file_root_is_read_with_its_sidecars_only(tmp_path) -> None:
    crowded = _fill(tmp_path / "crowded", 40)
    (crowded / "memory.db").write_text("a")
    (crowded / "memory.db-wal").write_text("b")
    snap = observe.snapshot([crowded / "memory.db"])
    assert sorted(snap) == [str(crowded / "memory.db"), str(crowded / "memory.db-wal")]


async def test_a_crowded_folder_cant_starve_the_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(observe, "MAX_FILES", 200)
    # Sorted first, and so read first, before the budget was shared: it used it all up.
    crowded = _fill(tmp_path / "a-crowded", 1000)
    app = tmp_path / "zz-app"
    (app / "data").mkdir(parents=True)
    named = Store("sqlite", str(crowded / "app.db"), ["app.py:1"])
    canary = "eval-canary-share"

    async def act() -> None:
        (app / "data" / "notes.txt").write_text(canary)

    obs = await observe.watch(app, [named], act, canary)
    assert str(app / "data" / "notes.txt") in obs.canary
    # 200 read before the probe, 199 after (the project's new file took one): the least is said.
    assert obs.unread == [{"root": str(crowded), "seen": 1000, "limit": 199}]


# The toy app, holding a file open in a crowded temp folder ---------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def crowded_toy(temp_folder, tmp_path, monkeypatch):
    """The toy app, its output held open in a temp folder of 3,000 unrelated files, with a
    budget of 1,000: its app folder, its outside store, its port and its process."""
    monkeypatch.setattr(observe, "MAX_FILES", 1000)
    _fill(temp_folder, 3000)
    # Deeper than the temp folder, so the old probe read the temp folder first.
    app = tmp_path / "code" / "projects" / "app"
    shutil.copytree(TOY, app)
    (app / "data").mkdir()
    elsewhere = tmp_path / "home" / "me" / ".toyagent"
    elsewhere.mkdir(parents=True)
    port = _free_port()
    with (temp_folder / "toy-output.log").open("w") as out:
        proc = subprocess.Popen(
            [sys.executable, str(app / "app.py"), str(port), str(elsewhere / "memory.db")],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
        )
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield app, elsewhere, port, proc
    proc.terminate()
    proc.wait(5)


@pytest.mark.skipif(shutil.which("lsof") is None, reason="needs lsof")
async def test_a_file_open_in_a_crowded_temp_folder_doesnt_hide_the_stores(
    crowded_toy, temp_folder
) -> None:
    """The defect: the toy held its output open in the temp folder, the probe watched that whole
    folder, ran out of files there, and never read the app's own. Thousands of unrelated files
    there now change nothing."""
    app, elsewhere, port, proc = crowded_toy
    canary = "eval-canary-crowded"

    async def act() -> None:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"http://127.0.0.1:{port}/", json={"message": canary})
            r.raise_for_status()

    obs = await observe.watch(app, discover.scan(app), act, canary, pid=proc.pid)

    assert str(temp_folder / "toy-output.log") in obs.open_files
    assert str(temp_folder) not in obs.roots
    assert str(temp_folder / "toy-output.log") in obs.roots
    assert obs.unread == []
    held = sorted(obs.canary)
    assert str(app / "data" / "chat.db") in held
    assert str(app / "cache" / "notes.txt") in held
    assert any(p.startswith(str(elsewhere)) for p in held), held


# Said: the plan, its report, the evidence and the API ------------------------------------------


UNREAD = [{"root": "/srv/app/uploads", "seen": 25000, "limit": 6000}]


def test_the_rehearsals_account_says_the_search_was_cut_short() -> None:
    plan = Plan(target="bot", unread=list(UNREAD))
    result = Rehearsal(ok=True, steps=[Step("starting state kept", True)], seconds=3)
    said = iso._account(plan, result, [], "eval-bot-0", [])
    assert "The search for stores was incomplete" in said
    assert "/srv/app/uploads (read 6,000 of 25,000 files)" in said
    clean = iso._account(Plan(target="bot"), result, [], "eval-bot-0", [])
    assert "incomplete" not in clean


def test_what_was_cut_short_is_kept_on_the_plan_once_per_folder() -> None:
    plan = Plan(target="bot", unread=list(UNREAD))
    iso.note_unread(plan, [{"root": "/srv/app/uploads", "seen": 26000, "limit": 5000}])
    iso.note_unread(plan, [{"root": "/var/lib/app", "seen": 9000, "limit": 5000}])
    assert plan.unread == [
        {"root": "/srv/app/uploads", "seen": 26000, "limit": 5000},
        {"root": "/var/lib/app", "seen": 9000, "limit": 5000},
    ]


def test_the_evidence_kept_names_what_was_not_fully_read() -> None:
    seen = observe.Observation(["/srv/app"], unread=list(UNREAD))
    assert iso.evidence([], [], seen)["observation"]["unread"] == UNREAD


def test_an_old_plan_reads_with_nothing_unread() -> None:
    text = Plan(target="bot").to_json().replace('"unread": [],', "")
    assert '"unread"' not in text
    assert Plan.from_json(text).unread == []


def test_the_api_names_what_the_search_did_not_fully_read(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    config = load(tmp_path / "evalution.yaml")
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/targets/bot/isolation").json()["unread"] == []  # no plan yet
        PlanStore(config.data_dir).save(Plan(target="bot", status="proven", unread=UNREAD))
        assert client.get("/v1/targets/bot/isolation").json()["unread"] == UNREAD
