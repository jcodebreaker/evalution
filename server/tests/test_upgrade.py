"""``evalution backup | upgrade | restore``: tests and past reports survive new code, the copy stays
private, and a restore puts back exactly what was there. Over the old run files of
``fixtures/runs``."""

import json
import os
import shlex
import shutil
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.cli import main
from evalution.config import load
from evalution.results.migrate import upcast
from evalution.results.record import SCHEMA
from evalution.results.store import FolderUse, RunLock
from evalution.results.views import summary

RUNS = Path(__file__).parent / "fixtures" / "runs"
REPO = Path(__file__).parents[2]
SECRET = "fake-secret-value-789"
OLD_RUNS = ("v0-first", "v0-second", "v0-edge")
CASE = {
    "name": "Fake greeting",
    "turns": [{"message": "Hello"}],
    "reply": {"rubric": ["Says hello"]},
}


@pytest.fixture
def home(tmp_path) -> Path:
    """A folder as an install leaves it: old runs, a test, what it learned, settings, a secret."""
    root = tmp_path / "home"
    runs = root / "data" / "runs"
    runs.mkdir(parents=True)
    for name in OLD_RUNS:
        shutil.copy(RUNS / f"{name}.json", runs / f"{name}.json")
    current = upcast(json.loads((RUNS / "v0-first.json").read_text()))
    (runs / "current.json").write_text(json.dumps({**current, "run": "current"}))
    (runs / "current.evidence.jsonl.gz").write_bytes(b"not read here")
    tests = root / "sets" / "my-app" / "main"
    tests.mkdir(parents=True)
    (tests / "set.json").write_text(json.dumps({"name": "Main"}))
    (tests / "fake-greeting.case.json").write_text(json.dumps(CASE))
    (root / "targets" / "my-app").mkdir(parents=True)
    (root / "targets" / "my-app" / "profile.md").write_text("What it learned\n")
    (root / "data" / "secrets.json").write_text(json.dumps({"my-app.key": SECRET}))
    (root / "data" / "secrets.json").chmod(0o600)
    (root / "evalution.yaml").write_text(yaml.safe_dump({"traces": {"managed": False}}))
    load(root / "evalution.yaml")  # its database, as the first start makes it
    return root


def everything(root: Path) -> dict[str, bytes | str]:
    """Every file under *root*, but the backups and the lock; a database as what it holds, which
    is what its backup keeps, without what SQLite keeps beside it."""
    found: dict[str, bytes | str] = {}
    for p in root.rglob("*"):
        if not p.is_file() or "backups" in p.parts or p.name == "run.lock":
            continue
        if p.name.endswith(("-wal", "-shm")):
            continue
        if p.suffix == ".db":
            db = sqlite3.connect(p)
            try:
                found[p.relative_to(root).as_posix()] = "\n".join(db.iterdump())
            finally:
                db.close()
        else:
            found[p.relative_to(root).as_posix()] = p.read_bytes()
    return found


def said(capsys) -> tuple[str, str]:
    found = capsys.readouterr()
    return found.out, found.err


