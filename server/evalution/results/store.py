"""Where results go: each run a document of kind :data:`RUN` under its id, which the routes read,
with its evidence (:mod:`evalution.results.evidence`), its files (:mod:`evalution.storage.files`)
and any request to stop it (:class:`RunStore`). A target may also send finished runs to a
:class:`RunSink`. The data folder keeps the locks that let one process at a time play
(:class:`RunLock`) and keep a restore apart from ``serve`` (:class:`FolderUse`).
"""

from __future__ import annotations

import asyncio
import copy
import fcntl
import logging
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

from evalution.core.evidence import TryEvidence
from evalution.results import evidence as evidence_log
from evalution.results.migrate import UNREADABLE, upcast
from evalution.storage import Docs
from evalution.storage.files import Files

_logger = logging.getLogger(__name__)
RUN = "run"
#: A request, from another process, to stop the run under the same key.
STOP = "run-stop"
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_run_id(value: str) -> bool:
    return bool(_RUN_ID.match(value))


class ResultStore(Protocol):
    """Takes a run (the :data:`RUN` document) each time it changes."""

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

    FILE = "run.lock"

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / self.FILE
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


class RunStore:
    """A workspace's runs, their evidence and the requests to stop them; the one the routes
    read."""

    def __init__(
        self,
        docs: Docs,
        settle: Callable[[dict[str, Any]], Any] | None = None,
        files: Files | None = None,
    ) -> None:
        self.docs = docs
        #: Its runs' files (:mod:`evalution.storage.files`); ``None`` where there is no data folder
        #: of its own to keep them in (hosted).
        self.files = files
        #: What a run is read again with just before each save, under its lock: the workspace's
        #: labelled examples (:mod:`evalution.runs.trust`), which change while a run is held.
        self.settle = settle
        #: One lock per run: every read-modify-write of a stored run takes it, so none lands on
        #: top of another (one process per workspace).
        self._turns: dict[str, asyncio.Lock] = {}

    def write(self, run: Mapping[str, Any]) -> None:
        """:raises ValueError: Not a run id."""
        if not is_run_id(str(run["run"])):
            raise ValueError(f"not a run id: {run['run']}")
        self.docs.put(RUN, str(run["run"]), run)

    async def _in_turn[T](self, run: str, work: Callable[..., T], *args: Any) -> T:
        """*work* in a thread, holding *run*'s lock: one at a time and in the order asked. Stop
        cancels the caller, not the work: it finishes before the next one starts, or it would land
        on top of it."""
        lock = self._turns.setdefault(run, asyncio.Lock())

        async def turn() -> T:
            async with lock:
                return await asyncio.to_thread(work, *args)

        return await asyncio.shield(asyncio.ensure_future(turn()))

    def _settled(self, run: dict[str, Any]) -> None:
        if self.settle is not None:
            self.settle(run)
        self.write(run)

    async def save(self, run: Mapping[str, Any]) -> None:
        """Write *run* as it is now (copied here, so tries played side by side may go on
        changing it), settled, and one save of it at a time."""
        doc = copy.deepcopy(dict(run))
        await self._in_turn(str(doc["run"]), self._settled, doc)

    async def update(self, run: str, change: Callable[[dict[str, Any]], bool]) -> bool:
        """Read *run*, *change* it and write it back if *change* says it changed, under its lock;
        whether it did."""

        def read_change_write() -> bool:
            doc = self.load(run)
            if doc is None or not change(doc):
                return False
            self.write(doc)
            return True

        return await self._in_turn(run, read_change_write)

    def replace(self, run: Mapping[str, Any], evidence: Iterable[TryEvidence]) -> None:
        """*run* and its evidence as given, whatever was kept under its id before: an uploaded
        run (:mod:`evalution.results.upload`) sent again replaces itself.

        :raises ValueError: Not a run id.
        """
        if not is_run_id(str(run["run"])):
            raise ValueError(f"not a run id: {run['run']}")
        self.docs.clear_entries(evidence_log.EVIDENCE, str(run["run"]))
        for kept in evidence:
            evidence_log.keep(self.docs, str(run["run"]), kept)
        self.write(run)

    async def keep_evidence(self, run: str, evidence: TryEvidence) -> None:
        """Add one try to the run's evidence, in turn with its saves."""
        await self._in_turn(run, evidence_log.keep, self.docs, run, evidence)

    def evidence(self, run: str) -> list[TryEvidence]:
        """Every try kept for *run*, in the order played."""
        return list(evidence_log.read(self.docs, run))

    def delete_run(self, run: str) -> bool:
        """Forget *run*: its document, its evidence, any request to stop it and its files.
        Whether there was such a run.

        :raises ValueError: Not a run id.
        """
        if not is_run_id(run):
            raise ValueError(f"not a run id: {run}")
        if self.files is not None:
            self.files.delete_run(run)
        self.docs.clear_entries(evidence_log.EVIDENCE, run)
        self.docs.delete(STOP, run)
        return self.docs.delete(RUN, run)

    def ask_stop(self, run: str) -> None:
        """Ask whichever process is playing *run* to stop it."""
        self.docs.put(STOP, run, True)

    def stop_asked(self, run: str) -> bool:
        return self.docs.get(STOP, run) is not None

    def clear_stop(self, run: str) -> None:
        self.docs.delete(STOP, run)

    def load(self, run: str) -> dict[str, Any] | None:
        """*run* as today's schema; ``None`` for no such run, or one that doesn't read."""
        if not is_run_id(run):
            return None
        found = self.docs.get(RUN, run)
        return None if found is None else _read(run, found)

    def ids(self) -> list[str]:
        return self.docs.keys(RUN)

    def all(self, target: str | None = None, set_name: str | None = None) -> list[dict[str, Any]]:
        """Every run (of *target*, and of its set *set_name*), newest first."""
        found = (_read(key, body) for key, body, _ in self.docs.items(RUN))
        runs = [
            r
            for r in found
            if r
            and (target is None or r.get("target") == target)
            and (set_name is None or r.get("set") == set_name)
        ]
        return sorted(runs, key=lambda r: float(r.get("started_at") or 0), reverse=True)

    def upcast_all(self) -> tuple[list[str], list[str]]:
        """Rewrite every run of an older schema as today's: the ones changed, and the ones that
        don't read as a run, left as they are for the others' sake."""
        changed, unreadable = [], []
        for key, body, _ in self.docs.items(RUN):
            try:
                doc = dict(body)
                new = upcast(doc)
            except UNREADABLE as exc:
                _logger.warning("evals: run %s unreadable: %s", key, type(exc).__name__)
                unreadable.append(key)
                continue
            if new is not doc:
                self.docs.put(RUN, key, new)
                changed.append(key)
        return changed, unreadable


def _read(key: str, body: Any) -> dict[str, Any] | None:
    try:
        return upcast(dict(body))
    except UNREADABLE:
        _logger.warning("evals: run %s unreadable", key, exc_info=True)
        return None
