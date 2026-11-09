"""Finding where an app keeps state: the scan on small fake projects, and the probe watched end
to end on a real process that keeps state in places the code doesn't name."""

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from evalution.isolation import discover, observe

TOY = Path(__file__).parent / "fixtures" / "toy_store_app"


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _kinds(stores):
    return {(s.kind, s.locator) for s in stores}


def test_scan_reads_packages_env_compose_and_literals(tmp_path):
    root = _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\ndependencies = [\n  "psycopg[binary]>=3",\n  "chromadb",\n]\n'
            ),
            "package.json": '{"dependencies": {"ioredis": "^5"}}',
            ".env.example": "DATABASE_URL=postgres://me:pw@localhost:5432/app\nAPI_KEY=x\n",
            "app/db.py": 'import os\nurl = os.getenv("DATABASE_URL")\n',
            "app/mem.py": 'import chromadb\nc = chromadb.PersistentClient(path="./vectors")\n',
            "app/cache.ts": "const r = new Redis(process.env.REDIS_URL);\n",
            "app/local.py": 'import sqlite3\nsqlite3.connect("data/chat.db")\n',
            "app/graph.py": "from langgraph.store.memory import InMemoryStore\n",
            "compose.yaml": (
                "services:\n  db:\n    image: postgres:16\n    volumes:\n"
                "      - pgdata:/var/lib/postgresql/data\n"
                "      - ./init.sql:/docker-entrypoint-initdb.d/x.sql\n"
                "  app:\n    image: myapp\n    volumes:\n      - ./uploads:/app/uploads\n"
                "volumes:\n  pgdata:\n"
            ),
        },
    )
    found = _kinds(discover.scan(root))
    assert {
        ("postgres", "package:psycopg"),
        ("vector", "package:chromadb"),
        ("redis", "package:ioredis"),
        ("postgres", "env:DATABASE_URL"),
        ("redis", "env:REDIS_URL"),
        ("vector", "./vectors"),
        ("sqlite", "data/chat.db"),
        ("memory", "InMemoryStore"),
        ("postgres", "service:db"),
        ("volume", "volume:pgdata"),
        ("dir", "./uploads"),
    } <= found
    # Not stores: a key, a mounted init script, and the password in the example URL.
    assert not any("API_KEY" in loc or "init.sql" in loc or "pw" in loc for _, loc in found)


def test_scan_never_reads_dot_env(tmp_path):
    root = _write(tmp_path, {".env": "DATABASE_URL=postgres://u:secret@db/x\n", "a.py": "x = 1\n"})
    assert discover.scan(root) == []


def test_evidence_points_at_file_and_line(tmp_path):
    root = _write(tmp_path, {"a.py": "\n\nimport os\nos.environ['MONGO_URI']\n"})
    (store,) = discover.scan(root)
    assert store.kind == "mongo"
    assert store.evidence == ["a.py:4"]


def test_lsof_output_parsed_to_files_and_db_sockets():
    text = "\n".join(
        [
            "p123",
            "f3",
            "tREG",
            "n/Users/me/app/data/chat.db",
            "f4",
            "tREG",
            "n/usr/lib/libz.dylib",
            "f5",
            "tREG",
            "n/Users/me/app/.venv/lib/python3.12/site-packages/x.so",
            "f6",
            "tIPv4",
            "n127.0.0.1:50000->127.0.0.1:5432",
            "f7",
            "tIPv4",
            "n127.0.0.1:50001->10.0.0.2:443",
        ]
    )
    files, sockets = observe.parse_lsof(text)
    assert files == ["/Users/me/app/data/chat.db"]
    assert sockets == {"127.0.0.1:5432": "postgres"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def toy(tmp_path):
    """The toy app running from a copy, its outside store, its port and its process."""
    app = tmp_path / "app"
    shutil.copytree(TOY, app)
    (app / "data").mkdir()
    elsewhere = tmp_path / "elsewhere" / ".toyagent"
    elsewhere.mkdir(parents=True)
    hidden = elsewhere / "memory.db"
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(app / "app.py"), str(port), str(hidden)])
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield app, elsewhere, hidden, port, proc
    proc.terminate()
    proc.wait(5)


@pytest.mark.skipif(shutil.which("lsof") is None, reason="needs lsof")
async def test_probe_finds_every_store_the_toy_app_writes(toy):
    app, elsewhere, hidden, port, proc = toy
    stores = discover.scan(app)
    assert ("sqlite", "data/chat.db") in _kinds(stores)
    assert ("postgres", "env:DATABASE_URL") in _kinds(stores)
    assert not any(str(hidden) in s.locator for s in stores), "the scan can't know this one"

    canary = "eval-canary-7f3a91"

    async def act():
        async with httpx.AsyncClient() as client:
            r = await client.post(f"http://127.0.0.1:{port}/", json={"message": canary})
            r.raise_for_status()

    assert observe.listening(port) == proc.pid
    obs = await observe.watch(app, stores, act, canary, pid=proc.pid)
    result = {s.locator: s for s in observe.reconcile(app, stores, obs)}

    # Named in code, and the probe confirms it holds the conversation.
    chat = result["data/chat.db"]
    assert any("holds the canary" in note for note in chat.seen)
    # Written to, never named: found only by watching.
    notes = next(s for loc, s in result.items() if loc.endswith("cache/notes.txt"))
    assert notes.evidence == ["not named anywhere in the code"]
    assert any("holds the canary" in note for note in notes.seen)
    # Outside the project and never named: found through the process's open files.
    outside = [s for loc, s in result.items() if loc.startswith(str(elsewhere.resolve()))]
    assert outside, sorted(result)
    assert any("holds the canary" in n for s in outside for n in s.seen)
    # Named, but nothing reached it during the probe.
    assert result["env:DATABASE_URL"].seen == []


def test_container_finds_mark_the_stores_that_name_them():
    from evalution.isolation import containers

    stores = [
        discover.Store("postgres", "env:DATABASE_URL"),
        discover.Store("postgres", "postgresql://prod-test-cluster/app"),
        discover.Store("volume", "volume:artifact-data"),
    ]
    boxes = [containers.Container("relay-evals-engine-1", "engine", {})]
    found = [
        containers.Found("relay-evals-postgres-1", "postgres", "app.conversation_items"),
        containers.Found(
            "relay-evals-engine-1", "volume", "volume:relay-evals_artifact-data/memory/x"
        ),
    ]
    out = {s.locator: s for s in containers.reconcile(stores, boxes, found)}
    assert len(out["env:DATABASE_URL"].seen) == 1
    assert out["postgresql://prod-test-cluster/app"].seen == []  # an example in the code
    assert len(out["volume:artifact-data"].seen) == 1
    assert len(out) == 5


def test_scan_skips_tests_and_their_throwaway_stores(tmp_path):
    root = _write(
        tmp_path,
        {
            "app/db.py": 'import sqlite3\nsqlite3.connect("data/app.db")\n',
            "tests/test_db.py": 'sqlite3.connect("test.db")\n',
            "engine/tests/db/helpers.py": 'URL = "postgresql://localhost:5432/testdb"\n',
            "app/db_test.py": 'open("fixture.sqlite")\n',
            "node_modules/pg/index.js": "process.env.DATABASE_URL\n",
        },
    )
    assert _kinds(discover.scan(root)) == {("sqlite", "data/app.db")}