def test_an_upgrade_keeps_tests_and_reports_and_can_be_undone(home, capsys) -> None:
    config = str(home / "evalution.yaml")
    before = everything(home)
    old = {n: json.loads((RUNS / f"{n}.json").read_text()) for n in OLD_RUNS}

    assert main(["--config", config, "upgrade"]) == 0
    out, _ = said(capsys)

    # What changed is said, and the secret isn't.
    assert "3 of 4 runs rewritten" in out and "4 of 4 runs read" in out and "1 tests read" in out
    assert SECRET not in out

    # The old runs are today's shape now, and say what they said under the old one.
    runs = home / "data" / "runs"
    for name, doc in old.items():
        stored = json.loads((runs / f"{name}.json").read_text())
        assert stored["schema"] == SCHEMA
        assert summary(stored) == summary(upcast(doc))
        assert (runs / f"{name}.json.bak").read_bytes() == (RUNS / f"{name}.json").read_bytes()
    assert (runs / "current.json").read_bytes() == before["data/runs/current.json"]
    assert (home / "sets" / "my-app" / "main" / "fake-greeting.case.json").is_file()

    # The server starts over all of it.
    with TestClient(create_app(load(home / "evalution.yaml"))) as client:
        assert client.get("/v1/health").json() == {"ok": True}

    # The backup is whole and private, the secret in it.
    (backup,) = (home / "backups").iterdir()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    for path in backup.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600), path
    assert SECRET in (backup / "data" / "secrets.json").read_text()
    assert (backup / "settings" / "evalution.yaml").is_file()
    assert not (backup / "data" / "run.lock").exists()
    manifest = (backup / "manifest.json").read_text()
    assert SECRET not in manifest

    # The command it gave is what undoes it, to the byte and the mode, the backup kept.
    command = next(line for line in out.splitlines() if "to undo all of it" in line)
    words = shlex.split(command.split(": ", 2)[2])
    assert words[-2:] == ["restore", str(backup)]
    assert main(words[1:]) == 0
    out, _ = said(capsys)
    assert everything(home) == before
    assert stat.S_IMODE((home / "data" / "secrets.json").stat().st_mode) == 0o600
    assert "4 of 4 runs read" in out and SECRET not in out
    assert not (home / "data" / "runs" / "v0-edge.json.bak").exists()

    # And a restore backs up what it replaces.
    made = sorted(p for p in (home / "backups").iterdir() if p != backup)
    assert len(made) == 1
    assert (made[0] / "data" / "runs" / "v0-edge.json.bak").is_file()


def test_a_run_that_doesnt_read_is_said_and_stops_nothing_else(home, capsys) -> None:
    (home / "data" / "runs" / "broken.json").write_text("{ not json")
    (home / "data" / "runs" / "not-a-run.json").write_text("[1, 2]")
    assert main(["--config", str(home / "evalution.yaml"), "upgrade"]) == 1
    out, _ = said(capsys)
    assert "these didn't read before it either: broken, not-a-run" in out
    assert "3 of 6 runs rewritten" in out and "4 of 6 runs read" in out
    assert (home / "data" / "runs" / "broken.json").read_text() == "{ not json"


@pytest.mark.parametrize("command", ["backup", "upgrade", "restore"])
def test_nothing_happens_while_a_run_is_going(home, capsys, command) -> None:
    config = str(home / "evalution.yaml")
    assert main(["--config", config, "backup"]) == 0  # the control: free, it works
    (backup,) = (home / "backups").iterdir()
    said(capsys)
    before = everything(home)
    args = [command, str(backup)] if command == "restore" else [command]
    lock = RunLock(home / "data" / "run.lock")
    assert lock.acquire()
    try:
        assert main(["--config", config, *args]) == 2
    finally:
        lock.release()
    assert "a run is going" in said(capsys)[1]
    assert everything(home) == before
    assert list((home / "backups").iterdir()) == [backup]


def test_a_stale_phoenix_state_file_doesnt_block_a_restore(home, capsys) -> None:
    config = str(home / "evalution.yaml")
    assert main(["--config", config, "backup"]) == 0
    (backup,) = (home / "backups").iterdir()
    (home / "data" / "phoenix").mkdir()
    state = home / "data" / "phoenix" / "serving.json"
    # Its pid, reused by now, is alive; no serve holds the folder.
    state.write_text(json.dumps({"pid": os.getpid(), "url": "http://127.0.0.1:1"}))
    assert main(["--config", config, "restore", str(backup)]) == 0
    # Control: a serve holding the folder still stops it.
    use = FolderUse(home / "data")
    assert use.serving()
    try:
        assert main(["--config", config, "restore", str(backup)]) == 2
        assert "serve is running" in said(capsys)[1]
    finally:
        use.release()


