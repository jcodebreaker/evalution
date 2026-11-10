"""Connecting a target from the page. Secret values are kept as stored secrets and never
returned or logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Request

from evalution.adapters import adapter_info, broken, infos
from evalution.adapters.page import LABEL as PAGE_ONLY_LABEL
from evalution.api.deps import (
    Current,
    busy,
    invalid,
    local_only,
    not_found,
    read_body,
    target_name,
)
from evalution.api.workspaces import Workspace
from evalution.config import ConfigError, TargetConfig
from evalution.connect import connect, discover, probe
from evalution.core.case import SLUG
from evalution.project.profile import connection, update_connection
from evalution.runs.page.map import described, page_map_of, page_only
from evalution.targets import Targets


def _parse(
    targets: Targets, name: str, body: dict[str, Any], stored: set[str]
) -> tuple[dict, dict]:
    if not targets.folders and body.get("code"):
        raise invalid("Code folders are not available hosted")
    try:
        block, changes = connect.parse(name, body, stored)
        connect.draft(name, block, targets.config.root, targets.config.keys)
    except connect.TargetError as exc:
        raise invalid(str(exc)) from exc
    return block, changes


def _managed(targets: Targets, name: str) -> str:
    name = target_name(targets, name)
    if name not in targets.config.managed:
        raise busy(f"{name} is set in {targets.config.file.name}; edit it there")
    return name


def _page(conf: TargetConfig) -> dict[str, Any] | None:
    """How a target plays through its page (ADR 0010), in plain words; ``None`` when it plays
    through its API. Read only here: the map is learned and approved on the Test environment page
    (:mod:`evalution.connect.page`), or written by hand in the target's ``page:`` block."""
    only = page_only(conf)
    try:
        found = page_map_of(conf)
    except ConfigError as exc:
        return {"map": [], "error": str(exc), "only": only}
    if found is None:
        return None
    return {"map": described(found, only=only), "error": None, "only": only}


def _view(targets: Targets, name: str) -> dict[str, Any]:
    conf = targets.conf(name)
    info = adapter_info(conf.adapter)
    label = info.label if info else PAGE_ONLY_LABEL if page_only(conf) else conf.adapter
    return {
        **connect.view(conf, name in targets.config.managed),
        "adapter_label": label,
        "page": _page(conf),
        #: Why its adapter doesn't load, when it doesn't: none of its runs can start.
        "adapter_error": broken().get(conf.adapter),
        "adapter_change": connection(targets, name).get("adapter_change"),
    }


def _keep_focus(block: dict[str, Any], body: dict[str, Any], kept: dict[str, Any]) -> None:
    """A body whose ``code`` leaves ``focus`` or ``learn`` out keeps the one stored (a form that
    doesn't show it); ``focus: null`` clears it (all of the agents again), ``learn: null`` sets
    the default (``on_demand``)."""
    given = body.get("code")
    old = kept.get("code") if isinstance(kept.get("code"), dict) else {}
    if not isinstance(given, dict) or not isinstance(block.get("code"), dict):
        return
    for key in ("focus", "learn"):
        if key not in given and isinstance(old.get(key), str):
            block["code"][key] = old[key]


def _adapter_row(name: str) -> dict[str, Any]:
    """An adapter as the form draws it."""
    info = infos()[name]
    return {
        "name": name,
        "label": info.label,
        "options": list(info.options),
        "required": list(info.required),
        "secrets": list(info.secrets),
    }


async def _create(targets: Targets, lock: asyncio.Lock, body: dict[str, Any]) -> dict[str, Any]:
    """Save a new target: its block, its secrets, and a first set when it has none."""
    name = body.get("name")
    if not isinstance(name, str):
        raise invalid("name: required")
    async with lock:
        if name in targets.config.targets:
            raise busy(f"An app named {name} already exists")
        block, changes = _parse(targets, name, body, set())
        await asyncio.to_thread(
            connect.save, targets.docs, targets.config.keys, name, block, changes
        )
        await targets.reload(name)
        await asyncio.to_thread(connect.starter_set, targets.docs, name)
    return _view(targets, name)


def _found(ws: Workspace) -> discover.Discoveries:
    """The apps found on disk: their routes are local only."""
    assert ws.found is not None
    return ws.found


