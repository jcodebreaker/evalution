"""A learn as a background job (docs/plans/next-level.md C10): its own record per target, not a
chat turn. The page, connect's first learn and the test writer start one; the page follows it.

* **One at a time** per target: asking while one runs answers the running one; an area asked
  for meanwhile is added to it (its ``asked``), and the learn takes it before its overview.
* **No clock.** It ends when its areas are done or their budgets are spent; an area that fails
  never stops the others. Nothing of the chat's limits applies.
* **It outlives the chat.** Closing or resetting the chat leaves it going; :meth:`Learns.stop`
  stops it, and what it learned is kept area by area.
* **A restart** finds a record still ``running`` with no job in this process: it was cut, and
  reads ``stopped`` with ``interrupted``; the page offers "Go on", a new job that learns the
  areas not learned yet.

The record is the target's ``learn_job.json`` (:data:`DOC`). The learn itself is run by
*run* (:mod:`evalution.writer`), given here so this layer imports nothing above it, as
:class:`evalution.project.isolating.Isolations` is given its *run*.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evalution.project.profile import read_doc, write_doc
from evalution.targets import Targets

_logger = logging.getLogger(__name__)

DOC = "learn_job.json"
STATES = ("running", "done", "stopped", "failed")
#: What the record says of a learn cut by a server that stopped.
INTERRUPTED = "The server stopped while it learned. What it learned is kept."


@dataclass
class Job:
    """One learn of *target*: what was asked (``areas``, ``everything``, ``force``), who asked
    (``by``: ``page``, ``connect``, ``writer``, ``chat``), its ``state`` and times, the
    ``stage`` it is at, its ``progress`` as the page draws it (the stage, the skeleton's
    entries, each area's agent: :mod:`evalution.writer.learn`), the ``reply`` it used to say in
    the chat, and its ``session``: the document its usage and failed steps are kept on."""

    target: str
    id: str = field(default_factory=lambda: f"l{secrets.token_hex(8)}")
    areas: list[str] = field(default_factory=list)
    everything: bool = False
    force: bool = False
    by: str = "page"
    state: str = "running"
    started: float = field(default_factory=time.time)
    ended: float | None = None
    reply: str | None = None
    error: str | None = None
    interrupted: bool = False
    #: The learn's own session (``usage``, ``failed_learns``, ``learn``: its progress).
    session: dict[str, Any] = field(default_factory=dict)

    @property
    def progress(self) -> dict[str, Any] | None:
        found = self.session.get("learn")
        return found if isinstance(found, dict) else None

    def view(self) -> dict[str, Any]:
        progress = self.progress
        usage = self.session.get("usage") or []
        return {
            "id": self.id,
            "target": self.target,
            "state": self.state,
            "by": self.by,
            "areas_asked": list(self.areas),
            "everything": self.everything,
            "force": self.force,
            "started": self.started,
            "ended": self.ended,
            "stage": (progress or {}).get("stage") if self.state == "running" else None,
            "progress": _copy(progress),
            "reply": self.reply,
            "error": self.error,
            "interrupted": self.interrupted,
            "session": self.session.get("id"),
            "usage": usage[-1] if usage else None,
        }

    @classmethod
    def of(cls, body: Mapping[str, Any]) -> Job:
        """A record kept before."""
        state = body.get("state")
        job = cls(
            target=str(body.get("target") or ""),
            id=str(body.get("id") or ""),
            areas=[str(a) for a in body.get("areas_asked") or []],
            everything=bool(body.get("everything")),
            force=bool(body.get("force")),
            by=str(body.get("by") or "page"),
            state=state if state in STATES else "stopped",
            started=float(body.get("started") or 0),
            ended=body.get("ended"),
            reply=body.get("reply"),
            error=body.get("error"),
            interrupted=bool(body.get("interrupted")),
        )
        if isinstance(body.get("progress"), dict):
            job.session["learn"] = dict(body["progress"])
        if body.get("session"):
            job.session["id"] = body["session"]
        if isinstance(body.get("usage"), dict):
            job.session["usage"] = [body["usage"]]
        return job


def _copy(value: Any) -> Any:
    """A copy the page can read while the learn moves its progress on."""
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


#: Runs a learn job to its end: sets its ``reply`` (and its ``error`` when it failed).
Run = Callable[[Job], Awaitable[None]]


def read(targets: Targets, name: str) -> Job | None:
    found = read_doc(targets, name, DOC)
    return Job.of(found) if isinstance(found, dict) and found.get("id") else None


def keep(targets: Targets, job: Job) -> None:
    write_doc(targets, job.target, DOC, job.view())


def interrupt(targets: Targets, name: str) -> Job | None:
    """*name*'s record, a learn cut by an earlier server marked as such: ``stopped``,
    ``interrupted``, its areas in flight waiting again."""
    job = read(targets, name)
    if job is None or job.state != "running":
        return job
    job.state, job.interrupted, job.ended = "stopped", True, job.ended or time.time()
    job.error = INTERRUPTED
    for area in (job.progress or {}).get("areas") or []:
        if isinstance(area, dict) and area.get("state") == "running":
            area["state"] = "waiting"
    keep(targets, job)
    return job


class Learns:
    """The learns of one workspace's targets, each in the background. *run* does the learn."""

    def __init__(self, targets: Targets, run: Run) -> None:
        self.targets = targets
        self.run = run
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False

    def running(self, target: str) -> bool:
        task = self._tasks.get(target)
        return task is not None and not task.done()

    def get(self, target: str) -> Job | None:
        """The job going, else the last one kept; one kept as running with nothing going here
        was cut with an earlier server (:func:`interrupt`)."""
        if target in self.jobs and (self.running(target) or self.jobs[target].state != "running"):
            return self.jobs[target]
        job = interrupt(self.targets, target)
        if job is not None:
            self.jobs[target] = job
        return job

    def interrupted(self) -> list[str]:
        """Mark every target's learn cut by an earlier server; the targets that had one."""
        cut = []
        for name in self.targets.config.targets:
            try:
                before = read(self.targets, name)
                if before is not None and before.state == "running" and not self.running(name):
                    interrupt(self.targets, name)
                    cut.append(name)
            except Exception:  # one target's record must not stop the server
                _logger.warning("learn: %s's job not read", name, exc_info=True)
        return cut

    def start(
        self,
        target: str,
        areas: Sequence[str] = (),
        *,
        everything: bool = False,
        force: bool = False,
        by: str = "page",
    ) -> Job:
        """Start a learn of *target*, or answer the one going: areas asked for meanwhile are
        added to it (learned before its overview)."""
        if self.running(target):
            job = self.jobs[target]
            progress = job.progress
            for area in areas:
                if area not in job.areas:
                    job.areas.append(area)
                if progress is not None:
                    progress.setdefault("asked", []).append({"id": area, "force": force})
            return job
        job = Job(target, areas=list(dict.fromkeys(areas)), everything=everything, force=force)
        job.by = by
        self.jobs[target] = job
        keep(self.targets, job)
        task = asyncio.get_running_loop().create_task(self._go(job))
        self._tasks[target] = task
        task.add_done_callback(lambda _t: self._tasks.pop(target, None))
        return job

    async def _go(self, job: Job) -> None:
        try:
            await self.run(job)
            if job.state == "running":
                job.state = "failed" if job.error else "done"
        except asyncio.CancelledError:
            job.state = "stopped"
            if self._closing:
                job.interrupted, job.error = True, INTERRUPTED
            job.reply = job.reply or "Stopped. What it learned is kept."
            raise
        except Exception as exc:
            _logger.warning("learn: %s failed", job.target, exc_info=True)
            job.state, job.error = "failed", str(exc) or type(exc).__name__
        finally:
            job.ended = time.time()
            with contextlib.suppress(Exception):
                await asyncio.to_thread(keep, self.targets, job)

    def stage(self, job: Job) -> None:
        """Keep *job* as it stands: what a restart finds."""
        keep(self.targets, job)

    async def wait(self, target: str, seconds: float | None = None) -> Job | None:
        """*target*'s job once it ended, or as it stands after *seconds*."""
        task = self._tasks.get(target)
        if task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), seconds)
        return self.get(target)

    async def stop(self, target: str) -> Job | None:
        """Stop *target*'s learn; what it learned is kept. The job as it ended, or the last one
        when none was going."""
        task = self._tasks.get(target)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        return self.get(target)

    async def close(self) -> None:
        """The server stops: a learn going is cut, and reads as interrupted."""
        self._closing = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
