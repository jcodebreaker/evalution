"""How tests reach a target: its API, or its page by a map the test writer learned (ADR 0010).
The map tried on evalution's copy is approved here, once, and saved as the target's ``page:``
block (beside ``evalution.yaml`` for a target set there); undoing goes back to the map before.

UI testing is set up here in one flow (docs/plans/ui-testing-ease.md, 2): what this computer
lacks for it is installed from the page (``/settings/ui-testing/install``), and the agent learns
a target's page with the page following its steps (``/targets/{t}/page/learn``), its rehearsal's
screenshots and video then waiting for one Approve."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Response

from evalution.api.deps import (
    Current,
    busy,
    invalid,
    known_set,
    local_only,
    not_found,
    target_name,
)
from evalution.api.workspaces import Workspace
from evalution.connect import page
from evalution.core.evidence import VIDEO
from evalution.project import pagemaps, profile
from evalution.runs.page import install
from evalution.runs.page.learn import lacking, refusal
from evalution.runs.page.target import page_lacks
from evalution.storage.files import FileNameError
from evalution.writer import paging


def _view(ws: Workspace, name: str) -> dict[str, Any]:
    learns = ws.page_learns
    # The agent learning the page from the Set up UI testing button, while it does and after.
    return {**page.view(ws.targets, name), "learn": learns.view(name) if learns else None}


def _ui_testing(ws: Workspace) -> dict[str, Any]:
    """What UI testing has on this computer (as ``evalution doctor`` reads it), whether the page
    can install what it lacks, and the install job, when there is one."""
    missing = page_lacks(True)
    _steps, why_not = install.plan()
    job = ws.installs.job
    return {
        "ready": not missing,
        "missing": [m.as_dict() for m in missing],
        "can_install": bool(missing) and why_not is None,
        "why_not_install": why_not if missing else None,
        "job": job.view() if job is not None else None,
    }


def _offer(ws: Workspace, name: str) -> dict[str, Any]:
    """The UI testing offer for *name*: made where its tests could play through its page (a
    workspace that keeps files: locally or in CI), its code has a chat page, and nothing about
    its page is set up, waiting or turned down. ``can_learn``/``why_not``: whether the agent can
    learn the page now, or what comes first. ``underway``: no offer, as Set up UI testing was
    pressed: the agent is learning the page, or its map waits for Approve, which the card
    shows."""
    targets = ws.targets
    dismissed = bool(profile.connection(targets, name).get("ui_offer_dismissed"))
    try:
        waiting = pagemaps.store(targets).proposed(name) is not None
    except ValueError:
        waiting = True
    learning = ws.page_learns is not None and ws.page_learns.running(name)
    open_to = not dismissed and targets.files is not None
    found = None
    if open_to and pagemaps.block_of(targets, name) is None:
        found = paging.chat_page(targets, name)
    underway = found is not None and (waiting or learning)
    if underway:
        found = None
    why_not = None
    missing = None
    if found is not None:
        try:
            capabilities = targets.adapter(name).capabilities
            why_not = refusal(targets.config, name, capabilities)
            if why_not is not None:
                missing = lacking(targets.config, name, capabilities)
        except Exception as exc:  # an adapter that can't be made can't be played through its page
            why_not = f"{name}'s adapter can't be made: {exc}"
    return {
        "target": name,
        "offer": found is not None,
        "underway": underway,
        "found_in": found,
        "dismissed": dismissed,
        "can_learn": found is not None and why_not is None,
        "why_not": why_not,
        # What UI testing lacks, when that's why not, with what fixes it.
        "missing": missing.as_dict() if missing is not None else None,
    }


def _not_playing(ws: Workspace, name: str) -> None:
    """:raises HTTPException: 409, a run is playing on *name*: its map doesn't change under it."""
    if ws.runs.playing(name):
        raise busy("A run is going on this app. Try again when it ends.")


