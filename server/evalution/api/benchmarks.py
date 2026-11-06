"""Ready-made benchmarks: what can be run on a target, what its runs came to, and getting one."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter

from evalution.api.deps import busy, invalid, target_name
from evalution.benchmarks import longmemeval as benchmarks
from evalution.benchmarks import planning
from evalution.runs.runner import Runs
from evalution.traces.base import TraceSource


def _relink(found: dict[str, Any], source: TraceSource | None) -> None:
    """Each question's trace link as it should read now: older runs may name the wrong Phoenix."""
    if source is None:
        return
    for run in found["runs"]:
        for question in run["questions"]:
            if url := question.get("trace_url"):
                question["trace_url"] = source.trace_url(url.rsplit("/", 1)[-1])


def benchmarks_router(runs: Runs) -> APIRouter:
    router = APIRouter()
    targets = runs.targets

    @router.get("/benchmarks")
    async def get_benchmarks(target: str) -> dict[str, Any]:
        """Its sizes, and each run's numbers and questions, newest first."""
        name = target_name(targets, target)
        await runs.recover()
        played = await asyncio.to_thread(runs.all, name)
        found = benchmarks.overview(name, targets.config.sets_dir, played)
        _relink(found, targets.source(name))
        found["plan"] = await asyncio.to_thread(
            planning.current, targets, name, targets.adapter(name)
        )
        return found

    @router.post("/benchmarks/plan")
    async def make_plan(body: dict[str, Any]) -> dict[str, Any]:
        """Work out how the benchmark should play on this app, and try it for real."""
        name = target_name(targets, body.get("target"))
        caller = runs.caller("strong")
        if caller is None:
            raise invalid("Planning needs a model. Set one up in Settings.")
        if runs.busy() or not runs.lock.acquire():
            raise busy("A run is going. Plan again when it ends.")
        try:
            return await planning.plan_for(
                targets, name, targets.adapter(name), caller, targets.config.data_dir
            )
        except ValueError as exc:
            raise invalid(f"The plan didn't hold up: {exc}") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise invalid(f"Couldn't try the plan on this app: {exc}") from exc
        finally:
            runs.lock.release()

    @router.post("/benchmarks/plan/approve")
    async def approve_plan(body: dict[str, Any]) -> dict[str, Any]:
        """Write the planned test set, ready to run."""
        name = target_name(targets, body.get("target"))
        try:
            return await asyncio.to_thread(planning.approve, targets, name)
        except ValueError as exc:
            raise invalid(str(exc)) from exc

    @router.post("/benchmarks/install")
    async def install(body: dict[str, Any]) -> dict[str, Any]:
        """Write a size as a test set, downloading its data the first time."""
        name = target_name(targets, body.get("target"))
        size = str(body.get("size"))
        if size not in benchmarks.SIZES:
            raise invalid(f"No benchmark size {size!r}")
        config = targets.config
        try:
            slug, count = await asyncio.to_thread(
                benchmarks.install, size, name, config.sets_dir, config.data_dir
            )
        except httpx.HTTPError as exc:
            raise invalid("Couldn't download the benchmark's data. Try again.") from exc
        return {"set": slug, "questions": count}

    return router
