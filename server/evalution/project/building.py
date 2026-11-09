"""A target's build recipe from the page (ADR 0011, phase 3): proposing it by rule, proving it as
a job the page follows (its steps, and the build's last lines as it prints them), undoing the last
version, removing what was built, and what the page shows of it.

Proving builds what's on disk into evalution's names and starts a copy from it
(:func:`evalution.isolation.build.prove`): one job per target, in the background.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from evalution.config import Config
from evalution.isolation import build
from evalution.isolation.recipe import (
    BuildRecipe,
    RecipeStore,
    image_names,
    named,
    propose,
    stale,
    words,
)
from evalution.targets import Targets

_logger = logging.getLogger(__name__)

BUILD, COPY, PROBE, STOP = "build", "copy", "probe", "stop"
LABELS = {
    BUILD: "Build {target}'s code into evalution's names",
    COPY: "Start a copy from it",
    PROBE: "Check the copy plays it and answers",
    STOP: "Take the copy down",
}
#: Lines of what the build printed that a job keeps, the newest last, and how much of each.
LINES_KEPT = 40
LINE_CHARS = 300
_BREAKS = re.compile(r"[\r\n]+")

Prove = Callable[..., Awaitable[BuildRecipe]]


def _store(targets: Targets) -> RecipeStore:
    return RecipeStore(targets.config.data_dir)


def _step(key: str, label: str) -> dict[str, Any]:
    return {"key": key, "label": label, "state": "waiting", "seconds": None, "detail": None}


@dataclass
class Job:
    """One proving, as the page follows it: its steps and the build's last lines."""

    target: str
    started: float = field(default_factory=time.time)
    #: ``running``, ``done`` (the recipe is saved, proven or not) or ``failed`` (no verdict).
    state: str = "running"
    steps: list[dict[str, Any]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    ended: float | None = None
    _began: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = [_step(k, v.format(target=self.target)) for k, v in LABELS.items()]

    def _end(self, step: dict[str, Any], state: str, now: float) -> None:
        began = self._began.pop(step["key"], None)
        if began is not None:
            step["seconds"] = round(now - began, 1)
        step["state"] = state

    def stage(self, key: str) -> None:
        """*key*'s step begins, and those before it are done."""
        keys = [s["key"] for s in self.steps]
        if key not in keys:
            return
        at, now = keys.index(key), time.monotonic()
        for step in self.steps[:at]:
            if step["state"] in ("waiting", "running"):
                self._end(step, "done", now)
        if self.steps[at]["state"] == "waiting":
            self.steps[at]["state"] = "running"
            self._began[key] = now

    def said(self, text: str) -> None:
        for line in _BREAKS.split(text):
            if line := line.strip():
                self.lines.append(line[:LINE_CHARS])
        del self.lines[:-LINES_KEPT]

    def finish(self, recipe: BuildRecipe) -> None:
        """The recipe is saved: every step done, or the one going ``failed`` when it isn't
        proven."""
        self.state, self.ended = "done", time.time()
        now = time.monotonic()
        failed = recipe.status != "proven"
        # The step that failed: the last one begun before the copy was taken down.
        begun = [s for s in self.steps if s["key"] != STOP and s["state"] != "waiting"]
        for step in self.steps:
            if step["state"] == "running" or (step["state"] == "waiting" and not failed):
                self._end(step, "done", now)
        if failed and begun:
            begun[-1]["state"] = "failed"
        if failed:
            self.error = recipe.report.rsplit("Not proven: ", 1)[-1][:2000]

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
            "lines": list(self.lines),
            "error": self.error,
        }


class Builds:
    """The page's proving of build recipes, one job per target, each in the background. *prove*
    is :func:`evalution.isolation.build.prove` unless a test gives another."""

    def __init__(self, prove: Prove | None = None) -> None:
        self.prove = prove
        self.jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def running(self, target: str) -> bool:
        return target in self._tasks

    def start(self, config: Config, target: str, done: Callable[[], None] = lambda: None) -> Job:
        """Prove *target*'s current recipe; *done* is called when it ends, however.

        :raises RuntimeError: One is already going for *target*.
        :raises LookupError: *target* has no recipe to prove.
        """
        if self.running(target):
            raise RuntimeError(f"already proving {target}'s build recipe")
        recipe = RecipeStore(config.data_dir).load(target)
        if recipe is None:
            raise LookupError(f"{target} has no build recipe: propose one first")
        job = Job(target)
        job.stage(BUILD)
        self.jobs[target] = job
        task = asyncio.create_task(self._go(config, job, recipe))
        self._tasks[target] = task

        def ended(_task: asyncio.Task[None]) -> None:
            self._tasks.pop(target, None)
            done()

        task.add_done_callback(ended)
        return job

    async def _go(self, config: Config, job: Job, recipe: BuildRecipe) -> None:
        prove = self.prove or build.prove
        try:
            proved = await prove(config, job.target, recipe, said=job.said, on_stage=job.stage)
            await asyncio.to_thread(RecipeStore(config.data_dir).save, proved)
        except asyncio.CancelledError:
            job.fail("Stopped before it finished.")
            raise
        except Exception as exc:
            _logger.warning("build: %s's recipe not proven", job.target, exc_info=True)
            job.fail(str(exc) or type(exc).__name__)
            return
        job.finish(proved)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# Proposing, undoing, removing


