"""Keeping a target's isolation plan current, and finding one from the page (ADR 0009, phase 6).

A plan cites the project files its evidence comes from, with their hashes
(:attr:`evalution.isolation.plan.Plan.evidence_hashes`). :func:`mark_stale` is the claims' rule
(ADR 0005) for it: when a commit changed one of those files, a proven plan is saved again as a new
version with status ``stale``, its report saying which files. Free, no model, and only commits
move it: a file edited and not committed yet marks nothing. It runs in the same background check
as what evalution knows (:class:`evalution.project.impact.Watcher`), after a hook or Home asks.

:class:`Isolations` runs :func:`evalution.isolation.isolate.isolate` for the page, one at a time
per target, as a job whose stages the page shows as they go.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalution.config import Config
from evalution.isolation.plan import Plan, PlanStore, stale
from evalution.targets import Targets

_logger = logging.getLogger(__name__)

#: What a stale plan's report says, after the rehearsal's account (a later rehearsal drops it).
STALE_NOTE = "\n\nOut of date: a commit changed {files}."
#: The stages of finding and proving isolation, in order, as the page says them.
STAGES = (
    ("start", "Starting evalution's own copy of your app"),
    ("find", "Finding where it keeps what it remembers"),
    ("plan", "Writing a plan to reset each one"),
    ("rehearse", "Rehearsing the plan on the copy"),
    ("done", "Proven"),
)
#: How often the job looks for the probe's evidence, which says the plan is drafted.
WATCH_SECONDS = 0.5


def _plans(targets: Targets) -> PlanStore:
    return PlanStore(targets.config.data_dir)


def _git(root: Path, *args: str) -> str | None:
    """A git command's output in *root*; ``None`` when it fails or this is not a checkout. A
    repo's own settings never get to run anything from here."""
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],  # noqa: S607
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _uncommitted(root: Path, files: list[str]) -> set[str] | None:
    """Which of *files* (relative to *root*) differ from the last commit in the working tree, or
    aren't committed at all; ``None`` when *root* is not a git checkout with a commit."""
    if not files:
        return set()
    changed = _git(root, "diff", "--relative", "--no-renames", "--name-only", "-z", "HEAD", "--",
                   *files)  # fmt: skip
    new = _git(root, "ls-files", "-z", "--others", "--", *files)
    if changed is None or new is None:
        return None
    return {n for n in (changed + new).split("\0") if n}


def changed_files(plan: Plan, root: Path) -> list[str]:
    """The files *plan*'s evidence cites that a commit changed since it was drafted. In a folder
    that is not a git checkout there are no commits, and every changed file counts."""
    changed = stale(plan, root)
    pending = _uncommitted(root, changed)
    return changed if pending is None else [f for f in changed if f not in pending]


def mark_stale(targets: Targets, name: str) -> list[str]:
    """Save *name*'s proven plan again as ``stale`` when a commit changed a file its evidence
    cites; the files, or nothing when it stays as it is. Free: no model, no app."""
    code = targets.code(name)
    if code is None:
        return []
    store = _plans(targets)
    try:
        plan = store.load(name)
    except ValueError:
        return []
    if plan is None or plan.status != "proven":
        return []
    files = changed_files(plan, code.root.resolve())
    if not files:
        return []
    plan.status = "stale"
    plan.report = plan.report + STALE_NOTE.format(files=", ".join(files))
    store.save(plan)
    _logger.info("isolation: %s's plan is out of date (%s)", name, ", ".join(files))
    return files


async def mark_stale_quietly(targets: Targets, name: str) -> None:
    try:
        await asyncio.to_thread(mark_stale, targets, name)
    except Exception:
        _logger.warning("isolation: %s's plan not checked", name, exc_info=True)


# Finding and proving it from the page


Stage = Callable[[str], None]
#: Finds, plans, rehearses and saves; calls *stage* as each begins. Swapped in tests.
Isolate = Callable[[Config, str, Stage], Awaitable[Plan]]


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


async def _evidence_saved(path: Path, before: float | None, stage: Stage) -> None:
    """Say ``rehearse`` once the probe's evidence is saved, which is when the plan is drafted."""
    while True:
        await asyncio.sleep(WATCH_SECONDS)
        now = _mtime(path)
        if now is not None and now != before:
            stage("plan")
            stage("rehearse")
            return


async def isolate(config: Config, target: str, stage: Stage) -> Plan:
    """:func:`evalution.isolation.isolate.isolate` on evalution's own copy of the app, telling
    *stage* as it goes. Sends two messages to the copy, never to the person's app."""
    from evalution.isolation.isolate import isolate as run

    path = PlanStore(config.data_dir).root / target / "evidence.json"
    before = _mtime(path)

    async def started(_summary: str) -> bool:
        stage("find")
        return True

    watch = asyncio.create_task(_evidence_saved(path, before, stage))
    try:
        return await run(config, target, ask=started)
    finally:
        watch.cancel()


