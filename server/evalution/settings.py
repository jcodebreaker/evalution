"""The model set from the page (docs/architecture.md, "Config"): ``model.yaml`` next to the
config, written only here, and its key in ``<data_dir>/secrets.json`` as ``model.api_key``.
A ``model:`` in ``evalution.yaml`` wins and is never written. The key is never sent back.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

from evalution.config import MODEL_SECRET, Config, ModelConfig, read_secrets, write_secrets
from evalution.core import net
from evalution.llm import LLM, LLMError, endpoint

PROVIDERS = ("anthropic", "openai", "compatible")
URLS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1"}
#: The env var a page-set model's key may come from, before the stored one.
KEY_ENV = "EVALUTION_MODEL_KEY"
LIST_SECONDS = 15.0
TEST_TOKENS = 64
NOT_THE_PROTOCOL = "The provider did not answer with a model list"


class SettingsError(ValueError):
    """A body that does not follow the shape; the message names the field."""


class WrongAddress(SettingsError):
    """Something answered there, but not this protocol: the API may hang elsewhere."""


def provider(model: ModelConfig) -> str:
    if model.kind == "anthropic":
        return "anthropic"
    return "openai" if model.base_url == URLS["openai"] else "compatible"


def view(config: Config, spent_usd: float) -> dict[str, Any]:
    """The model as the page shows it: no key, only whether one is set."""
    model = config.model
    env = model.api_key_env if model else KEY_ENV
    from_env = bool(env and os.environ.get(env, "").strip())
    source = "config" if config.model_source == "config" else "env" if from_env else "page"
    return {
        "provider": provider(model) if model else "anthropic",
        "base_url": model.base_url if model else URLS["anthropic"],
        "strong": model.models.get("strong", "") if model else "",
        "light": model.models.get("light", "") if model else "",
        "key_set": bool(model.api_key()) if model else bool(read_secrets().get(MODEL_SECRET)),
        "source": source,
        "spent_usd": round(spent_usd, 4),
        "addresses": URLS,
    }


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{key}: required")
    return value.strip()


def _api_root(url: str) -> str:
    """Where an OpenAI-compatible API's ``/models`` and ``/chat/completions`` hang, however it was
    written: a bare server gets the ``/v1`` such servers use, and a full endpoint loses its tail."""
    for tail in ("/chat/completions", "/completions", "/models"):
        url = url.removesuffix(tail)
    return url if urlsplit(url).path.strip("/") else f"{url}/v1"


def base_url(name: Any, given: Any) -> str:
    """The API's base URL for provider *name*: fixed for Claude and OpenAI, given otherwise."""
    if name not in PROVIDERS:
        raise SettingsError(f"provider: one of {', '.join(PROVIDERS)}")
    if name != "compatible":
        return URLS[name]
    if not isinstance(given, str) or not given.strip().startswith(("http://", "https://")):
        raise SettingsError("base_url: an http(s) URL")
    return _api_root(given.strip().rstrip("/"))


def parse(body: dict[str, Any], prices: dict[str, dict[str, float]]) -> ModelConfig:
    """The :class:`ModelConfig` a page body asks for (*prices* kept from the file).

    :raises SettingsError: A field that does not follow the shape.
    """
    url = base_url(body.get("provider"), body.get("base_url"))
    key = body.get("key")
    if key is not None and not isinstance(key, str):
        raise SettingsError("key: must be a string")
    return ModelConfig(
        kind="anthropic" if body["provider"] == "anthropic" else "openai",
        base_url=url,
        api_key_env=KEY_ENV,
        models={"strong": _text(body, "strong"), "light": _text(body, "light")},
        prices=prices,
    )


def _file_prices(file: Path) -> dict[str, dict[str, float]]:
    if not file.is_file():
        return {}
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    model = data.get("model") if isinstance(data, dict) else None
    prices = model.get("prices") if isinstance(model, dict) else None
    return prices if isinstance(prices, dict) else {}


def save(config: Config, body: dict[str, Any]) -> bool:
    """Write the body's model to ``model.yaml`` and its key (a value sets, ``null`` removes,
    absent or empty keeps). Whether a stored key stayed behind: one in the environment goes where
    it is pointed, but one a form stored goes only where it was given for.

    :raises SettingsError: A field that does not follow the shape.
    """
    model = parse(body, _file_prices(config.model_file))
    block: dict[str, Any] = {
        "kind": model.kind,
        "base_url": model.base_url,
        "api_key_env": KEY_ENV,
        "models": model.models,
    }
    if model.prices:
        block["prices"] = model.prices
    key = body.get("key")
    dropped = False
    if key is None and "key" in body:
        write_secrets({MODEL_SECRET: None})
    elif isinstance(key, str) and key.strip():
        write_secrets({MODEL_SECRET: key.strip()})
    elif _moved(config, model.base_url):
        dropped = bool(read_secrets().get(MODEL_SECRET))
        write_secrets({MODEL_SECRET: None})
    file = config.model_file
    tmp = file.with_name(f".{file.name}.tmp")
    head = "# evalution's model, set from the page. The key is not here.\n"
    tmp.write_text(head + yaml.safe_dump({"model": block}, sort_keys=False), encoding="utf-8")
    tmp.replace(file)
    return dropped


