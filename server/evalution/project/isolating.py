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
from evalution.isolation.plan import Plan, PlannedStore, PlanStore, given, own_name, stale
from evalution.isolation.setup import (
    PERSON_FIELDS,
    Setup,
    SetupStore,
    changed,
    person_name,
    setup_for,
)
from evalution.targets import Targets

_logger = logging.getLogger(__name__)

#: What a stale plan's report says, after the rehearsal's account (a later rehearsal drops it).
STALE_NOTE = "\n\nOut of date: a commit changed {files}."
#: The steps of setting up testing, as the page says them. Once the plan is drafted, a step for
#: each store the copy gets goes after ``find``: ``store:<id>``.
COPY, FIND, PERSON, PROVE = "copy", "find", "person", "prove"
LABELS = {
    COPY: "Copy {target}",
    FIND: "Find where it keeps things",
    PERSON: "Make a test person",
    PROVE: "Check it resets cleanly",
}
#: What the ``person`` step says when the setup names no test person.
APPS_OWN = "the app's own"
#: Stage names :func:`evalution.isolation.isolate.isolate` said before these steps, and the step
#: each is now. ``plan`` (drafting) is part of ``find``.
ALIASES = {"start": COPY, "rehearse": PROVE, "plan": FIND}
#: Each kind of store a copy gets, as its step names it; any other is memory or files.
_KINDS = {"postgres": "Postgres", "mysql": "MySQL", "redis": "Redis", "mongo": "MongoDB"}
_MEMORY = ("memory", "vector")


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


#: Told each stage as it begins, and the plan with ``plan`` once it is drafted.
Stage = Callable[..., None]
#: Finds, plans, rehearses and saves; calls *stage* as each begins. Swapped in tests.
Isolate = Callable[[Config, str, Stage], Awaitable[Plan]]


async def isolate(config: Config, target: str, stage: Stage) -> Plan:
    """:func:`evalution.isolation.isolate.isolate` on evalution's own copy of the app, telling
    *stage* each step as it begins, and the plan once drafted. Sends two messages to the copy,
    never to the person's app."""
    from evalution.isolation.isolate import isolate as run

    async def started(_summary: str) -> bool:
        return True

    return await run(
        config,
        target,
        ask=started,
        on_stage=stage,
        on_plan=lambda plan: stage("plan", plan),
    )


def store_label(store: PlannedStore) -> str:
    """What the step giving the copy *store* says, by its kind."""
    if store.kind in _KINDS:
        return f"Give it its own {_KINDS[store.kind]}"
    said = " ".join([store.locator, *store.seen, *store.found_as]).lower()
    if store.kind in _MEMORY or "memor" in said:
        return "Give it its own memory"
    return "Give it its own files"


def _step(key: str, label: str, detail: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "state": "waiting", "seconds": None, "detail": detail}


