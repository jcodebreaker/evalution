"""What evalution understood of a target, and keeping that current."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from evalution.api.deps import busy, invalid, not_found, target_name
from evalution.code import CodeError
from evalution.project import hooks, knowledge, profile, understanding
from evalution.runs.runner import Runs
from evalution.suite.cases import list_sets
from evalution.writer.agent import Agent


async def _understood(runs: Runs, name: str) -> dict[str, Any]:
    found = await asyncio.to_thread(understanding.view, runs.targets, runs, name)
    return {**found, "knowledge": await asyncio.to_thread(knowledge.summary, runs, name)}


def understanding_router(runs: Runs, agent: Agent, url: str) -> APIRouter:
    router = APIRouter()
    targets = runs.targets

    @router.get("/targets/{target}/understanding")
    async def get_understanding(target: str) -> dict[str, Any]:
        """What evalution understood of the target, with the admin's steers and the numbers as
        they are now; ``status: "learning"`` before the agent wrote it."""
        return await _understood(runs, target_name(targets, target))

    @router.post("/targets/{target}/understanding/steer")
    async def steer_understanding(target: str, body: dict[str, Any]) -> dict[str, Any]:
        """A claim or node marked right or wrong or fixed, a priority picked or added, a
        starter selected."""
        name = target_name(targets, target)
        try:
            await asyncio.to_thread(understanding.steer, targets, name, body)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise invalid(str(exc)) from exc
        return await _understood(runs, name)

    @router.post("/targets/{target}/understanding/update")
    async def update_understanding(target: str) -> dict[str, Any]:
        """Bring what it knows up to date now: the one thing that spends in ``manual``."""
        name = target_name(targets, target)
        outcome = await knowledge.refresh(runs, name, asked=True)
        if outcome == "no_model":
            raise busy("No model is set up yet.")
        if outcome in ("failed", "busy", "superseded"):
            raise busy("Couldn't update. Nothing changed.")
        return await _understood(runs, name)

    @router.post("/targets/{target}/understanding/undo")
    async def undo_understanding(target: str) -> dict[str, Any]:
        """Put back what it knew before the last update from a commit."""
        name = target_name(targets, target)
        try:
            await asyncio.to_thread(knowledge.undo, targets, name)
        except LookupError as exc:
            raise busy(str(exc)) from exc
        return await _understood(runs, name)

    @router.post("/targets/{target}/understanding/seen", status_code=204)
    async def saw_understanding(target: str) -> None:
        """The owner opened what it learned: Home stops saying what changed."""
        await asyncio.to_thread(knowledge.saw, targets, target_name(targets, target))

    @router.get("/targets/{target}/updates")
    async def get_updates(target: str) -> dict[str, str]:
        return {"mode": profile.updates(targets, target_name(targets, target))}

    @router.put("/targets/{target}/updates")
    async def put_updates(target: str, body: dict[str, Any]) -> dict[str, str]:
        """``auto``: a light model call per committed change. ``manual``: only on a click."""
        name = target_name(targets, target)
        try:
            await knowledge.choose(runs, name, body.get("mode"))
        except ValueError as exc:
            raise invalid(str(exc)) from exc
        return {"mode": profile.updates(targets, name)}

    @router.get("/targets/{target}/hooks")
    async def get_hooks(target: str) -> dict[str, Any]:
        name = target_name(targets, target)
        return await asyncio.to_thread(hooks.status, targets.code(name), name)

    @router.post("/targets/{target}/hooks")
    async def post_hooks(target: str) -> dict[str, Any]:
        """Put the hooks into the target's repo. Only this and the command ever do."""
        name = target_name(targets, target)
        try:
            return await asyncio.to_thread(hooks.install, targets.code(name), name, url)
        except CodeError as exc:
            raise busy(str(exc)) from exc
        except ValueError as exc:
            raise invalid(str(exc)) from exc

    @router.delete("/targets/{target}/hooks")
    async def delete_hooks(target: str) -> dict[str, Any]:
        name = target_name(targets, target)
        return await asyncio.to_thread(hooks.uninstall, targets.code(name), name)

    @router.post("/targets/{target}/understanding/plan")
    async def plan_starters(target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Ask the agent to plan the chosen starters; returns its session. ``session``: the one
        to ask, else a new one on ``set`` (else the first set)."""
        name = target_name(targets, target)
        ids = body.get("starters")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise invalid("starters: a list of suggested test ids")
        doc = understanding.load(targets, name)
        if doc is None:
            raise not_found("Understanding")
        starters = {s["id"]: s for s in doc.get("starters") or []}
        if missing := [i for i in ids if i not in starters]:
            raise not_found(f"Suggested test {missing[0]}")
        exists = await asyncio.to_thread(understanding.existing, targets, name)
        if made := [starters[i]["title"] for i in ids if exists(starters[i])]:
            raise busy(f"Already a test: {made[0]}")
        for sid in starters:
            understanding.steer(targets, name, {"starter": sid, "selected": sid in ids})
        text = "Plan these starters: " + "; ".join(f"{starters[i]['title']} ({i})" for i in ids)
        session_id = body.get("session")
        try:
            if not isinstance(session_id, str) or not session_id:
                sets = list_sets(targets.docs, name)
                set_name = body.get("set") or (sets[0] if sets else None)
                if not isinstance(set_name, str):
                    raise invalid("set: this app has no test set")
                session_id = (await agent.create(name, set_name))["id"]
            return await agent.message(session_id, text)
        except LookupError as exc:
            raise not_found("Test writer session or test set") from exc

    @router.get("/targets/{target}/card.md")
    async def get_card(target: str) -> PlainTextResponse:
        """The project card as Markdown."""
        found = await asyncio.to_thread(
            understanding.view, targets, runs, target_name(targets, target)
        )
        return PlainTextResponse(understanding.card_md(found), media_type="text/markdown")

    return router
