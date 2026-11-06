"""The targets, and what is stored for one of them.

Two routers because the one-target routes must come after ``connect``'s discovery routes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from evalution.api.deps import Current, invalid, not_found, target_name
from evalution.config import ConfigError
from evalution.core.case import CLEAN_KINDS
from evalution.project import drift, profile
from evalution.runs.preflight import passed_setups, setup_key
from evalution.suite.store import list_sets


def targets_router() -> APIRouter:
    router = APIRouter()

    @router.get("/targets")
    async def get_targets(ws: Current) -> dict[str, Any]:
        """The targets, with what each can do and how many sets and drift changes it has."""
        targets, runs = ws.targets, ws.runs
        await runs.recover()
        rows = []
        passed = passed_setups(targets.config.data_dir)
        for name in targets.names():
            conf = targets.conf(name)
            try:
                adapter = targets.adapter(name)
                declared = adapter.capabilities
                caps, note = declared.names(), declared.isolation_note
                clean, error = sorted(declared.starts_clean()), None
                checked = passed.get(name) == setup_key(adapter, conf)
            except (ConfigError, ImportError) as exc:
                caps, note, clean, checked, error = [], None, [], False, str(exc)
            rows.append(
                {
                    "name": name,
                    "adapter": conf.adapter,
                    "capabilities": caps,
                    "isolation_note": note,
                    "starts_clean": clean,
                    "required_clean": profile.required_clean(targets, name),
                    # One test message passed on these settings.
                    "checked": checked,
                    "error": error,
                    "phoenix": conf.phoenix is not None,
                    "code": conf.code is not None,
                    "sets": len(list_sets(targets.docs, name)),
                    "profile": bool(profile.read_profile(targets, name)),
                    "drift": len(drift.read(targets, name).get("changes") or []),
                    "managed": name in targets.config.managed,
                }
            )
        return {"data": rows}

    return router


def target_router() -> APIRouter:
    router = APIRouter()

    @router.get("/targets/{target}/sets")
    async def get_target_sets(ws: Current, target: str) -> dict[str, Any]:
        targets, runs = ws.targets, ws.runs
        return {"data": await asyncio.to_thread(runs.set_rows, target_name(targets, target))}

    @router.put("/targets/{target}/isolation")
    async def put_isolation(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """What every new test of this app must start clean of: only what the app can promise."""
        targets = ws.targets
        name = target_name(targets, target)
        kinds = body.get("required")
        if not isinstance(kinds, list) or set(map(str, kinds)) - set(CLEAN_KINDS):
            raise invalid(f"required: a list of {', '.join(CLEAN_KINDS)}")
        promised = targets.adapter(name).capabilities.starts_clean()
        if lacking := [k for k in kinds if k not in promised]:
            raise invalid(f"This app can't promise a clean start of {lacking[0]}")
        await asyncio.to_thread(profile.update_connection, targets, name, required_clean=kinds)
        return {"required": kinds}

    @router.get("/targets/{target}/profile")
    async def get_profile(ws: Current, target: str) -> dict[str, Any]:
        """The profile text, and the drift since it was written; 404 when there is none yet."""
        targets = ws.targets
        name = target_name(targets, target)
        if not profile.read_profile(targets, name):
            raise not_found("Profile")
        return {**profile.profile_view(targets, name), "drift": drift.read(targets, name)}

    @router.put("/targets/{target}/profile")
    async def put_profile(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """The admin's edit of the profile (the fingerprint stays as it was)."""
        targets = ws.targets
        name = target_name(targets, target)
        if not isinstance(body.get("text"), str):
            raise invalid("text: required")
        try:
            profile.write_profile(targets, name, body["text"])
        except ValueError as exc:
            raise invalid(str(exc)) from exc
        return {**profile.profile_view(targets, name), "drift": drift.read(targets, name)}

    return router
