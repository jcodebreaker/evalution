"""Targets connected from the page (docs/architecture.md, "Connecting a target").

A connected target is a block in the database (:func:`evalution.config.save_page_target`),
written only here, and its secrets are stored secrets named ``targets/<target>/<key>``; the block
names them the way a hand-edited one names env vars (``api_key_env: targets/my-app/api_key``).
Hand-edited targets in ``evalution.yaml`` are read, never written.

:func:`check` tries each part of a target without playing anything: the app's reachability, its
Phoenix project and conversations, and its code folder. :mod:`evalution.connect.probe` is the one
that sends a message.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from evalution.adapters import adapter_info, infos
from evalution.adapters.base import AdapterInfo, flat_options, part
from evalution.adapters.page import NAME as PAGE_ONLY
from evalution.code import HASH_CAP, Code
from evalution.config import (
    CodeConfig,
    ConfigError,
    KeyRing,
    TargetConfig,
    save_page_target,
    target_config,
)
from evalution.core import net
from evalution.core.case import SLUG
from evalution.storage import Docs
from evalution.suite.store import list_sets, save_set
from evalution.traces.builtin import SpanStore, reader
from evalution.traces.profile import recent_sessions

CHECK_SECONDS = 10.0
CHECK_SESSIONS = 20
CHECK_PAGES = 5
#: The starter set a new target gets, so the agent has somewhere to add tests.
STARTER_SET = "main"
PHOENIX_SECRET = "phoenix_api_key"  # noqa: S105 - its name, not its value
_SECRETS = "targets/"


class TargetError(ValueError):
    """A target body that does not follow the shape; the message names the field."""


def _text(block: dict[str, Any], key: str, where: str, required: bool = True) -> str | None:
    value = block.get(key)
    if value is None or value == "":
        if required:
            raise TargetError(f"{where}.{key}: required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise TargetError(f"{where}.{key}: must be a string")
    return value.strip()


def _ref(name: str, key: str) -> str:
    """The stored secret *key* of target *name*."""
    return f"{_SECRETS}{name}/{key}"


def parse(name: str, body: dict[str, Any], stored: set[str]) -> tuple[dict, dict]:
    """``(block, secrets)`` from a page's target *body*: the target's block, which names
    the secrets the target will have (the new ones in *body*, else the *stored* keys), and the
    secret changes (``key -> value``, ``None`` to remove).

    :raises TargetError: A field that does not follow the shape.
    """
    if not SLUG.match(name):
        raise TargetError("name: lowercase letters, digits, - and _")
    adapter = body.get("adapter")
    if not isinstance(adapter, str) or adapter not in infos():
        raise TargetError(f"adapter: one of {', '.join(infos())}")
    info = infos()[adapter]
    options = body.get("options")
    if not isinstance(options, dict):
        raise TargetError("options: required")
    unknown = sorted(set(options) - set(info.options))
    if unknown:
        raise TargetError(f"options.{unknown[0]}: not an option of the {adapter} adapter")
    options = {k: v for k, v in options.items() if v not in (None, "")}
    for key in info.required:
        if key not in options:
            raise TargetError(f"options.{key}: required")
    try:
        info.validate(options)
    except ValueError as exc:
        raise TargetError(str(exc)) from exc

    changes = _secrets(info, body.get("secrets"))
    removed = {k for k, v in changes.items() if v is None}
    have = (stored | {k for k, v in changes.items() if v}) - removed
    refs = {key: _ref(name, key) for key in info.secrets if key in have}
    block: dict[str, Any] = {"adapter": adapter, **info.block(options, refs)}

    phoenix = body.get("phoenix")
    if phoenix is not None and not isinstance(phoenix, dict):
        raise TargetError("phoenix: {url, project} or null")
    if phoenix:
        block["phoenix"] = {
            "url": _text(phoenix, "url", "phoenix"),
            "project": _text(phoenix, "project", "phoenix", required=False) or "default",
        }
        if public := _text(phoenix, "public_url", "phoenix", required=False):
            block["phoenix"]["public_url"] = public
        if PHOENIX_SECRET in have:
            block["phoenix"]["api_key_env"] = _ref(name, PHOENIX_SECRET)

    code = body.get("code")
    if isinstance(code, dict) and code.get("path"):
        block["code"] = {"path": _text(code, "path", "code")}
        for key in ("ignore", "include"):
            globs = code.get(key) or []
            if not isinstance(globs, list) or not all(isinstance(g, str) and g for g in globs):
                raise TargetError(f"code.{key}: a list of globs")
            if globs:
                block["code"][key] = globs
        summarize = code.get("summarize")
        if summarize is not None:
            if isinstance(summarize, bool) or not isinstance(summarize, int | float):
                raise TargetError("code.summarize: a share above 0, at most 1")
            block["code"]["summarize"] = summarize
        area_model = code.get("area_model")
        if area_model is not None:
            if area_model not in ("strong", "light"):
                raise TargetError("code.area_model: strong or light")
            block["code"]["area_model"] = area_model
    elif code not in (None, {}) and not isinstance(code, dict):
        raise TargetError("code: {path, ignore, include}")
    return block, {_ref(name, k): v for k, v in changes.items()}


def _secrets(info: AdapterInfo, given: Any) -> dict[str, str | None]:
    """The secret changes in a body: a value sets one, ``null`` removes it, absent keeps it."""
    if given is None:
        return {}
    if not isinstance(given, dict):
        raise TargetError("secrets: a mapping of key to value")
    allowed = (PHOENIX_SECRET, *info.secrets)
    out: dict[str, str | None] = {}
    for key, value in given.items():
        if key not in allowed:
            raise TargetError(f"secrets.{key}: not a secret of the {info.name} adapter")
        if value is not None and not isinstance(value, str):
            raise TargetError(f"secrets.{key}: must be a string")
        if value is None or value.strip():
            out[key] = value.strip() if value else None
    return out


def stored_keys(keys: KeyRing, name: str) -> set[str]:
    """The secret keys *name* has stored (never their values)."""
    prefix = _ref(name, "")
    return {k.removeprefix(prefix) for k in keys.store.names(prefix)}


def _host(url: str) -> str:
    """The host a call to *url* goes to, as the client reads it; an address it can't read is its
    own, so it matches nothing else."""
    try:
        return httpx.URL(url).host.lower() or url
    except httpx.InvalidURL:
        return url


def _hosts(options: Any) -> set[str]:
    """The hosts *options* call: every address in them at any depth, but what's sent as content."""
    if isinstance(options, list):
        return {h for item in options for h in _hosts(item)}
    if not isinstance(options, dict):
        return set()
    found: set[str] = set()
    for key, value in options.items():
        if isinstance(value, str) and (str(key) == "health" or str(key).endswith("url")):
            found.add(_host(value))
        elif key not in ("body", "headers"):
            found |= _hosts(value)
    return found


