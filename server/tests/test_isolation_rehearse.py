"""A reset plan rehearsed on the toy app: a plan covering every store is proven, one that misses
a store fails and names it, and either way the app is left as it was found."""

import asyncio
import shutil
import socket
import sys
from pathlib import Path

import httpx
import pytest

from evalution.isolation.rehearse import FileStore, rehearse

TOY = Path(__file__).parent / "fixtures" / "toy_store_app"
pytestmark = pytest.mark.skipif(shutil.which("lsof") is None, reason="needs lsof")


class Toy:
    """The toy app, started and stopped the way a plan's launch would."""

    def __init__(self, root: Path) -> None:
        self.app = root / "app"
        shutil.copytree(TOY, self.app)
        (self.app / "data").mkdir()
        self.hidden = root / "elsewhere" / ".toyagent" / "memory.db"
        self.hidden.parent.mkdir(parents=True)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self) -> int:
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, str(self.app / "app.py"), str(self.port), str(self.hidden)
        )
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    await client.get(f"http://127.0.0.1:{self.port}/", timeout=0.2)
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.05)
        return self.proc.pid

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()
        self.proc = None

    async def probe(self, canary: str) -> None:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"http://127.0.0.1:{self.port}/", json={"message": canary})
            r.raise_for_status()

    def stores(self, *, with_hidden: bool) -> list[FileStore]:
        out = [FileStore(self.app / "data" / "chat.db"), FileStore(self.app / "cache")]
        return [*out, FileStore(self.hidden)] if with_hidden else out


def _contents(files: list[Path]) -> dict[str, bytes]:
    return {str(f): f.read_bytes() for f in files}


async def _seeded(toy: Toy) -> dict[str, bytes]:
    """Some earlier use, then what each store holds, to check it's left exactly so."""
    await toy.start()
    await toy.probe("an earlier person's message")
    await toy.stop()
    return _contents([toy.app / "data" / "chat.db", toy.app / "cache" / "notes.txt", toy.hidden])


async def test_a_plan_covering_every_store_is_proven(tmp_path):
    toy = Toy(tmp_path)
    earlier = await _seeded(toy)
    try:
        result = await rehearse(
            toy.stores(with_hidden=True), toy.app, toy.start, toy.stop, toy.probe, tmp_path / "w"
        )
    finally:
        await toy.stop()
    assert result.ok, result.report()
    assert [s.name for s in result.steps] == [
        "starting state kept",
        "the probe reached a planned store",
        "the app wrote only to planned stores",
        "put back to the starting state",
        "the canary is gone after the reset",
        "left as it was found",
    ]
    assert _contents([Path(f) for f in earlier]) == earlier


async def test_a_plan_missing_a_store_fails_and_names_it(tmp_path):
    toy = Toy(tmp_path)
    earlier = await _seeded(toy)
    try:
        result = await rehearse(
            toy.stores(with_hidden=False), toy.app, toy.start, toy.stop, toy.probe, tmp_path / "w"
        )
    finally:
        await toy.stop()
    assert not result.ok
    assert result.steps[-2].name == "the app wrote only to planned stores"
    assert any(leak.startswith(str(toy.hidden.resolve())) for leak in result.leaks), result.leaks
    # The planned stores are still left as they were found. The one the plan missed isn't
    # evalution's to put back: the report names it instead.
    chat = str(toy.app / "data" / "chat.db")
    assert _contents([Path(chat)]) == {chat: earlier[chat]}
    assert "Not proven" in result.report()