def page_router() -> APIRouter:
    router = APIRouter()
    hosted_off = [Depends(local_only)]

    @router.get("/settings/ui-testing", dependencies=hosted_off)
    async def get_ui_testing(ws: Current) -> dict[str, Any]:
        """What UI testing has on this computer, without launching anything (ADR 0010): ready,
        or each piece it lacks in a sentence, with the command that installs it. The same
        reading as ``evalution doctor``'s. ``can_install``: the page's Install installs it
        here (else ``why_not_install`` says how); ``job``: the install, while it goes and
        after."""
        return await asyncio.to_thread(_ui_testing, ws)

    @router.post("/settings/ui-testing/install", status_code=202, dependencies=hosted_off)
    async def install_ui_testing(ws: Current) -> dict[str, Any]:
        """Install what UI testing lacks, on the computer this server runs on, in the
        background: only fixed commands (:mod:`evalution.runs.page.install`), never one sent
        here. Refused while a run goes or the agent is learning a page, which use the browser
        being installed. With nothing missing, nothing runs."""
        if ws.runs.busy() or ws.runs.probing:
            raise busy("A run is going. Install when it ends.")
        if ws.page_learns is not None and ws.page_learns.running():
            raise busy("The agent is learning a page with the browser. Install when it ends.")
        try:
            ws.installs.start()
        except RuntimeError as exc:
            raise busy(str(exc)) from exc
        return await asyncio.to_thread(_ui_testing, ws)

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

    @router.put("/targets/{target}/page/proof", dependencies=hosted_off)
    async def put_proof(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """What every try played through the page keeps: ``screenshots`` and ``video``, each
        ``always``, ``failed`` or ``off``; a part left out stays as it is. A test's own
        ``page.proof`` still wins over it."""
        targets = ws.targets
        name = target_name(targets, target)
        async with ws.lock:
            _not_playing(ws, name)
            try:
                await asyncio.to_thread(page.set_proof, targets, name, body)
            except page.PageMapError as exc:
                raise invalid(str(exc)) from exc
            await targets.reload(name)
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/page/learn", status_code=202, dependencies=hosted_off)
    async def learn_page(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Set up UI testing: the agent learns the page (or learns it again) on evalution's copy,
        in the background, the page following its steps; a map that showed its reply then waits
        for Approve. Trying the map sends one message to the copy, so the page says so and sends
        ``confirm: true``. ``set``: the set whose chat the agent works in (default its first)."""
        name = target_name(ws.targets, target)
        if body.get("confirm") is not True:
            raise invalid("Trying the map sends one message to evalution's copy: confirm it.")
        set_name = body.get("set")
        if set_name is not None:
            if not isinstance(set_name, str):
                raise invalid("set: a set's id")
            known_set(ws.targets, name, set_name)
        learns = ws.page_learns
        if learns is None:
            raise invalid("Not available hosted")
        if ws.runs.busy() or ws.runs.probing:
            raise busy("A run is going. Try again when it ends.")
        if ws.isolations.running(name):
            raise busy("Testing is being set up for this app. Try again when it ends.")
        if ws.installs.running():
            raise busy("UI testing is being installed. Try again when it's done.")
        try:
            why_not = refusal(ws.targets.config, name, ws.targets.adapter(name).capabilities)
        except Exception as exc:  # an adapter that can't be made can't be played through its page
            why_not = f"{name}'s adapter can't be made: {exc}"
        if why_not is not None:
            raise busy(why_not)
        try:
            await learns.start(name, set_name)
        except RuntimeError as exc:
            raise busy(str(exc)) from exc
        except LookupError as exc:
            raise invalid(str(exc)) from exc
        return await asyncio.to_thread(_view, ws, name)

    @router.post("/targets/{target}/page/learn/stop", dependencies=hosted_off)
    async def stop_learning(ws: Current, target: str) -> dict[str, Any]:
        """Stop the agent learning the page; a map it already tried stays waiting."""
        name = target_name(ws.targets, target)
        if ws.page_learns is not None:
            await ws.page_learns.stop(name)
        return await asyncio.to_thread(_view, ws, name)

    @router.get("/targets/{target}/page/offer")
    async def get_offer(ws: Current, target: str) -> dict[str, Any]:
        """Whether to offer UI testing: the target's code has a chat page, and it has no page
        map, none waiting for approval, and the offer wasn't turned down."""
        name = target_name(ws.targets, target)
        return await asyncio.to_thread(_offer, ws, name)

    @router.post("/targets/{target}/page/offer/dismiss")
    async def dismiss_offer(ws: Current, target: str) -> dict[str, Any]:
        """Not now: the offer isn't made again for this target; the Test environment page still
        sets UI testing up."""
        targets = ws.targets
        name = target_name(targets, target)
        await asyncio.to_thread(profile.update_connection, targets, name, ui_offer_dismissed=True)
        return await asyncio.to_thread(_offer, ws, name)

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
        kind = "video/webm" if shot == VIDEO else "image/png"
        return Response(data, media_type=kind, headers=headers)

    return router