def draft(config: Config, body: dict[str, Any]) -> ModelConfig:
    """The model a form asks for, not saved; the key it will have once saved: a typed one, else one
    in the environment, else the stored one, but only on the host it was stored for."""
    model = parse(body, config.model.prices if config.model else {})
    key = body.get("key")
    if isinstance(key, str) and key.strip():
        return replace(model, key=key.strip())
    if config.model is not None:
        model = replace(model, api_key_env=config.model.api_key_env)
    return replace(model, no_stored_key=_moved(config, model.base_url))


def _moved(config: Config, url: str) -> bool:
    """Whether *url* is on another host than the saved model's."""
    saved = config.model
    return saved is not None and urlsplit(saved.base_url).hostname != urlsplit(url).hostname


async def list_models(
    model: ModelConfig, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    """The models the provider offers, with their price per million tokens when known.

    :raises SettingsError: The provider did not answer with a list.
    """
    path = "/v1/models?limit=1000" if model.kind == "anthropic" else "/models"
    url, headers = endpoint(model, path)
    async with net.client(timeout=LIST_SECONDS, transport=transport) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise SettingsError(net.explain(url, exc)) from exc
    if response.status_code in (401, 403):
        raise SettingsError("The key was refused")
    if response.status_code in (404, 405):
        raise WrongAddress(f"The provider answered {response.status_code}")
    if not response.is_success:
        raise SettingsError(f"The provider answered {response.status_code}")
    try:
        rows = response.json().get("data")
    except (ValueError, AttributeError) as exc:
        raise WrongAddress(NOT_THE_PROTOCOL) from exc
    if not isinstance(rows, list):
        raise WrongAddress(NOT_THE_PROTOCOL)
    found = []
    for row in rows:
        name = str(row.get("id") or "") if isinstance(row, dict) else ""
        if not name:
            continue
        entry: dict[str, Any] = {"id": name, "name": str(row.get("display_name") or name)}
        if rate := model.rate(name):
            entry["input"], entry["output"] = rate.get("input", 0.0), rate.get("output", 0.0)
        found.append(entry)
    return sorted(found, key=lambda m: m["id"])


def _other_root(url: str) -> str:
    """The same server with ``/v1`` added, or taken off."""
    return url.removesuffix("/v1") if url.endswith("/v1") else f"{url}/v1"


async def find_models(
    model: ModelConfig, given: str | None, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """The models at *model*'s address, or, where only that fails to speak the protocol, at the same
    server with ``/v1`` added or taken off. ``{data, base_url}``, with ``note`` only when the
    address that works isn't the one *given*.

    :raises SettingsError: Neither answered with a list; the first one's reason.
    """
    url = model.base_url
    try:
        found = await list_models(model, transport)
    except WrongAddress as first:
        if provider(model) != "compatible":
            raise
        url = _other_root(url)
        try:
            found = await list_models(replace(model, base_url=url), transport)
        except SettingsError:
            raise first from None
    wrote = (given or "").strip().rstrip("/")
    answer: dict[str, Any] = {"data": found, "base_url": url}
    if wrote and wrote != url:
        answer["note"] = f"Using {url}: that's where this server answers."
    return answer


def _failure(message: str) -> str:
    """An :class:`LLMError` in a few words, without the provider's body."""
    head = message.split(":")[0]
    if head.endswith((" 401", " 403")):
        return "The key was refused"
    if head.endswith(" 404"):
        return "No such model"
    return head[:1].upper() + head[1:]


async def test(
    model: ModelConfig, spend_file: Path | None, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """One tiny call on each of the strong and light models: ``{key: {ok, detail, ms}}``."""
    llm = LLM(model, transport=transport, spend_file=spend_file)

    async def one(key: str) -> dict[str, Any]:
        started = time.monotonic()
        try:
            hello = [{"role": "user", "content": "Hi"}]
            reply = await llm.chat(key, "Answer in one word.", hello, max_tokens=TEST_TOKENS)
        except LLMError as exc:
            ok, detail = False, _failure(str(exc))
        else:
            ok, detail = True, reply.model or model.models[key]
        return {"ok": ok, "detail": detail, "ms": round((time.monotonic() - started) * 1000)}

    try:
        strong, light = await asyncio.gather(one("strong"), one("light"))
    finally:
        await llm.close()
    return {"strong": strong, "light": light}