@dataclass
class Job:
    """One target's finding and proving, as the page follows it."""

    target: str
    started: float = field(default_factory=time.time)
    #: ``running``, ``done`` (the plan is saved, proven or not) or ``failed`` (it didn't finish).
    state: str = "running"
    steps: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    ended: float | None = None

    def stage(self, key: str) -> None:
        """*key*'s step begins: those before it are done."""
        keys = [k for k, _ in STAGES]
        if key not in keys:
            return
        at = keys.index(key)
        self.steps = [
            {"key": k, "text": text, "state": "done" if i < at else "doing"}
            for i, (k, text) in enumerate(STAGES[: at + 1])
        ]

    def finish(self, plan: Plan) -> None:
        self.state, self.ended = "done", time.time()
        for step in self.steps:
            step["state"] = "done"
        last = {"key": "done", "text": STAGES[-1][1], "state": "done"}
        if plan.status != "proven":
            last = {"key": "done", "text": "Not proven", "state": "problem"}
        self.steps = [s for s in self.steps if s["key"] != "done"] + [last]

    def fail(self, error: str) -> None:
        self.state, self.ended, self.error = "failed", time.time(), error
        for step in self.steps:
            if step["state"] == "doing":
                step["state"] = "problem"

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started": self.started,
            "ended": self.ended,
            "steps": [dict(s) for s in self.steps],
            "error": self.error,
        }


class Isolations:
    """The page's finding and proving, one job per target, each in the background. *run* is
    :func:`isolate` unless a test gives another."""

    def __init__(self, run: Isolate | None = None) -> None:
        self.run = run or isolate
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def running(self, target: str) -> bool:
        return target in self._tasks

    def start(self, config: Config, target: str, done: Callable[[], None] = lambda: None) -> Job:
        """Start finding and proving *target*'s isolation; *done* is called when it ends, however.

        :raises RuntimeError: One is already going for *target*.
        """
        if self.running(target):
            raise RuntimeError(f"already finding {target}'s isolation")
        job = Job(target)
        job.stage("start")
        self.jobs[target] = job
        task = asyncio.create_task(self._go(config, job))
        self._tasks[target] = task

        def ended(_task: asyncio.Task[None]) -> None:
            self._tasks.pop(target, None)
            done()

        task.add_done_callback(ended)
        return job

    async def _go(self, config: Config, job: Job) -> None:
        try:
            plan = await self.run(config, job.target, job.stage)
        except asyncio.CancelledError:
            job.fail("Stopped before it finished.")
            raise
        except Exception as exc:
            _logger.warning("isolation: %s not found", job.target, exc_info=True)
            job.fail(str(exc) or type(exc).__name__)
            return
        job.finish(plan)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# What the page shows


def _evidence(found: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the last probe found, counted: names and paths only."""
    if not found:
        return None
    candidates = found.get("candidates") or []
    observation = found.get("observation") or {}
    return {
        "candidates": len(candidates),
        "touched": sum(1 for c in candidates if c.get("seen")),
        "found": [f"{f.get('container')}:{f.get('where')}" for f in found.get("found") or []],
        "watched": list(observation.get("roots") or []),
    }


def view(targets: Targets, name: str, jobs: Isolations | None = None) -> dict[str, Any]:
    """*name*'s isolation as the page shows it: the plan and how far it is proven, what a commit
    changed under it, what the last probe found, the versions kept, and a job going."""
    store = _plans(targets)
    try:
        plan = store.load(name)
        history = store.history(name)
    except ValueError as exc:
        plan, history = None, []
        problem: str | None = f"The saved plan doesn't read: {exc}"
    else:
        problem = None
    code = targets.code(name)
    stale_files: list[str] = []
    if plan is not None and code is not None:
        stale_files = changed_files(plan, code.root.resolve())
    job = jobs.jobs.get(name) if jobs else None
    numbers = [n for n, _ in _versions(store, name)]
    return {
        "target": name,
        "status": plan.status if plan else "none",
        "version": numbers[-1] if numbers else None,
        "plan": _plan(plan) if plan else None,
        "stale_files": (stale_files or noted(plan)) if plan and plan.status == "stale" else [],
        "evidence": _evidence(store.load_evidence(name)),
        "history": [
            {
                "version": n,
                "status": p.status,
                "updated": p.updated,
                "stores": len(p.stores),
            }
            for n, p in zip(numbers, history, strict=False)
        ],
        "can_undo": bool(history),
        "can_run": code is not None,
        "job": job.view() if job else None,
        "problem": problem,
    }


def _versions(store: PlanStore, name: str) -> list[tuple[int, Path]]:
    folder = store.root / name / "versions"
    if not folder.is_dir():
        return []
    return sorted((int(p.stem), p) for p in folder.glob("*.json") if p.stem.isdigit())


def _plan(plan: Plan) -> dict[str, Any]:
    return {
        "stores": [
            {
                "id": s.id,
                "kind": s.kind,
                "locator": s.locator,
                "strategy": s.strategy,
                "moved_by": list(s.moved_by),
                "keyed_by": s.keyed_by,
                "seen": bool(s.seen),
            }
            for s in plan.stores
        ],
        "exclude": list(plan.exclude),
        "report": plan.report,
        "leaks": list(plan.leaks),
        "updated": plan.updated,
    }


def brief(targets: Targets, name: str) -> dict[str, Any] | None:
    """What Home says of it: only a plan that a commit left out of date, with the files."""
    try:
        plan = _plans(targets).load(name)
    except ValueError:
        return None
    if plan is None or plan.status != "stale":
        return None
    code = targets.code(name)
    files = changed_files(plan, code.root.resolve()) if code else []
    return {"status": plan.status, "stale_files": files or noted(plan)}


def noted(plan: Plan) -> list[str]:
    """The files a stale plan's report says a commit changed."""
    head = STALE_NOTE.strip().split("{", 1)[0]
    for line in reversed(plan.report.splitlines()):
        if line.startswith(head):
            return [f.strip() for f in line[len(head) :].rstrip(".").split(",") if f.strip()]
    return []
