"""Where apps send spans when evalution keeps traces itself."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from evalution.api.deps import not_found
from evalution.targets import Targets
from evalution.traces import otlp


def traces_router(targets: Targets) -> APIRouter:
    router = APIRouter()

    @router.get("/traces")
    async def get_traces() -> dict[str, Any]:
        """Whether evalution keeps traces itself, and where apps send them."""
        return {"managed": targets.managed_url is not None, "url": targets.managed_url}

    @router.post("/traces")
    async def post_traces(request: Request) -> Response:
        """Spans over OTLP/HTTP, for the built-in source; there is nothing here without it."""
        if targets.store is None:
            raise not_found("Trace intake")
        return await otlp.receive(request, targets.store)

    return router
