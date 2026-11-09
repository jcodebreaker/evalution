"""Store drivers round-trip: keep, change, put back, same digest. The Postgres and volume drivers
run against throwaway Docker resources this file creates (named ``eval-test-...``) and removes,
and are skipped when Docker doesn't answer. Every guard refuses before any command runs."""

import json
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from evalution.isolation import rehearse
from evalution.isolation.drivers import (
    FileStore,
    PostgresStore,
    Refused,
    VolumeStore,
)
from evalution.isolation.drivers import files as files_driver
from evalution.isolation.drivers.base import DockerError

# Files ---------------------------------------------------------------------------------------


def _sqlite(path: Path, rows: list[str]) -> None:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS t (body TEXT)")
    db.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    db.commit()
    db.close()


def test_the_prototype_import_still_works():
    assert rehearse.FileStore is FileStore


def test_a_sqlite_file_with_sidecars_round_trips(tmp_path):
    path = tmp_path / "data" / "chat.db"
    path.parent.mkdir()
    _sqlite(path, ["first"])
    sidecar = path.with_name("chat.db-wal")
    sidecar.write_bytes(b"pending frames")
    store = FileStore(path)
    before = store.digest()
    store.keep(tmp_path / "kept")

    _sqlite(path, ["canary-123"])
    sidecar.unlink(missing_ok=True)  # SQLite removes it on close
    path.with_name("chat.db-journal").write_bytes(b"canary-123")
    assert store.digest() != before
    assert store.holding("canary-123")

    store.put_back(tmp_path / "kept")
    assert store.digest() == before
    assert sidecar.read_bytes() == b"pending frames"
    assert not path.with_name("chat.db-journal").exists()
    assert store.holding("canary-123") == []


def test_a_folder_round_trips(tmp_path):
    folder = tmp_path / "cache"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    (folder / ".hidden").write_text("h")
    store = FileStore(folder)
    before = store.digest()
    store.keep(tmp_path / "kept")

    (folder / "a.txt").write_text("changed")
    (folder / "sub" / "new.txt").write_text("canary-xyz")
    (folder / ".hidden").unlink()
    assert store.covers(str(folder / "sub" / "new.txt"))
    assert store.holding("canary-xyz") == [str((folder / "sub" / "new.txt").resolve())]

    store.put_back(tmp_path / "kept")
    assert store.digest() == before
    assert not (folder / "sub" / "new.txt").exists()
    assert (folder / ".hidden").read_text() == "h"


def test_a_store_that_was_absent_is_removed_on_put_back(tmp_path):
    store = FileStore(tmp_path / "later.db")
    store.keep(tmp_path / "kept")
    (tmp_path / "later.db").write_text("made by the app")
    store.put_back(tmp_path / "kept")
    assert not (tmp_path / "later.db").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="clones are macOS's cp -c")
