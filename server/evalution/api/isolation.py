"""A target's isolation (ADR 0009): its plan and how far it is proven, finding and proving it
on evalution's own copy of the app, undoing the last version, and the test setup the copies
play on."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from evalution.api.deps import Current, busy, invalid, local_only, target_name
from evalution.api.workspaces import Workspace
from evalution.isolation import outbound
from evalution.isolation.isolate import code_folder, launch_spec, plan_spec
from evalution.isolation.plan import Plan, PlanStore, holds_state
from evalution.isolation.setup import SetupError
from evalution.project import isolating

#: What the page must send to start it: it spends two messages of the app's own copy (three when
#: the rehearsal also proves putting the copy back with the app up).
CONFIRM = "confirm"


def _view(ws: Workspace, name: str) -> dict[str, Any]:
    plan = _plan(ws, name)
    return {
        **isolating.view(ws.targets, name, ws.isolations),
        "outbound": _outbound(ws, name, plan),
        "put_back": _put_back(plan),
    }


def _plan(ws: Workspace, name: str) -> Plan | None:
    try:
        return PlanStore(ws.targets.config.data_dir).load(name)
    except ValueError:
        return None


def _spec(ws: Workspace, name: str, plan: Plan | None) -> dict[str, Any] | None:
    """How a copy of *name* starts: as its plan says, else as its code folder shows; ``None``
    when nothing says."""
    config = ws.targets.config
    try:
        if plan is not None:
            return plan_spec(config, name, plan)
        return launch_spec(code_folder(config, name).resolve(), config.targets[name].options)
    except (LookupError, OSError):
        return None


def _outbound(ws: Workspace, name: str, plan: Plan | None) -> list[dict[str, Any]]:
    """``[{name, kind, allowed}]``: the settings that would reach outside the copy, by name only;
    each starts empty on the copy unless allowed."""
    spec = _spec(ws, name, plan)
    if spec is None:
        return []
    return outbound.listed(spec, outbound.OutboundStore(ws.targets.config.data_dir).allowed(name))


def _put_back(plan: Plan | None) -> dict[str, Any] | None:
    """How a run puts the copy back between tries: ``hot`` is what the rehearsal proved of doing
    it with the app up (``proven``, ``failed``, ``None`` untried); ``app_holds_state``, that only
    a restart clears the app's memory. ``restart`` is true when every put back stops and starts
    the whole copy."""
    if plan is None:
        return None
    holds = holds_state(plan)
    return {
        "hot": plan.hot,
        "app_holds_state": holds,
        "restart": holds or plan.hot != "proven",
    }


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
        background, on evalution's own copy of the app. It sends two or three messages to that
        copy, so the page says so and sends ``confirm: true``."""
        name = target_name(ws.targets, target)
        if body.get(CONFIRM) is not True:
            raise invalid(
                "This sends two messages to evalution's copy of the app (three when it also proves "
                "putting it back with the app running): confirm it."
            )
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

    @router.get("/targets/{target}/isolation/setup", dependencies=hosted_off)
    async def get_setup(ws: Current, target: str) -> dict[str, Any]:
        """The test setup: the test person, what the copy starts with, when it is put back, the
        copies side by side; what the target supports; whether the plan is checked again before
        the next run."""
        name = target_name(ws.targets, target)
        return await asyncio.to_thread(isolating.setup_view, ws.targets, name)

    @router.put("/targets/{target}/isolation/setup", dependencies=hosted_off)
    async def put_setup(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Change any part of the test setup. A change to what the copy starts with marks a
        proven plan to be checked again before the next run. Not while a run plays, nor while
        isolation is being found."""
        name = target_name(ws.targets, target)
        if ws.runs.busy():
            raise busy("A run is going. Change the test setup when it ends.")
        if ws.isolations.running(name):
            raise busy("Testing is being set up for this app. Try again when it ends.")
        try:
            await asyncio.to_thread(isolating.change_setup, ws.targets, name, body)
        except SetupError as exc:
            raise invalid(str(exc)) from exc
        return await asyncio.to_thread(isolating.setup_view, ws.targets, name)

    @router.put("/targets/{target}/isolation/outbound", dependencies=hosted_off)
    async def put_outbound(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Allow one setting that reaches outside the copy, or block it again:
        ``{name, allowed}``. Names only; the copy starts with it as the person said from its next
        start. The list as it now is: ``{outbound: [{name, kind, allowed}]}``."""
        name = target_name(ws.targets, target)
        setting, allowed = body.get("name"), body.get("allowed")
        if not isinstance(setting, str) or not isinstance(allowed, bool):
            raise invalid("Send the setting's name and allowed: true or false.")
        if ws.runs.busy():
            raise busy("A run is going. Change what leaves the copy when it ends.")
        if ws.isolations.running(name):
            raise busy("Testing is being set up for this app. Try again when it ends.")
        listed = await asyncio.to_thread(_outbound, ws, name, _plan(ws, name))
        if setting not in {o["name"] for o in listed}:
            raise invalid(f"{setting} isn't a setting that reaches outside the copy.")
        store = outbound.OutboundStore(ws.targets.config.data_dir)
        await asyncio.to_thread(store.allow, name, setting, allowed)
        return {"outbound": await asyncio.to_thread(_outbound, ws, name, _plan(ws, name))}

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
