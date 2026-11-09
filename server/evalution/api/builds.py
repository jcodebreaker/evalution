"""A target's build recipe (ADR 0011, phase 3): how a version of its code is built into
evalution's own names, proposing it by rule, proving it on what's on disk (a job the page
follows), undoing the last version, and removing what was built. Local only: hosted has no
copies, so nothing to build for."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from evalution.api.deps import Current, busy, invalid, local_only, target_name
from evalution.api.workspaces import Workspace
from evalution.isolation.launch import LaunchError
from evalution.project import building

#: What the page must send to prove a recipe: it builds the app's code once, minutes and GBs.
CONFIRM = "confirm"


def _view(ws: Workspace, name: str) -> dict[str, Any]:
    return building.view(ws.targets, name, ws.builds)


def _not_proving(ws: Workspace, name: str) -> None:
    """:raises HTTPException: 409, the recipe is being proven."""
    if ws.builds.running(name):
        raise busy("The build recipe is being proven. Try again when it ends.")


def _claim(ws: Workspace, name: str) -> None:
    """One at a time per target, none while a run plays, a test message is on its way or
    isolation is being found (each would start a copy too); no run starts until it ends.

    :raises HTTPException: 409, any of those is going.
    """
    runs, probes = ws.runs, ws.probes
    _not_proving(ws, name)
    if ws.isolations.running(name):
        raise busy("Testing is being set up for this app. Try again when it ends.")
    if runs.busy():
        raise busy("A run is going. Try again when it ends.")
    if name in probes.busy:
        raise busy("A test message is on its way. Try again when it's back.")
    if not runs.lock.acquire():
        raise busy("A run is going. Try again when it ends.")
    probes.busy.add(name)


def builds_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(local_only)])

    @router.get("/targets/{target}/builds")
    async def get_build(ws: Current, target: str) -> dict[str, Any]:
        """The recipe in words with its evidence, how far it is proven, what was built and what
        proving costs, the free disk, the versions kept, and the job proving it."""
        name = target_name(ws.targets, target)
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/builds/propose")
    async def propose_build(ws: Current, target: str) -> dict[str, Any]:
        """Propose the recipe by rule from the app's files: saved as a new version (undoable)
        unless the current one already builds the same way. Builds nothing."""
        name = target_name(ws.targets, target)
        _not_proving(ws, name)
        try:
            await asyncio.to_thread(building.propose_for, ws.targets, name)
        except LookupError as exc:
            raise invalid(str(exc).strip("'\"")) from exc
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/builds/prove", status_code=202)
    async def prove_build(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Prove the recipe in the background: build what's on disk into evalution's names,
        start a copy from it, check it answers, take it down. It builds the app's code once
        (minutes, and GBs of disk), so the page says so and sends ``confirm: true``."""
        name = target_name(ws.targets, target)
        if body.get(CONFIRM) is not True:
            raise invalid("This builds the app's code once, taking minutes and disk: confirm it.")
        found = await asyncio.to_thread(_view, ws, name)
        if found["recipe"] is None:
            raise invalid("Propose a build recipe first.")
        if not found["can_prove"]:
            raise invalid(
                "Under 5 GB is free on this computer's disk: remove built versions or free "
                "space elsewhere first."
                if found["free_bytes"] < found["min_free_bytes"]
                else "Say where the app's code is first."
            )
        _claim(ws, name)

        def let_go() -> None:
            ws.probes.busy.discard(name)
            ws.runs.lock.release()

        try:
            ws.builds.start(ws.targets.config, name, let_go)
        except Exception:
            let_go()
            raise
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/builds/undo")
    async def undo_build(ws: Current, target: str) -> dict[str, Any]:
        """Go back to the recipe's version before this one."""
        name = target_name(ws.targets, target)
        _not_proving(ws, name)
        try:
            await asyncio.to_thread(building.undo, ws.targets, name)
        except LookupError as exc:
            raise busy("There is no build recipe to undo.") from exc
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/builds/remove")
    async def remove_build(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Remove one built version (``tag``): evalution's own images of it, never the person's,
        then trim the Docker VM's disk where it can. Says what was removed and trimmed."""
        name = target_name(ws.targets, target)
        _not_proving(ws, name)
        tag = body.get("tag")
        if not isinstance(tag, str) or not tag:
            raise invalid("tag: the built version to remove")
        try:
            done = await building.remove(ws.targets, name, tag)
        except LookupError as exc:
            raise invalid(f"Nothing was built as {tag}.") from exc
        except (LaunchError, ValueError) as exc:
            raise invalid(str(exc)) from exc
        found = await asyncio.to_thread(_view, ws, name)
        return {
            **found,
            "removed": {
                "images": done.removed,
                "missing": done.missing,
                "trimmed": done.trimmed,
                "note": done.note,
            },
        }

    return router