def leaves_hosts(options: Any, given_for: Any) -> bool:
    """Whether *options* reach a host that *given_for* doesn't."""
    return bool(_hosts(options) - _hosts(given_for))


def moved_secrets(conf: TargetConfig, body: dict[str, Any], stored: set[str]) -> set[str]:
    """The *stored* keys that can't stay with *body*: a secret goes only to the hosts it was given
    for, so a new host needs it typed again. Another port on the same host keeps it."""
    info = adapter_info(conf.adapter)
    old = info.page_options(conf.options) if info else flat_options(conf.options)
    moved: set[str] = set()
    if _hosts(body.get("options")) - _hosts(old):
        moved |= stored - {PHOENIX_SECRET}
    phoenix = body.get("phoenix") if isinstance(body.get("phoenix"), dict) else {}
    if _hosts({"url": phoenix.get("url")}) - _hosts(
        {"url": conf.phoenix.url if conf.phoenix else None}
    ):
        moved |= stored & {PHOENIX_SECRET}
    return moved


def left_behind(name: str, moved: set[str], changes: dict[str, Any]) -> list[str]:
    """Those of *moved* no value came with: the owner has to type them again."""
    return sorted(key for key in moved if not changes.get(_ref(name, key)))


def forgotten(name: str, keys: set[str]) -> dict[str, None]:
    """The secret changes that remove *keys* of *name*."""
    return {_ref(name, key): None for key in keys}


def view(conf: TargetConfig, managed: bool) -> dict[str, Any]:
    """A target as the page edits it: no secret values, and no secret names either (which of its
    secrets are stored, when *managed*)."""
    block = conf.options
    info = adapter_info(conf.adapter)
    options = info.page_options(block) if info else flat_options(block)
    options = {k: v for k, v in options.items() if not k.endswith("_env")}
    phoenix = None
    if conf.phoenix:
        phoenix = {"url": conf.phoenix.url, "project": conf.phoenix.project}
        if conf.phoenix.public_url:
            phoenix["public_url"] = conf.phoenix.public_url
    raw_code = block.get("code") if isinstance(block.get("code"), dict) and conf.code else None
    code = (
        {
            "path": str(raw_code["path"]),
            "ignore": list(conf.code.ignore),
            "include": list(conf.code.include),
            "summarize": conf.code.summarize,
            "area_model": conf.code.area_model,
        }
        if raw_code
        else None
    )
    return {
        "name": conf.name,
        "adapter": conf.adapter,
        "options": options,
        "phoenix": phoenix,
        "code": code,
        "secrets": sorted(stored_keys(conf.keys, conf.name)) if managed else [],
        "managed": managed,
    }


