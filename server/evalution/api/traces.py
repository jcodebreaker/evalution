"""Where apps send spans: ``POST /v1/traces``, standard OTLP into the workspace's built-in store.
Hosted, an app sends a workspace token with them, a traces token being the one made for that
(:mod:`evalution.api.auth`); locally, none."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from evalution.api.deps import Current
from evalution.targets import Targets
from evalution.traces import otlp


def traces_router() -> APIRouter:
    router = APIRouter()

    @router.get("/traces")
    async def get_traces(request: Request, ws: Current) -> dict[str, Any]:
        """Where apps send traces (``url``, the OTLP endpoint's base, as an exporter takes it),
        and whether they need a token to (``token``: hosted)."""
        return {"url": ws.traces_url, "token": bool(request.app.state.hosted)}

    @router.post("/traces")
    async def post_traces(request: Request, ws: Current) -> Response:
        return await otlp.receive(request, ws.targets.store)

    return router


def intake_router(targets: Targets) -> APIRouter:
    """Only the intake, over *targets*: what ``evalution run`` serves while it plays, outside any
    workspace's server."""
    router = APIRouter()

    @router.post("/traces")
    async def post_traces(request: Request) -> Response:
        return await otlp.receive(request, targets.store)

    return router