@pytest.mark.parametrize("damage", ["changed", "removed", "no manifest", "outside"])
def test_restore_refuses_a_backup_it_cant_trust(home, capsys, damage) -> None:
    config = str(home / "evalution.yaml")
    assert main(["--config", config, "backup"]) == 0
    (backup,) = (home / "backups").iterdir()
    (home / "data" / "new-since.txt").write_text("kept")
    before = everything(home)
    manifest = backup / "manifest.json"
    if damage == "changed":
        (backup / "data" / "secrets.json").write_text("{}")
    elif damage == "removed":
        (backup / "data" / "runs" / "v0-first.json").unlink()
    elif damage == "no manifest":
        manifest.unlink()
    else:
        doc = json.loads(manifest.read_text())
        doc["files"]["data/../../outside.txt"] = {"mode": 0o644, "sha256": "", "size": 0}
        manifest.write_text(json.dumps(doc))
    said(capsys)
    assert main(["--config", config, "restore", str(backup)]) == 2
    assert SECRET not in "".join(said(capsys))
    assert everything(home) == before


def test_a_database_in_data_is_copied_whole(home) -> None:
    """A store a live server may be writing through its journal: the copy holds what is committed,
    and no journal beside it."""
    traces = home / "data" / "traces"
    traces.mkdir()
    db = sqlite3.connect(traces / "spans.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE spans (id TEXT)")
    db.execute("INSERT INTO spans VALUES ('kept')")
    db.commit()
    try:
        assert (traces / "spans.db-wal").exists()
        assert main(["--config", str(home / "evalution.yaml"), "backup"]) == 0
    finally:
        db.close()
    (backup,) = (home / "backups").iterdir()
    copy = backup / "data" / "traces" / "spans.db"
    assert not copy.with_name("spans.db-wal").exists()
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    copied = sqlite3.connect(copy)
    try:
        assert copied.execute("SELECT id FROM spans").fetchall() == [("kept",)]
    finally:
        copied.close()


def test_a_backup_cant_go_inside_what_it_backs_up(home, capsys) -> None:
    config = str(home / "evalution.yaml")
    assert main(["--config", config, "backup", "--to", str(home / "data" / "copies")]) == 2
    assert "can't go inside" in said(capsys)[1]
    assert not (home / "data" / "copies").exists()


def test_what_the_page_sets_lives_in_the_data_folder(tmp_path, capsys) -> None:
    """So a container's read-only config is never written, and the backup's data holds it."""
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"traces": {"managed": False}}))
    (tmp_path / "evalution.yaml").chmod(0o400)
    options = {"send": {"url": "http://x"}, "reply": "$.reply", "done": "response"}
    body = {"name": "app", "adapter": "http", "options": options}
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        assert client.post("/v1/targets", json=body).status_code == 201
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["evalution.yaml"]
    assert "app" in load(tmp_path / "evalution.yaml").targets
    assert main(["--config", str(tmp_path / "evalution.yaml"), "backup"]) == 0
    (backup,) = (tmp_path / "backups").iterdir()
    assert (backup / "data" / "evalution.db").is_file()
    assert sorted(p.name for p in (backup / "settings").iterdir()) == ["evalution.yaml"]
    said(capsys)