@dataclass
class Job:
    """One target's setting up of testing, as the page follows it: a step for each thing done
    to evalution's copy of the app, each ``waiting``, ``running``, ``done`` or ``failed``, with
    the seconds it took once it ended."""

    target: str
    #: The test person's name, for the ``person`` step; ``None``: the app's own.
    person: str | None = None
    started: float = field(default_factory=time.time)
    #: ``running``, ``done`` (the plan is saved, proven or not) or ``failed`` (it didn't finish).
    state: str = "running"
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    ended: float | None = None
    _began: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = [
                _step(COPY, LABELS[COPY].format(target=self.target)),
                _step(FIND, LABELS[FIND]),
                _step(PERSON, LABELS[PERSON], self.person or APPS_OWN),
                _step(PROVE, LABELS[PROVE]),
            ]

    def _end(self, step: dict[str, Any], state: str, now: float) -> None:
        began = self._began.pop(step["key"], None)
        if began is not None:
            step["seconds"] = round(now - began, 1)
        step["state"] = state

    def drafted(self, plan: Plan) -> None:
        """The plan is drafted: a step for each store the copy gets, after ``find``."""
        kept = [s for s in self.steps if not s["key"].startswith("store:")]
        at = next((i + 1 for i, s in enumerate(kept) if s["key"] == FIND), 1)
        stores = [_step(f"store:{s.id}", store_label(s), own_name(s)) for s in given(plan)]
        self.steps = kept[:at] + stores + kept[at:]

    def stage(self, key: str, plan: Plan | None = None) -> None:
        """*key*'s step begins, and those before it are done. With *plan* (drafted), its store
        steps are added first. A key no step has is passed over."""
        if plan is not None:
            self.drafted(plan)
        key = ALIASES.get(key, key)
        keys = [s["key"] for s in self.steps]
        if key not in keys:
            return
        at = keys.index(key)
        now = time.monotonic()
        for step in self.steps[:at]:
            if step["state"] in ("waiting", "running"):
                self._end(step, "done", now)
        step = self.steps[at]
        if step["state"] == "waiting":
            step["state"] = "running"
            self._began[key] = now

    def finish(self, plan: Plan) -> None:
        """The plan is saved: every step done, the last ``failed`` when it isn't proven."""
        self.state, self.ended = "done", time.time()
        now = time.monotonic()
        for step in self.steps:
            if step["state"] in ("waiting", "running"):
                self._end(step, "done", now)
        if plan.status != "proven":
            self.steps[-1]["state"] = "failed"

    def fail(self, error: str) -> None:
        self.state, self.ended, self.error = "failed", time.time(), error
        now = time.monotonic()
        for step in self.steps:
            if step["state"] == "running":
                self._end(step, "failed", now)

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
        job = Job(target, person=person_name(setup_for(config, target)))
        job.stage(COPY)
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


# The test setup


def person_fields(targets: Targets, name: str) -> list[str]:
    """The test person's fields *name*'s adapter takes (``capabilities.person``), in the order
    the page shows them; none when it takes no test person, or can't be made."""
    try:
        declared = getattr(targets.adapter(name).capabilities, "person", None)
    except Exception:  # an adapter that can't be made takes no person here
        _logger.debug("isolation: %s's adapter not made for its setup", name, exc_info=True)
        return []
    if declared is True:
        return list(PERSON_FIELDS)
    if not declared or isinstance(declared, str):
        return []
    try:
        said = {str(f) for f in declared}
    except TypeError:
        return []
    return [f for f in PERSON_FIELDS if f in said]


def _plan_or_none(targets: Targets, name: str) -> Plan | None:
    try:
        return _plans(targets).load(name)
    except ValueError:
        return None


def seeds(plan: Plan | None) -> bool:
    """Whether a copy for *plan* can start with sample data: it gets a Postgres store."""
    return plan is not None and any(s.kind == "postgres" for s in given(plan))


def needs_check(targets: Targets, name: str, plan: Plan | None) -> bool:
    """Whether *plan* is rehearsed again before the next run: marked stale (by a commit, or a
    change to what the copy starts with), or its evidence changed since it was proven."""
    if plan is None or plan.status not in ("proven", "stale"):
        return False
    if plan.status == "stale":
        return True
    code = targets.code(name)
    return code is not None and bool(stale(plan, code.root.resolve()))


def setup_view(targets: Targets, name: str, person: list[str] | None = None) -> dict[str, Any]:
    """*name*'s test setup as the page shows it: the setup, what the target supports (the test
    person's fields its adapter takes, and sample data when the copy gets a Postgres store), and
    whether the plan is checked again before the next run."""
    plan = _plan_or_none(targets, name)
    return {
        **setup_for(targets.config, name).view(),
        "supports": {
            "person": person_fields(targets, name) if person is None else person,
            "seed": seeds(plan),
        },
        "needs_check": needs_check(targets, name, plan),
    }


def change_setup(targets: Targets, name: str, body: Any) -> Setup:
    """Save *body*'s changes to *name*'s setup; a change to what the copy starts with marks a
    proven plan stale.

    :raises SetupError: A change it doesn't take, in words for the page.
    """
    before = setup_for(targets.config, name)
    code = targets.code(name)
    folder = code.root if code is not None else None
    after = changed(before, body, folder=folder, seed=seeds(_plan_or_none(targets, name)))
    return SetupStore(targets.config.data_dir).save(name, after, before=before)