def propose_for(targets: Targets, name: str) -> tuple[BuildRecipe, bool]:
    """*name*'s recipe by rule, saved as a new version unless the current one already builds the
    same way and still holds: ``(recipe, saved)``.

    :raises LookupError: No code folder, or nothing says how a copy of the app starts.
    """
    code = targets.code(name)
    if code is None:
        raise LookupError("Say where the app's code is first: the recipe is found from it.")
    folder = code.root.resolve()
    spec = build.copy_spec(targets.config, name)
    found = propose(folder, name, spec)
    store = _store(targets)
    current = store.load(name)
    if current is not None and current.same_as(found) and not stale(current, folder):
        return current, False
    store.save(found)
    return found, True


def undo(targets: Targets, name: str) -> BuildRecipe | None:
    """Go back to the recipe's version before this one.

    :raises LookupError: There is none to undo.
    """
    return _store(targets).undo(name)


async def remove(targets: Targets, name: str, tag: str) -> build.Removed:
    """Remove the version built as *tag*: evalution's own images of it, then trim the VM's disk.

    :raises LookupError: Nothing was built as *tag*.
    """
    store = _store(targets)
    record = (await asyncio.to_thread(store.built, name)).get(tag)
    if record is None:
        raise LookupError(f"nothing built as {tag}")
    names = [str(n) for n in (record.get("images") or {}).values()]
    done = await build.remove_images(names)
    await asyncio.to_thread(store.forget_built, name, tag)
    return done


# What the page shows


def minutes(seconds: float) -> str:
    return f"about {max(1, round(seconds / 60))} min"


def gigabytes_up(size: float) -> str:
    return f"{max(1, math.ceil(size / 1000**3))} GB"


def cost(recipe: BuildRecipe | None, target: str) -> str:
    """What proving costs, said before it starts: "builds relay-lite's code once: about 4
    min and up to 20 GB", from the last build when there was one."""
    if recipe is not None and recipe.kind == "process":
        return f"starts a copy of {target} once; its setup isn't run in your folder"
    last = (recipe.last_build if recipe else None) or {}
    seconds = float(last.get("seconds") or build.FIRST_SECONDS)
    size = float(last.get("disk_bytes") or last.get("image_bytes") or build.FIRST_BYTES)
    return f"builds {target}'s code once: {minutes(seconds)} and up to {gigabytes_up(size)}"


def _recipe(recipe: BuildRecipe) -> dict[str, Any]:
    return {
        "kind": recipe.kind,
        "words": words(recipe),
        "files": list(recipe.files),
        "services": list(recipe.services),
        "profiles": list(recipe.profiles),
        "name_var": recipe.name_var,
        "tag_var": recipe.tag_var,
        "images": named(recipe, "<commit>"),
        "yours": dict(recipe.images),
        "setup": [{"run": list(s.run), "cwd": s.cwd} for s in recipe.setup],
        "command": list(recipe.command),
        "cwd": recipe.cwd,
        "evidence": [{"at": c.at, "says": c.says} for c in recipe.evidence],
        "report": recipe.report,
        "by": recipe.by,
        "why": recipe.why,
        "updated": recipe.updated,
    }


def view(targets: Targets, name: str, jobs: Builds | None = None) -> dict[str, Any]:
    """*name*'s build recipe as the page shows it: the recipe in words with its evidence, how far
    it is proven, the files changed under it, what was built and its cost, the versions kept, the
    free disk, and the job proving it when one is going."""
    store = _store(targets)
    problem: str | None = None
    try:
        recipe = store.load(name)
        history = store.history(name)
    except ValueError as exc:
        recipe, history = None, []
        problem = f"The saved recipe doesn't read: {exc}"
    code = targets.code(name)
    changed: list[str] = []
    if recipe is not None and code is not None:
        changed = stale(recipe, code.root.resolve())
    status = recipe.status if recipe else "none"
    if status == "proven" and changed:
        status = "stale"
    try:
        free = build.free_bytes(targets.config.data_dir)
    except OSError:
        free = 0
    built = sorted(store.built(name).values(), key=lambda r: -float(r.get("at") or 0))
    job = jobs.jobs.get(name) if jobs else None
    if recipe is not None and recipe.kind != "process":
        try:
            image_names(recipe, "probe")
        except ValueError as exc:
            problem = problem or f"The recipe can't name its images: {exc}"
    return {
        "target": name,
        "status": status,
        "version": history[-1][0] if history else None,
        "recipe": _recipe(recipe) if recipe else None,
        "last_build": recipe.last_build if recipe else None,
        "built": [
            {
                "tag": str(r.get("tag")),
                "images": list((r.get("images") or {}).values()),
                "seconds": r.get("seconds"),
                "disk_bytes": r.get("disk_bytes"),
                "image_bytes": r.get("image_bytes"),
                "at": r.get("at"),
            }
            for r in built
        ],
        "stale_files": changed,
        "history": [
            {"version": n, "status": r.status, "kind": r.kind, "by": r.by, "updated": r.updated}
            for n, r in history
        ],
        "can_undo": bool(history),
        "can_propose": code is not None,
        "can_prove": recipe is not None and code is not None and free >= build.MIN_FREE,
        "cost": cost(recipe, name),
        "free_bytes": free,
        "min_free_bytes": build.MIN_FREE,
        "job": job.view() if job else None,
        "problem": problem,
    }