@pytest.mark.parametrize("existing", [True, False])
def test_install_again_leaves_your_settings_and_data_alone(tmp_path, existing) -> None:
    """``install.sh`` with stand-ins for what it calls: no network, and it pulls nothing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("uv", "npm"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n')
        stub.chmod(0o755)
    root = tmp_path / "checkout"
    (root / "data" / "runs").mkdir(parents=True)
    (root / "data" / "runs" / "kept.json").write_text("{}")
    shutil.copy(REPO / "install.sh", root / "install.sh")
    shutil.copy(REPO / "evalution.example.yaml", root / "evalution.example.yaml")
    if existing:
        (root / "evalution.yaml").write_text("# mine\n")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    done = subprocess.run(["sh", "install.sh"], cwd=root, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (root / "data" / "runs" / "kept.json").read_text() == "{}"
    found = (root / "evalution.yaml").read_text()
    if existing:
        assert found == "# mine\n"
        assert "evalution upgrade" in done.stdout
    else:
        assert found == (REPO / "evalution.example.yaml").read_text()
    assert "git" not in log.read_text()


def test_an_upgrade_that_loses_or_changes_a_run_says_so(home, capsys, monkeypatch) -> None:
    """The check after it is a real one: a rewrite gone wrong, and what to do then."""

    def goes_wrong(runs: Path) -> list[str]:
        (runs / "v0-edge.json").write_text("{ cut off")
        doc = json.loads((runs / "current.json").read_text())
        doc["totals"] = None
        doc["cases"] = {}
        (runs / "current.json").write_text(json.dumps(doc))
        return ["v0-edge", "current"]

    monkeypatch.setattr("evalution.upgrade.migrate_dir", goes_wrong)
    assert main(["--config", str(home / "evalution.yaml"), "upgrade"]) == 1
    out, _ = said(capsys)
    assert "PROBLEM: these no longer read: v0-edge" in out
    assert "PROBLEM: these now read differently: current" in out
    assert "to undo all of it" in out


@pytest.mark.parametrize("section", ["data", "sets"])
def test_a_backup_inside_what_it_replaces_isnt_restored(home, capsys, section) -> None:
    config = str(home / "evalution.yaml")
    assert main(["--config", config, "backup"]) == 0
    (backup,) = (home / "backups").iterdir()
    inside = home / section / "kept-here"
    shutil.move(backup, inside)
    before = everything(home)
    said(capsys)
    assert main(["--config", config, "restore", str(inside)]) == 2
    assert "inside what it replaces" in said(capsys)[1]
    assert everything(home) == before and (inside / "manifest.json").is_file()
    # Control: moved out, the same backup restores.
    outside = home / "safe"
    shutil.move(inside, outside)
    assert main(["--config", config, "restore", str(outside)]) == 0


def test_restore_and_serve_refuse_each_other_even_with_serve_idle(home, capsys) -> None:
    config = str(home / "evalution.yaml")
    assert main(["--config", config, "backup"]) == 0
    (backup,) = (home / "backups").iterdir()
    said(capsys)
    with TestClient(create_app(load(home / "evalution.yaml"))):
        assert main(["--config", config, "restore", str(backup)]) == 2
        assert "serve is running" in said(capsys)[1]
        # More than one serve on a folder is as allowed as it was.
        with TestClient(create_app(load(home / "evalution.yaml"))) as second:
            assert second.get("/v1/health").status_code == 200
    # Control: once it has stopped.
    assert main(["--config", config, "restore", str(backup)]) == 0
    going = FolderUse(home / "data")
    assert going.alone()
    try:
        with (
            pytest.raises(RuntimeError, match="restore is going"),
            TestClient(create_app(load(home / "evalution.yaml"))),
        ):
            pass
    finally:
        going.release()
    with TestClient(create_app(load(home / "evalution.yaml"))) as back:
        assert back.get("/v1/health").status_code == 200


def test_migrate_says_what_it_couldnt_read(home, capsys) -> None:
    config = str(home / "evalution.yaml")
    assert main(["--config", config, "migrate"]) == 0  # the control: all of them read
    assert "3 runs rewritten" in said(capsys)[0]
    (home / "data" / "runs" / "broken.json").write_text("{ not json")
    assert main(["--config", config, "migrate"]) == 1
    out, err = said(capsys)
    assert "0 runs rewritten" in out and "broken" in err


def test_regrading_an_old_file_by_the_rules_keeps_it(home, capsys) -> None:
    config = str(home / "evalution.yaml")
    runs = home / "data" / "runs"
    doc = json.loads((RUNS / "v0-first.json").read_text())
    (runs / "v0-first.json").write_text(json.dumps({**doc, "run": "v0-first"}))
    old = (runs / "v0-first.json").read_bytes()
    assert main(["--config", config, "regrade", "v0-first", "--rules"]) == 0
    assert (runs / "v0-first.json.bak").read_bytes() == old
    assert (runs / "v0-first.json").read_bytes() != old
    # Control: today's shape has nothing to keep, and a later regrade doesn't replace the copy.
    assert main(["--config", config, "regrade", "current", "--rules"]) == 0
    assert not (runs / "current.json.bak").exists()
    assert main(["--config", config, "regrade", "v0-first", "--rules"]) == 0
    assert (runs / "v0-first.json.bak").read_bytes() == old
