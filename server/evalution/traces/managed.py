"""evalution's own Phoenix, and where a target's traces are read (docs/architecture.md, "Traces").

``evalution serve`` runs Phoenix (the optional ``arize-phoenix`` package, group ``traces``) as a
child process: data under ``<data_dir>/phoenix``, the UI and OTLP over HTTP on 127.0.0.1 at the
configured port, or the next free one. Phoenix always opens a gRPC listener; unless
``traces.grpc`` names a port it gets a random local one nobody is told about. :class:`Supervisor`
waits for ``/healthz``, checks it every few seconds, starts Phoenix again when it dies or stops
answering, and stops it on shutdown. ``serving.json`` in its folder says where it runs, for
``evalution run`` in another process.

Each target's spans go to project ``evalution-<target>`` (:func:`project`); an app that can't
name its project lands in ``default``, and evalution reads both, by session and trace id. The
built-in source (:mod:`evalution.traces.builtin`) is read the same way, with no process to run.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import IO, Protocol

import httpx

from evalution.config import PhoenixConfig
from evalution.core import net
from evalution.traces.base import SpanReader, TraceSource
from evalution.traces.builtin import DEFAULT_TARGET, TARGET_PREFIX, Builtin, SpanStore
from evalution.traces.phoenix import Phoenix

_logger = logging.getLogger(__name__)

PREFIX = TARGET_PREFIX
STATE_FILE = "serving.json"
INSTALL = "pip install 'evalution[traces]' (or uv sync --group traces)"
#: How long Phoenix may take to answer after it starts (first start migrates its database).
START_SECONDS = 90.0
CHECK_SECONDS = 5.0
#: Failed health checks in a row before a running Phoenix is started again.
MISSES = 3
PORT_TRIES = 20


def project(target: str) -> str:
    """The Phoenix project a target's spans go to in evalution's own Phoenix."""
    return f"{PREFIX}{target}"


def installed() -> bool:
    try:
        importlib.metadata.version("arize-phoenix")
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def free_port(start: int, tries: int = PORT_TRIES) -> int | None:
    """The first port from *start* that nothing listens on at 127.0.0.1."""
    for port in range(start, min(start + tries, 65536)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


def reader(
    target: str,
    own: PhoenixConfig | None,
    managed_url: str | None,
    api_key: str | None = None,
    store: SpanStore | None = None,
) -> TraceSource | None:
    """What a target's traces are read from. With evalution's own Phoenix or the built-in *store*:
    its spans for the target, then its ``default`` ones, then the app's own Phoenix when the
    target has one. Without: the app's own Phoenix, or nothing."""
    if store is not None:
        also: list[SpanReader] = [Builtin(store, DEFAULT_TARGET)]
        if own:
            also.append(Phoenix(own, api_key=api_key))
        return Builtin(store, target, also=also)
    if not managed_url:
        return Phoenix(own, api_key=api_key) if own else None
    others: list[SpanReader] = [Phoenix(PhoenixConfig(managed_url, DEFAULT_TARGET))]
    # An app with its own Phoenix sends its traces there, so that's where a link to one must go.
    shown = None
    if own and own.url.rstrip("/") != managed_url.rstrip("/"):
        others.append(Phoenix(own, api_key=api_key))
        shown = own.public_url or own.url
    return Phoenix(PhoenixConfig(managed_url, project(target), public_url=shown), also=others)


def landing(name: str, managed_url: str, store: SpanStore | None) -> TraceSource:
    """Where an app's spans land, on their own: *name* is a :func:`project` or ``default``."""
    if store is not None:
        return Builtin(store, name.removeprefix(PREFIX))
    return Phoenix(PhoenixConfig(managed_url, name))


