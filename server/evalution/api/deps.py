"""What the area routers share: the errors they answer with, who may ask, and the lookups
of a route's target and set."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from evalution.config import ALLOWED_HOSTS_ENV
from evalution.suite.cases import list_sets
from evalution.targets import Targets

LOCAL_NAMES = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
#: Where a server listening on all addresses is always reached from this computer.
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_ALL_ADDRESSES = ("0.0.0.0", "::")  # noqa: S104 - what a bind says, compared and never bound


def invalid(message: str) -> HTTPException:
    return HTTPException(400, message)


def not_found(what: str) -> HTTPException:
    return HTTPException(404, f"{what} not found")


def busy(message: str) -> HTTPException:
    return HTTPException(409, message)


def _hostname(value: str | None) -> str:
    """The host in a ``Host`` or ``Origin`` value, without its scheme and port; empty when it can't
    be read, which is no name anything answers under."""
    try:
        return (urlsplit(f"//{(value or '').split('://')[-1]}").hostname or "").lower()
    except ValueError:
        return ""


def _name(value: str) -> str:
    """A name from ``allowed_hosts`` as :func:`_hostname` reads it from a request."""
    value = value.strip().lower()
    return value if value.count(":") > 1 and "[" not in value else _hostname(value)


def answers_to(bind: str | None, configured: Iterable[str]) -> frozenset[str]:
    """The names a server open to other computers answers under: the *configured* ones and what
    its *bind* implies, the address itself or, on all addresses, this computer's own names."""
    implied = _LOOPBACK if bind in _ALL_ADDRESSES else {bind} if bind else set()
    return frozenset(_name(n) for n in (*implied, *configured)) - {""}


def refusal(request: Request) -> str | None:
    """Why a request may not be answered. There is no login, so being on this computer is not
    enough: a web page open in the owner's browser is on this computer too. A page on another
    site can send a write without asking (its ``Origin`` says so), or reach here under its own
    name to read the answers (the ``Host`` says so): that holds on any bind, so a server answers
    only under its own names and the ones it was given, and takes writes only from pages on them.
    A proxy on this computer may keep the public name or put in its own, and the page it serves
    writes from the public one, which needn't be the name the request came in under."""
    names = request.app.state.allowed_hosts
    if not request.app.state.remote:
        client = request.client.host if request.client else ""
        if client not in LOCAL_NAMES:
            return "evalution only answers on the computer it runs on"
        names = LOCAL_NAMES | names
    if _hostname(request.headers.get("host")) not in names:
        return f"evalution doesn't answer under this name: add it to {ALLOWED_HOSTS_ENV}"
    origin = request.headers.get("origin")
    if (
        request.method not in _SAFE_METHODS
        and origin is not None
        and _hostname(origin) not in names
    ):
        return f"evalution doesn't take requests from other sites: add it to {ALLOWED_HOSTS_ENV}"
    return None


async def guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Every route, the page and the API's own docs behind :func:`refusal`."""
    reason = refusal(request)
    if reason:
        return JSONResponse({"detail": reason}, status_code=403)
    return await call_next(request)


def target_name(targets: Targets, name: Any) -> str:
    if name is None:
        names = targets.names()
        if len(names) != 1:
            raise invalid("target: required")
        return names[0]
    if not isinstance(name, str):
        raise invalid("target: must be a target name")
    try:
        targets.conf(name)
    except LookupError as exc:
        raise not_found("App") from exc
    return name


def known_set(targets: Targets, target: str, set_name: str) -> None:
    """:raises HTTPException: 404, *target* has no such set."""
    if set_name not in list_sets(targets.docs, target):
        raise not_found("Test set")


async def read_body(request: Request) -> dict[str, Any]:
    """The JSON body, read here so a malformed one is never echoed back (it holds
    secrets)."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise invalid("the body must be a JSON object")
    return body
