"""evalution's own model. The key is kept as a stored secret and never returned or logged."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Header, Request

from evalution import settings
from evalution.api.deps import busy, invalid, read_body
from evalution.config import ConfigError, load
from evalution.llm import LLM
from evalution.targets import Targets


def _view(targets: Targets, llm: LLM) -> dict[str, Any]:
    return settings.view(targets.config, llm.spent_usd)


def settings_router(targets: Targets, llm: LLM, lock: asyncio.Lock) -> APIRouter:
    router = APIRouter()

    @router.get("/settings/model")
    async def get_model() -> dict[str, Any]:
        return _view(targets, llm)

    @router.put("/settings/model")
    async def put_model(request: Request) -> dict[str, Any]:
        """Save the model; every part uses it from the next call."""
        body = await read_body(request)
        if request.app.state.auth is not None and "key" in body and body["key"] != "":
            # Hosted, a model key is each person's own, never the workspace's.
            raise invalid("Save your own key with PUT /v1/me/model-key")
        if targets.config.model_source == "config":
            raise busy(f"The model is set in {targets.config.file.name}; edit it there")
        async with lock:
            try:
                dropped = await asyncio.to_thread(settings.save, targets.config, targets.docs, body)
                fresh = load(targets.config.file, targets.docs)
            except (settings.SettingsError, ConfigError) as exc:
                raise invalid(str(exc)) from exc
            targets.config = replace(
                targets.config, model=fresh.model, model_source=fresh.model_source
            )
            llm.use(fresh.model)
        return {**_view(targets, llm), "key_dropped": dropped}

    @router.get("/settings/model/models")
    async def get_models(
        provider: str,
        base_url: str | None = None,
        x_model_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """The provider's models with their prices; a key typed but not saved comes as the
        ``X-Model-Key`` header."""
        try:
            url = settings.base_url(provider, base_url)
            model = settings.draft(
                targets.config,
                {
                    "provider": provider,
                    "base_url": url,
                    "strong": "-",
                    "light": "-",
                    "key": x_model_key,
                },
            )
            return await settings.find_models(model, base_url)
        except settings.SettingsError as exc:
            raise invalid(str(exc)) from exc

    @router.post("/settings/model/test")
    async def test_model(request: Request) -> dict[str, Any]:
        """One tiny call on each model: the form as it stands when given, else the saved one."""
        body = await read_body(request)
        try:
            model = settings.draft(targets.config, body) if body else targets.config.model
        except settings.SettingsError as exc:
            raise invalid(str(exc)) from exc
        if model is None:
            raise invalid("No model is set up yet")
        return await settings.test(model, targets.docs)

    return router