def running_url(data_dir: Path) -> str | None:
    """Where the Phoenix of a ``serve`` running now is, from its ``serving.json``."""
    try:
        state = json.loads((data_dir / "phoenix" / STATE_FILE).read_text(encoding="utf-8"))
        os.kill(int(state["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return str(state["url"])


class Process(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


Spawn = Callable[[list[str], dict[str, str], IO[bytes]], Process]
Healthy = Callable[[str], Awaitable[bool]]


def _popen(command: list[str], env: dict[str, str], log: IO[bytes]) -> Process:
    return subprocess.Popen(  # noqa: S603 - our own interpreter and arguments
        command, env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
    )


async def _healthz(url: str) -> bool:
    try:
        async with net.client(timeout=3.0) as client:
            return (await client.get(f"{url}/healthz")).is_success
    except httpx.HTTPError:
        return False


class Supervisor:
    """Phoenix as a child process of ``serve``. *spawn* and *healthy* are for tests."""

    def __init__(
        self,
        folder: Path,
        port: int = 6007,
        grpc: int | None = None,
        *,
        spawn: Spawn = _popen,
        healthy: Healthy = _healthz,
        every: float = CHECK_SECONDS,
        start_seconds: float = START_SECONDS,
    ) -> None:
        self.folder = folder
        self.wanted = port
        self.grpc = grpc
        self.port: int | None = None
        self.process: Process | None = None
        self.restarts = 0
        self._spawn, self._healthy = spawn, healthy
        self._every, self._start_seconds = every, start_seconds
        self._task: asyncio.Task[None] | None = None
        self._log: IO[bytes] | None = None

    @property
    def url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    def command(self) -> list[str]:
        return [sys.executable, "-m", "phoenix.server.main", "serve"]

    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "PHOENIX_WORKING_DIR": str(self.folder),
            "PHOENIX_HOST": "127.0.0.1",
            "PHOENIX_PORT": str(self.port),
            "PHOENIX_GRPC_PORT": str(self.grpc or 0),
            "PHOENIX_TELEMETRY_ENABLED": "false",
        }

    async def start(self) -> list[str]:
        """Start Phoenix and wait for it; the lines to print (the port, a problem).

        :raises RuntimeError: No free port near the configured one.
        """
        self.port = free_port(self.wanted)
        if self.port is None:
            raise RuntimeError(f"no free port from {self.wanted} for Phoenix")
        notes = []
        if self.port != self.wanted:
            notes.append(f"port {self.wanted} is taken, so Phoenix runs on {self.port}")
        self.folder.mkdir(parents=True, exist_ok=True)
        self._launch()
        if await self._wait():
            notes.append(f"Phoenix (traces) is on {self.url}")
        else:
            notes.append(f"Phoenix hasn't answered yet; its log is {self.folder / 'phoenix.log'}")
        (self.folder / STATE_FILE).write_text(
            json.dumps({"url": self.url, "pid": os.getpid()}), encoding="utf-8"
        )
        self._task = asyncio.create_task(self._watch())
        return notes

    def _launch(self) -> None:
        if self._log is None:
            self._log = (self.folder / "phoenix.log").open("ab")
        self.process = self._spawn(self.command(), self.env(), self._log)

    async def _wait(self) -> bool:
        """Whether Phoenix answered before :data:`START_SECONDS` (or it exited first)."""
        until = time.monotonic() + self._start_seconds
        while time.monotonic() < until:
            if self.process is None or self.process.poll() is not None:
                return False
            if await self._healthy(str(self.url)):
                return True
            await asyncio.sleep(min(1.0, self._every))
        return False

    async def _watch(self) -> None:
        misses = 0
        while True:
            await asyncio.sleep(self._every)
            dead = self.process is None or self.process.poll() is not None
            misses = 0 if not dead and await self._healthy(str(self.url)) else misses + 1
            if dead or misses >= MISSES:
                _logger.warning("phoenix: %s; starting it again", "exited" if dead else "silent")
                await asyncio.to_thread(self._end)
                self.restarts += 1
                self._launch()
                await self._wait()
                misses = 0

    def _end(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await asyncio.to_thread(self._end)
        with contextlib.suppress(OSError):
            (self.folder / STATE_FILE).unlink()
        if self._log is not None:
            self._log.close()
            self._log = None
