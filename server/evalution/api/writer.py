"""The test writer's sessions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from evalution.api.deps import invalid, not_found, set_root, target_name
from evalution.targets import Targets
from evalution.writer.agent import Agent


def writer_router(targets: Targets, agent: Agent) -> APIRouter:
    router = APIRouter()

    @router.post("/evals/agent/sessions")
    async def open_agent(body: dict[str, Any]) -> dict[str, Any]:
        name = target_name(targets, body.get("target"))
        set_name = body.get("set")
        if not isinstance(set_name, str) or not set_name:
            raise invalid("set: required")
        set_root(targets, name, set_name)
        return await agent.create(name, set_name)

    @router.get("/evals/agent/sessions/{session_id}")
    async def get_agent(session_id: str) -> dict[str, Any]:
        try:
            return await agent.get(session_id)
        except LookupError as exc:
            raise not_found("Test writer session") from exc

    @router.post("/evals/agent/sessions/{session_id}/messages")
    async def message_agent(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise invalid("text: required")
        run = body.get("run")
        if run is not None and not isinstance(run, str):
            raise invalid("run: must be a run id")
        try:
            return await agent.message(session_id, text.strip(), run)
        except LookupError as exc:
            raise not_found("Test writer session") from exc

    @router.post("/evals/agent/sessions/{session_id}/approve")
    async def approve_plan(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        plan_id = body.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            raise invalid("plan_id: required")
        try:
            return await agent.approve(session_id, plan_id)
        except LookupError as exc:
            raise not_found("Plan") from exc

    @router.post("/evals/agent/sessions/{session_id}/stop")
    async def stop_agent(session_id: str) -> dict[str, Any]:
        try:
            return await agent.stop(session_id)
        except LookupError as exc:
            raise not_found("Test writer session") from exc

    return router
