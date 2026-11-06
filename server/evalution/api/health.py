"""How a target is doing, and what changed in its repo."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from evalution.api.deps import Current, busy, invalid, local_only, target_name
from evalution.project import drift, impact
from evalution.review import health


def health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/targets/{target}/health")
    async def get_health(ws: Current, target: str, period: str = "7d") -> dict[str, Any]:
        """How the app is doing over the period (``7d``, ``30d`` or ``all``), from stored runs,
        tests, the understanding and drift; no model call. It starts a check for code changes in
        the background, at most every few minutes; ``checking`` says one is going."""
        targets, runs, watcher = ws.targets, ws.runs, ws.watcher
        name = target_name(targets, target)
        if period not in health.PERIODS:
            raise invalid("period: 7d, 30d or all")
        await runs.recover()
        doc = await asyncio.to_thread(health.for_target, targets, runs, name, period)
        last_run = doc["recent"][0]["run"] if doc["recent"] else None
        checking = watcher.kick(name, last_run) if watcher else False
        return {**doc, "checking": checking}

    @router.get("/targets/{target}/drift")
    async def get_drift(ws: Current, target: str) -> dict[str, Any]:
        targets = ws.targets
        return drift.read(targets, target_name(targets, target))

    @router.post("/targets/{target}/changed", status_code=202, dependencies=[Depends(local_only)])
    async def target_changed(ws: Current, target: str) -> dict[str, Any]:
        """A hook or CI says the target's repo moved: answers at once, and the code check, the
        change card and what it knows follow in the background, a burst of these as one."""
        targets, watcher = ws.targets, ws.watcher
        assert watcher is not None  # local only
        watcher.changed(target_name(targets, target))
        return {"checking": True}

    @router.post(
        "/targets/{target}/change/read", status_code=204, dependencies=[Depends(local_only)]
    )
    async def read_change(ws: Current, target: str) -> None:
        """The owner asks what the change on the card means: one light call, once."""
        targets, runs, llm = ws.targets, ws.runs, ws.llm
        name = target_name(targets, target)
        if not llm:
            raise busy("No model is set up yet.")
        await impact.refresh(runs, name, asked=True)

    return router
