"""Where results go: one JSON file per run,
``<data dir>/runs/<run>.json``, which the routes read (:class:`FileStore`). A target may also send
finished runs to a :class:`RunSink`.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

from evalution.core.evidence import TryEvidence
from evalution.results import evidence as sidecar
from evalution.results.migrate import UNREADABLE, upcast

_logger = logging.getLogger(__name__)
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ResultStore(Protocol):
    """Takes a run (the ``runs/<run>.json`` document) each time it changes."""

    async def save(self, run: Mapping[str, Any]) -> None:
        """Store *run*."""


class RunSink(ResultStore, Protocol):
    """Somewhere else a target's finished runs go, kept open until the target changes. ``save``
    never raises: a sink that fails must not fail the run."""

    async def close(self) -> None:
        """Let go of what it holds open."""


class RunLock:
    """``<data dir>/run.lock``: one run or analysis at a time across processes, so ``serve`` and
    ``evalution run`` can't both play on one data folder. The system lets go of it when the process
    ends, however it ends.

    Within a process, each :meth:`acquire` that returned true takes one :meth:`release`, from any
    thread, so one holder ending can't take it from under another."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: TextIO | None = None
        self._holders = 0
        self._guard = threading.Lock()

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> bool:
        """Take it, unless another process has it."""
        with self._guard:
            if self._file is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                file = self.path.open("w")
                try:
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    file.close()
                    return False
                self._file = file
            self._holders += 1
            return True

    def release(self) -> None:
        with self._guard:
            self._holders = max(self._holders - 1, 0)
            if self._file is not None and not self._holders:
                self._file.close()
                self._file = None


class FolderUse:
    """A lock on the data folder itself, not a file in it, so no backup or restore has to step
    around it: ``serve`` holds it shared as long as it runs, ``restore`` alone, so neither starts
    under the other, even an idle ``serve``. The system lets go when the process ends."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._fd: int | None = None

    def _take(self, how: int) -> bool:
        """Whether nobody stands in the way. A folder that can't be locked can't tell: go on."""
        self.folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.folder, os.O_RDONLY)
        try:
            fcntl.flock(fd, how | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except OSError:
            os.close(fd)
            return True
        self._fd = fd
        return True

    def serving(self) -> bool:
        return self._take(fcntl.LOCK_SH)

    def alone(self) -> bool:
        return self._take(fcntl.LOCK_EX)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class FileStore:
    """Runs as JSON files; the one the routes read."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs = self.root / "runs"
        self.lock = RunLock(self.root / "run.lock")
        self._lock = asyncio.Lock()

    def _path(self, run: str) -> Path | None:
        return self.runs / f"{run}.json" if _RUN_ID.match(run) else None

    def write(self, run: Mapping[str, Any], text: str | None = None) -> None:
        path = self._path(str(run["run"]))
        if path is None:
            raise ValueError(f"not a run id: {run['run']}")
        self.runs.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.stem}.{secrets.token_hex(4)}.tmp")
        try:
            tmp.write_text(text or json.dumps(run, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    async def _in_turn(self, write: Callable[..., None], *args: Any) -> None:
        """*write* in a thread, one at a time and in the order asked. Stop cancels the caller, not
        the write: it finishes before the next one starts, or it would land on top of it."""

        async def turn() -> None:
            async with self._lock:
                await asyncio.to_thread(write, *args)

        await asyncio.shield(asyncio.ensure_future(turn()))

    async def save(self, run: Mapping[str, Any]) -> None:
        """Write *run* as it is now (read here, so tries played side by side may go on
        changing it) and one save at a time."""
        await self._in_turn(self.write, run, json.dumps(run, ensure_ascii=False))

    async def keep_evidence(self, run: str, evidence: TryEvidence) -> None:
        """Add one try to the run's sidecar, one writer at a time."""
        await self._in_turn(sidecar.append, self.runs, run, evidence)

    def load(self, run: str) -> dict[str, Any] | None:
        path = self._path(run)
        if path is None or not path.is_file():
            return None
        try:
            return upcast(dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, *UNREADABLE):
            _logger.warning("evals: run file %s unreadable", path, exc_info=True)
            return None

    def all(self, target: str | None = None, set_name: str | None = None) -> list[dict[str, Any]]:
        """Every run (of *target*, and of its set *set_name*), newest first."""
        if not self.runs.is_dir():
            return []
        found = [self.load(p.stem) for p in self.runs.glob("*.json")]
        runs = [
            r
            for r in found
            if r
            and (target is None or r.get("target") == target)
            and (set_name is None or r.get("set") == set_name)
        ]
        return sorted(runs, key=lambda r: float(r.get("started_at") or 0), reverse=True)