def _job(ws: Workspace, job_id: str) -> discover.Job:
    try:
        return _found(ws).get(job_id)
    except LookupError as exc:
        raise not_found("That connection attempt") from exc


def _claim(ws: Workspace, name: str) -> None:
    """One message at a time per app, and none while a run plays, in this process or another;
    no run starts while it's on its way. :func:`_let_go` when it has ended."""
    runs, probes = ws.runs, ws.probes
    if runs.busy() or (name not in probes.busy and not runs.lock.acquire()):
        raise busy("A run is going. Try again when it ends.")
    if name in probes.busy:
        raise busy("A test message is already on its way.")
    probes.busy.add(name)


def _let_go(ws: Workspace, name: str) -> None:
    ws.probes.busy.discard(name)
    ws.runs.lock.release()


@contextlib.contextmanager
def _changing(ws: Workspace, name: str) -> Iterator[None]:
    """Nothing changes a target under its run or its test message, and neither starts until
    it's done."""
    if ws.runs.playing(name):
        raise busy("A run is going on this app. Try again when it ends.")
    if name in ws.probes.busy:
        raise busy("A test message is on its way. Try again when it's back.")
    ws.probes.busy.add(name)
    try:
        yield
    finally:
        ws.probes.busy.discard(name)


def connect_router() -> APIRouter:
    router = APIRouter()
    hosted_off = [Depends(local_only)]

    @router.get("/adapters")
    async def get_adapters() -> dict[str, Any]:
        """The kinds of app evalution can connect, with what each one takes; and those there that
        don't load, with why."""
        return {
            "data": [_adapter_row(name) for name in infos()],
            "broken": [{"name": name, "error": why} for name, why in broken().items()],
        }

    @router.get("/targets/{target}")
    async def get_target(ws: Current, target: str) -> dict[str, Any]:
        """A target as the page edits it, without its secrets."""
        targets = ws.targets
        return _view(targets, target_name(targets, target))

    @router.post("/targets", status_code=201)
    async def create_target(ws: Current, request: Request) -> dict[str, Any]:
        """Connect a target from its technical settings (the page's Details)."""
        targets, lock = ws.targets, ws.lock
        return await _create(targets, lock, await read_body(request))

    @router.post("/targets/discover", status_code=202, dependencies=hosted_off)
    async def discover_target(ws: Current, body: dict[str, Any]) -> dict[str, Any]:
        """Look at a project folder, a repository on the code host (``owner/repo``,
        ``github.com/owner/repo``, ``owner/repo#412``, ``owner/repo@ref``: cloned into
        ``<data>/code/<target>`` first, with the person's own git credentials), or an app's
        address, in the background; follow it with the GET below."""
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            raise invalid("Type your app's address, its repository or its project folder.")
        return _found(ws).start(path).view()

    @router.get("/targets/discover/{job_id}", dependencies=hosted_off)
    async def get_discovery(ws: Current, job_id: str) -> dict[str, Any]:
        """The steps so far, the questions left and, once ready, the proposed target."""
        return _job(ws, job_id).view()

    @router.post("/targets/discover/{job_id}/traces", dependencies=hosted_off)
    async def check_traces(ws: Current, job_id: str) -> dict[str, Any]:
        """Check again whether the app's spans reach evalution."""
        job = _job(ws, job_id)
        await _found(ws).check_traces(job)
        return job.view()

    @router.post("/targets/discover/{job_id}/accept", status_code=201, dependencies=hosted_off)
    async def accept_discovery(ws: Current, job_id: str, request: Request) -> dict[str, Any]:
        """Save the proposed target, with ``answers`` to its questions and an optional
        ``name``; or a ``target`` body edited in Details (secrets found in the folder are kept
        unless it gives its own)."""
        job = _job(ws, job_id)
        if job.status != "ready":
            raise busy(
                "Still looking at the folder"
                if job.status == "working"
                else "That folder couldn't be read; try again"
            )
        body = await read_body(request)
        answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
        if isinstance(body.get("target"), dict):
            target = dict(body["target"])
            given = target.get("secrets") if isinstance(target.get("secrets"), dict) else {}
            # What the folder held goes only to the address it was found for.
            found_for = job.body(answers).get("options")
            kept = {} if connect.leaves_hosts(target.get("options"), found_for) else job.secrets
            target["secrets"] = {**kept, **given}
        else:
            missing = job.missing(answers)
            if missing:
                raise invalid(discover.QUESTIONS[missing[0]][0])
            target = job.body(answers)
            if isinstance(body.get("name"), str) and body["name"].strip():
                target["name"] = body["name"].strip()
        made = await _create(ws.targets, ws.lock, target)
        _found(ws).forget(job_id)
        return made

    @router.put("/targets/{target}")
    async def update_target(ws: Current, target: str, request: Request) -> dict[str, Any]:
        """Change a connected target. A secret left out is kept, unless it can't go to a new host
        (``secrets_dropped`` names those); ``null`` removes it."""
        targets, lock, probes = ws.targets, ws.lock, ws.probes
        body = await read_body(request)
        async with lock:
            name = _managed(targets, target)
            with _changing(ws, name):
                stored = connect.stored_keys(targets.config.keys, name)
                moved = connect.moved_secrets(targets.conf(name), body, stored)
                block, changes = _parse(targets, name, body, stored - moved)
                # The page map and its test accounts aren't edited in the form: they stay.
                kept = targets.conf(name).options
                block.update({k: kept[k] for k in ("page", "page_accounts") if k in kept})
                _keep_focus(block, body, kept)
                dropped = connect.left_behind(name, moved, changes)
                changes = {**connect.forgotten(name, moved), **changes}
                was = targets.conf(name).adapter
                probes.failed.pop(name, None)
                await asyncio.to_thread(
                    connect.save, targets.docs, targets.config.keys, name, block, changes
                )
                await targets.reload(name)
                if was != block["adapter"]:
                    change = {"from": was, "to": block["adapter"], "at": time.time()}
                    await asyncio.to_thread(update_connection, targets, name, adapter_change=change)
        return {**_view(targets, name), "secrets_dropped": dropped}

    @router.delete("/targets/{target}")
    async def delete_target(ws: Current, target: str) -> dict[str, Any]:
        """Disconnect a target: its config and secrets go; its sets, runs and profile stay."""
        targets, lock = ws.targets, ws.lock
        async with lock:
            name = _managed(targets, target)
            with _changing(ws, name):
                await asyncio.to_thread(connect.remove, targets.docs, targets.config.keys, name)
                await targets.reload(name)
        return {"name": name, "removed": True, "kept": "Its sets, runs and profile stay on disk."}

    @router.post("/targets/{target}/check")
    async def check_message(ws: Current, target: str) -> dict[str, Any]:
        """Send one real message through the saved target: its reply, its traces and what's wrong,
        and a pass is remembered, so the first run needn't send it again."""
        targets = ws.targets
        name = target_name(targets, target)
        _claim(ws, name)
        try:
            return await probe.probe(targets, name, ws.llm, ws.probes)
        finally:
            _let_go(ws, name)

    @router.post("/targets/{target}/check/redraft")
    async def redraft_target(ws: Current, target: str) -> dict[str, Any]:
        """After a failed message, up to two corrected configs from the model, each tried with one
        more message. Saves nothing: the page saves the one that worked, once its owner says so."""
        targets, probes = ws.targets, ws.probes
        name = _managed(targets, target)
        if name not in probes.failed:
            raise busy("Send a test message first.")
        if not ws.llm:
            raise invalid("No model is set up yet")
        _claim(ws, name)
        try:
            return await probe.redraft(targets, name, ws.llm, probes)
        finally:
            _let_go(ws, name)

    @router.post("/targets/{target}/reach")
    async def reach_target(ws: Current, target: str, request: Request) -> dict[str, Any]:
        """Try each part of a target without playing anything. With a target body, the form as
        it stands (not saved; its secrets are used for this check only), else the saved one."""
        targets = ws.targets
        body = await read_body(request) if await request.body() else {}
        if "adapter" in body:
            if not SLUG.match(target):
                raise invalid("name: lowercase letters, digits, - and _")
            managed = target in targets.config.managed
            stored = connect.stored_keys(targets.config.keys, target) if managed else set()
            if stored:
                stored -= connect.moved_secrets(targets.conf(target), body, stored)
            block, changes = _parse(targets, target, body, stored)
            conf = connect.draft(target, block, targets.config.root, targets.config.keys)
            overlay = {k: v for k, v in changes.items() if v}
        else:
            conf, overlay = targets.conf(target_name(targets, target)), {}
        return await connect.check(conf, overlay, targets.store)

    return router
