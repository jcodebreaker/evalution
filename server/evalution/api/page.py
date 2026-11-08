"""How tests reach a target: its API, or its page by a map the test writer learned (ADR 0010).
The map tried on evalution's copy is approved here, once, and saved into the target's ``page:``
block; undoing goes back to the map before."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Response

from evalution.api.deps import Current, busy, local_only, not_found, target_name
from evalution.api.workspaces import Workspace
from evalution.connect import page
from evalution.project import pagemaps
from evalution.runs.page.target import page_lacks
from evalution.storage.files import FileNameError


def _view(ws: Workspace, name: str) -> dict[str, Any]:
    return page.view(ws.targets, name)


def _not_playing(ws: Workspace, name: str) -> None:
    """:raises HTTPException: 409, a run is playing on *name*: its map doesn't change under it."""
    if ws.runs.playing(name):
        raise busy("A run is going on this app. Try again when it ends.")


def page_router() -> APIRouter:
    router = APIRouter()
    hosted_off = [Depends(local_only)]

    @router.get("/settings/ui-testing", dependencies=hosted_off)
    async def get_ui_testing() -> dict[str, Any]:
        """What UI testing has on this computer, without launching anything (ADR 0010): ready,
        or each piece it lacks in a sentence, with the command that installs it. The same
        reading as ``evalution doctor``'s."""
        missing = await asyncio.to_thread(page_lacks, True)
        return {"ready": not missing, "missing": [m.as_dict() for m in missing]}

    @router.get("/targets/{target}/page")
    async def get_page(ws: Current, target: str) -> dict[str, Any]:
        """How tests reach the app: the map in use and how it stands, the one waiting for
        approval with its rehearsal's screenshots, the versions kept, and whether the page can
        be learned here."""
        name = target_name(ws.targets, target)
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/page/approve", dependencies=hosted_off)
    async def approve_page(ws: Current, target: str) -> dict[str, Any]:
        """Approve the map tried on evalution's copy: it's saved into the target's page: block,
        and tests reach the app through its page from the next run."""
        targets = ws.targets
        name = target_name(targets, target)
        async with ws.lock:
            _not_playing(ws, name)
            try:
                await asyncio.to_thread(page.approve, targets, name)
            except LookupError as exc:
                raise busy(str(exc)) from exc
            except page.PageMapError as exc:
                raise busy(str(exc)) from exc
            await targets.reload(name)
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/page/undo", dependencies=hosted_off)
    async def undo_page(ws: Current, target: str) -> dict[str, Any]:
        """Go back to the map before this one; with none before, tests reach the app through its
        API again."""
        targets = ws.targets
        name = target_name(targets, target)
        async with ws.lock:
            _not_playing(ws, name)
            try:
                await asyncio.to_thread(page.undo, targets, name)
            except LookupError as exc:
                raise busy("There is no approved page map to undo.") from exc
            except page.PageMapError as exc:
                raise busy(str(exc)) from exc
            await targets.reload(name)
        return await asyncio.to_thread(_view, ws, name)

    @router.get("/targets/{target}/page/shots/{shot}", dependencies=hosted_off)
    async def get_shot(ws: Current, target: str, shot: str) -> Response:
        """A screenshot the rehearsal of the map waiting for approval, or of the one in use,
        took."""
        targets = ws.targets
        name = target_name(targets, target)
        files = targets.files
        kept_maps = pagemaps.store(targets)
        try:
            found = [kept_maps.proposed(name), kept_maps.load(name)]
        except ValueError as exc:
            raise not_found("Screenshot") from exc
        kept = next((k for k in found if k is not None and shot in k.shots and k.shots_at), None)
        at = kept.shots_at if kept is not None else None
        used = found[1]
        if at is None and used is not None and used.missing_shot == shot:
            # The page as the check before a run, or a try, found it lacking what the map names.
            at = used.missing_shot_at
        if files is None or at is None:
            raise not_found("Screenshot")

        def read() -> bytes:
            with files.open(str(at["run"]), str(at["case"]), int(at["n"]), shot) as handle:
                return handle.read()

        try:
            data = await asyncio.to_thread(read)
        except (FileNameError, FileNotFoundError, IsADirectoryError, KeyError) as exc:
            raise not_found("Screenshot") from exc
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"}
        return Response(data, media_type="image/png", headers=headers)

    return router