# Saving


def save(
    docs: Docs, keys: KeyRing, name: str, block: dict[str, Any], changes: dict[str, str | None]
) -> None:
    """Write *name*'s block and its secret changes."""
    for ref, value in changes.items():
        keys.store.set(ref, value)
    save_page_target(docs, name, block)


def remove(docs: Docs, keys: KeyRing, name: str) -> None:
    """Drop *name*'s block and its secrets. Its sets, runs and profile stay."""
    save_page_target(docs, name, None)
    for key in stored_keys(keys, name):
        keys.store.set(_ref(name, key), None)


def starter_set(docs: Docs, target: str) -> None:
    """A first, empty set for *target* when it has none."""
    if not list_sets(docs, target):
        save_set(docs, target, STARTER_SET, {"name": "Main"})


# The check


def draft(
    name: str, block: dict[str, Any], root: Path, keys: KeyRing | None = None
) -> TargetConfig:
    """A :class:`TargetConfig` of a block not yet saved, its secrets read from *keys* (default:
    the environment only).

    :raises TargetError: The block does not load.
    """
    try:
        return target_config(name, block, root, keys)
    except ConfigError as exc:
        raise TargetError(str(exc)) from exc


def _reached(exc: httpx.HTTPError) -> str:
    try:
        return net.explain(str(exc.request.url), exc)
    except RuntimeError:  # an error raised outside a request
        return f"Couldn't reach it ({type(exc).__name__})"


async def _adapter(conf: TargetConfig, lookup: Any) -> dict[str, Any]:
    info = adapter_info(conf.adapter)
    if info is None and conf.adapter == PAGE_ONLY:
        return part(
            True,
            "Reached through its page alone: there's no API to try. A run plays the page on "
            "evalution's own copy of the app.",
        )
    if info is None:
        return part(False, f"No adapter {conf.adapter!r}")
    async with net.client(timeout=CHECK_SECONDS) as client:
        try:
            return await info.check(client, conf.options, lookup)
        except httpx.HTTPError as exc:
            return part(False, _reached(exc))


async def _phoenix(
    conf: TargetConfig, attribute: str, lookup: Any, store: SpanStore
) -> dict[str, Any]:
    """The traces' part of the check; ``reached`` says whether their source answered at all. The
    spans evalution keeps for the target are read, and the app's own Phoenix when it has one."""
    empty = {"sessions_seen": 0, "has_llm_spans": False, "reached": True}
    key = lookup(conf.phoenix.api_key_env) if conf.phoenix else None
    source = reader(conf.name, conf.phoenix, store, api_key=key)
    try:
        found = await recent_sessions(source, attribute, CHECK_SESSIONS, CHECK_PAGES)
    except httpx.HTTPStatusError as exc:
        return part(False, f"Phoenix answered {exc.response.status_code}", **empty)
    except httpx.HTTPError as exc:
        return part(False, _reached(exc), **{**empty, "reached": False})
    finally:
        await source.close()
    llm = any(s.kind == "LLM" for spans in found for s in spans)
    seen = {"sessions_seen": len(found), "has_llm_spans": llm, "reached": True}
    if not found:
        return part(False, f"No conversations with {attribute} yet", **seen)
    return part(True, f"{len(found)} recent conversations", **seen)


def _code(code: CodeConfig | None) -> dict[str, Any]:
    if code is None:
        return part(True, "Not connected", files_matched=0)
    if not code.path.is_dir():
        return part(False, "No such folder", files_matched=0)
    matched = len(Code(code).files(HASH_CAP))
    if not matched:
        return part(False, "No source files", files_matched=0)
    return part(True, f"{matched} files", files_matched=matched)


async def check(conf: TargetConfig, overlay: dict[str, str], store: SpanStore) -> dict[str, Any]:
    """Each part of *conf*, tried: secrets from *overlay* (a form not saved yet) first; its
    traces in *store*, the workspace's spans."""

    def lookup(ref: Any) -> str | None:
        return overlay.get(ref) or conf.keys.get(ref) if isinstance(ref, str) else None

    adapter, phoenix = await asyncio.gather(
        asyncio.wait_for(_adapter(conf, lookup), CHECK_SECONDS * 2),
        asyncio.wait_for(_phoenix(conf, conf.trace_attribute, lookup, store), CHECK_SECONDS * 3),
        return_exceptions=True,
    )
    if isinstance(adapter, BaseException):
        adapter = part(False, "No answer in time")
    if isinstance(phoenix, BaseException):
        phoenix = part(
            False, "No answer in time", sessions_seen=0, has_llm_spans=False, reached=False
        )
    return {
        "adapter": adapter,
        "phoenix": phoenix,
        "code": await asyncio.to_thread(_code, conf.code),
    }
