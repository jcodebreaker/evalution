"""A target's isolation (ADR 0009): its plan and how far it is proven, finding and proving it
on evalution's own copy of the app, and undoing the last version."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from evalution.api.deps import Current, busy, invalid, local_only, target_name
from evalution.api.workspaces import Workspace
from evalution.isolation.plan import PlanStore
from evalution.project import isolating

#: What the page must send to start it: it spends two messages of the app's own copy.
CONFIRM = "confirm"


def _view(ws: Workspace, name: str) -> dict[str, Any]:
    return isolating.view(ws.targets, name, ws.isolations)


def _claim(ws: Workspace, name: str) -> None:
    """One at a time per target, and none while a run plays or a test message is on its way;
    no run starts until it ends.

    :raises HTTPException: 409, any of those is going.
    """
    runs, probes = ws.runs, ws.probes
    if ws.isolations.running(name):
        raise busy("Isolation is already being found for this app.")
    if runs.busy():
        raise busy("A run is going. Try again when it ends.")
    if name in probes.busy:
        raise busy("A test message is on its way. Try again when it's back.")
    if not runs.lock.acquire():
        raise busy("A run is going. Try again when it ends.")
    probes.busy.add(name)


def isolation_router() -> APIRouter:
    router = APIRouter()
    hosted_off = [Depends(local_only)]

    @router.get("/targets/{target}/isolation")
    async def get_isolation(ws: Current, target: str) -> dict[str, Any]:
        """The plan, its status and report, the files a commit changed under it, what the last
        probe found, the versions kept, and the job finding it when one is going."""
        name = target_name(ws.targets, target)
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/isolation/run", status_code=202, dependencies=hosted_off)
    async def run_isolation(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Find where the app keeps what it remembers and prove a plan to reset it, in the
        background, on evalution's own copy of the app. It sends two messages to that copy, so
        the page says so and sends ``confirm: true``."""
        name = target_name(ws.targets, target)
        if body.get(CONFIRM) is not True:
            raise invalid("This sends two messages to evalution's copy of the app: confirm it.")
        if ws.targets.code(name) is None:
            raise invalid("Say where the app's code is first: isolation is found from it.")
        _claim(ws, name)

        def let_go() -> None:
            ws.probes.busy.discard(name)
            ws.runs.lock.release()

        try:
            ws.isolations.start(ws.targets.config, name, let_go)
        except Exception:
            let_go()
            raise
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/isolation/undo", dependencies=hosted_off)
    async def undo_isolation(ws: Current, target: str) -> dict[str, Any]:
        """Go back to the plan's version before this one."""
        name = target_name(ws.targets, target)
        if ws.isolations.running(name):
            raise busy("Isolation is being found for this app. Try again when it ends.")
        try:
            await asyncio.to_thread(PlanStore(ws.targets.config.data_dir).undo, name)
        except LookupError as exc:
            raise busy("There is no plan to undo.") from exc
        return await asyncio.to_thread(_view, ws, name)

    return router