def test_copies_are_clones_on_macos(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(args, *a, **kw):
        calls.append(list(args))
        return real(args, *a, **kw)

    monkeypatch.setattr(files_driver.subprocess, "run", spy)
    (tmp_path / "f.txt").write_text("x")
    FileStore(tmp_path / "f.txt").keep(tmp_path / "kept")
    assert calls and calls[0][1:3] == ["-c", "-R"]
    assert (tmp_path / "kept" / "f.txt").read_text() == "x"


def test_a_failed_clone_falls_back_to_a_plain_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(files_driver, "_cp", lambda: "/usr/bin/false")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("x")
    FileStore(tmp_path / "d").keep(tmp_path / "kept")
    assert (tmp_path / "kept" / "d" / "f.txt").read_text() == "x"


def test_put_back_refuses_outside_the_allowed_roots(tmp_path):
    (tmp_path / "theirs").mkdir()
    (tmp_path / "ours").mkdir()
    precious = tmp_path / "theirs" / "app.db"
    precious.write_text("someone's data")
    store = FileStore(precious, allowed_roots=[tmp_path / "ours"])
    store.keep(tmp_path / "kept")
    (tmp_path / "kept" / "app.db").write_text("would overwrite")
    with pytest.raises(Refused):
        store.put_back(tmp_path / "kept")
    assert precious.read_text() == "someone's data"
    inside = FileStore(tmp_path / "ours" / "app.db", allowed_roots=[tmp_path / "ours"])
    inside.keep(tmp_path / "kept2")
    inside.put_back(tmp_path / "kept2")


# Guards that refuse before any command -------------------------------------------------------


@pytest.fixture
def no_commands(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError(f"a command ran: {a}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.mark.parametrize(
    ("container", "db"),
    [
        ("relay-evals-postgres-1", "app"),
        ("evalution-postgres", "app"),
        ("eval-copy-postgres-1", "postgres"),
        ("eval-copy-postgres-1", "template0"),
        ("eval-copy-postgres-1", "template1"),
        ("eval-copy-postgres-1", "eval_golden_0123456789ab"),
        ("eval-copy-postgres-1", 'app"; DROP DATABASE x; --'),
    ],
)
def test_postgres_refuses_what_isnt_evalutions(no_commands, container, db):
    with pytest.raises(Refused):
        PostgresStore(container, db, "postgres")


def test_postgres_put_back_refuses_a_golden_copy_not_its_own(no_commands, tmp_path):
    store = PostgresStore("eval-copy-postgres-1", "app", "postgres")
    (tmp_path / "postgres.json").write_text(
        json.dumps(
            {"container": "eval-copy-postgres-1", "db": "app", "golden": "postgres", "owner": "x"}
        )
    )
    with pytest.raises(Refused):
        store.put_back(tmp_path)
    with pytest.raises(Refused):
        store.drop_golden(tmp_path)


@pytest.mark.parametrize("volume", ["relay_artifact-data", "evaluation", "data", "-eval_x"])
def test_volume_refuses_what_isnt_evalutions(no_commands, volume):
    with pytest.raises(Refused):
        VolumeStore(volume)


# Docker --------------------------------------------------------------------------------------


def _docker_answers() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        done = subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=False)
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0


docker_only = pytest.mark.skipif(not _docker_answers(), reason="needs a Docker daemon")


def _docker(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=120, **kw
    )


@pytest.fixture(scope="module")
def pg_container():
    name = f"eval-test-pg-{secrets.token_hex(4)}"
    try:
        _docker(
            "run", "-d", "--name", name, "--label", "evalution.test=drivers",
            "-e", "POSTGRES_PASSWORD=throwaway", "postgres:16-alpine",
        )  # fmt: skip
        for _ in range(120):
            logs = _docker("logs", name, check=False)
            ready = (logs.stdout + logs.stderr).count("ready to accept connections") >= 2
            if (
                ready
                and _docker("exec", name, "pg_isready", "-U", "postgres", check=False).returncode
                == 0
            ):
                break
            time.sleep(0.5)
        else:
            pytest.fail("the throwaway postgres never became ready")
        yield name
    finally:
        _docker("rm", "-f", "-v", name, check=False)


def _sql(container: str, db: str, sql: str) -> str:
    return _docker(
        "exec", container, "psql", "-U", "postgres", "-d", db, "-X", "-q", "-A", "-t",
        "-v", "ON_ERROR_STOP=1", "-c", sql,
    ).stdout.strip()  # fmt: skip


@pytest.fixture
def pg(pg_container, tmp_path):
    db = f"app_{secrets.token_hex(3)}"
    _sql(pg_container, "postgres", f"CREATE DATABASE {db}")
    _sql(pg_container, db, "CREATE SCHEMA mem")
    _sql(pg_container, db, "CREATE TABLE items (id serial PRIMARY KEY, body text)")
    _sql(pg_container, db, "CREATE TABLE mem.notes (id int PRIMARY KEY, note jsonb)")
    _sql(pg_container, db, "CREATE TABLE spans (id serial PRIMARY KEY, body text)")
    _sql(pg_container, db, "INSERT INTO items (body) VALUES ('starting row'), ('another')")
    _sql(pg_container, db, """INSERT INTO mem.notes VALUES (1, '{"k": "v"}')""")
    _sql(pg_container, db, "INSERT INTO spans (body) VALUES ('old trace')")
    store = PostgresStore(pg_container, db, "postgres", exclude=["spans"])
    yield store, db
    _sql(pg_container, "postgres", f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")


@docker_only
def test_postgres_round_trip_keeps_traces(pg, tmp_path):
    store, db = pg
    c = store.container
    before = store.digest()
    kept = tmp_path / "kept"
    t0 = time.perf_counter()
    store.keep(kept)
    keep_s = time.perf_counter() - t0
    golden = json.loads((kept / "postgres.json").read_text())["golden"]
    assert golden.startswith("eval_golden_")

    canary = f"eval-canary-{secrets.token_hex(4)}"
    # One dump line far longer than a stream reader's default limit.
    _sql(c, db, f"INSERT INTO items (body) VALUES (repeat('x', 300000) || '{canary}')")
    _sql(c, db, f"""UPDATE mem.notes SET note = '{{"k": "{canary}"}}'""")
    _sql(c, db, f"INSERT INTO spans (body) VALUES ('trace of {canary}')")
    _sql(c, db, "CREATE TABLE made_later (x int)")
    assert store.digest() != before
    # Traces aren't counted, and aren't where a leak is looked for.
    assert sorted(store.holding(canary)) == [f"{db}.mem.notes", f"{db}.public.items"]

    t0 = time.perf_counter()
    store.put_back(kept)
    put_back_s = time.perf_counter() - t0
    print(f"\npostgres keep {keep_s:.2f}s, put_back {put_back_s:.2f}s")
    assert store.digest() == before
    assert store.holding(canary) == []
    assert _sql(c, db, "SELECT count(*) FROM spans") == "2"
    assert canary in _sql(c, db, "SELECT string_agg(body, ',') FROM spans")
    assert _sql(c, db, "SELECT to_regclass('made_later')") == ""
    # A second reset from the same golden copy, the traces still carried.
    _sql(c, db, "DELETE FROM items")
    store.put_back(kept)
    assert store.digest() == before
    assert _sql(c, db, "SELECT count(*) FROM spans") == "2"

    store.drop_golden(kept)
    assert (
        _sql(c, "postgres", f"SELECT count(*) FROM pg_database WHERE datname = '{golden}'") == "0"
    )


@docker_only
def test_postgres_loads_sample_data_and_stops_at_the_first_error(pg, tmp_path):
    store, db = pg
    c = store.container
    seed = tmp_path / "seed.sql"
    seed.write_text(
        "CREATE TABLE people (id int PRIMARY KEY, name text);\n"
        "INSERT INTO people VALUES (1, 'Alex Rivera'), (2, 'Sam');\n"
        "INSERT INTO items (body) VALUES ('from the sample data');\n"
    )
    before = store.digest()
    store.load_sql(seed)
    assert _sql(c, db, "SELECT string_agg(name, ',' ORDER BY id) FROM people") == "Alex Rivera,Sam"
    assert _sql(c, db, "SELECT count(*) FROM items") == "3"
    assert store.digest() != before

    # Kept after loading, it is the starting state put back.
    kept = tmp_path / "kept"
    seeded = store.digest()
    store.keep(kept)
    _sql(c, db, "DELETE FROM people")
    store.put_back(kept)
    assert store.digest() == seeded
    store.drop_golden(kept)

    broken = tmp_path / "broken.sql"
    broken.write_text(
        "INSERT INTO people VALUES (3, 'Before');\nINSERT INTO nowhere VALUES (1);\n"
        "INSERT INTO people VALUES (4, 'After');\n"
    )
    with pytest.raises(DockerError, match="nowhere"):
        store.load_sql(broken)
    assert _sql(c, db, "SELECT count(*) FROM people WHERE id = 4") == "0"
    with pytest.raises(Refused, match=r"isn't a \.sql file"):
        store.load_sql(tmp_path / "seed.txt")


@docker_only
def test_postgres_keep_closes_the_apps_connections(pg, tmp_path):
    store, db = pg
    app = subprocess.Popen(
        ["docker", "exec", "-i", store.container, "psql", "-U", "postgres", "-d", db],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        assert app.stdin is not None
        app.stdin.write(b"SELECT 1;\n")
        app.stdin.flush()
        time.sleep(1)
        store.keep(tmp_path / "kept")
        store.put_back(tmp_path / "kept")
        store.drop_golden(tmp_path / "kept")
    finally:
        app.kill()
        app.wait()


@pytest.fixture
def volume():
    name = f"eval-test-vol-{secrets.token_hex(4)}"
    try:
        _docker("volume", "create", "--label", "evalution.test=drivers", name)
        seed = "mkdir -p /v/index && echo hello > /v/index/a.txt && echo dot > /v/.config"
        _docker("run", "--rm", "-v", f"{name}:/v", "alpine:3.20", "sh", "-c", seed)
        yield name
    finally:
        _docker("volume", "rm", "-f", name, check=False)


@docker_only
def test_volume_round_trip(volume, tmp_path):
    store = VolumeStore(volume)
    before = store.digest()
    assert store.digest() == before
    t0 = time.perf_counter()
    store.keep(tmp_path / "kept")
    keep_s = time.perf_counter() - t0

    canary = f"eval-canary-{secrets.token_hex(4)}"
    change = (
        f"echo {canary} > /v/index/memory.bin && echo changed > /v/index/a.txt"
        f" && rm /v/.config && echo {canary} > /v/.secret"
    )
    _docker("run", "--rm", "-v", f"{volume}:/v", "alpine:3.20", "sh", "-c", change)
    assert store.digest() != before
    assert sorted(store.holding(canary)) == [
        f"volume:{volume}/.secret",
        f"volume:{volume}/index/memory.bin",
    ]

    t0 = time.perf_counter()
    store.put_back(tmp_path / "kept")
    put_back_s = time.perf_counter() - t0
    print(f"\nvolume keep {keep_s:.2f}s, put_back {put_back_s:.2f}s")
    assert store.digest() == before
    assert store.holding(canary) == []
    listing = _docker("run", "--rm", "-v", f"{volume}:/v", "alpine:3.20", "ls", "-A", "/v").stdout
    assert sorted(listing.split()) == [".config", "index"]


@docker_only
def test_volume_that_doesnt_exist_isnt_made(tmp_path):
    name = f"eval-test-vol-{secrets.token_hex(4)}"
    store = VolumeStore(name)
    try:
        with pytest.raises(RuntimeError):
            store.digest()
        assert _docker("volume", "inspect", name, check=False).returncode != 0
    finally:
        _docker("volume", "rm", "-f", name, check=False)
