"""The one way evalution makes an HTTP client. A proxy set in the environment is honoured for the
outside world and never for this machine: a corporate proxy can't reach an app on localhost, and
shouldn't see its traffic.

It also says, in words, what about an address gets in the way: :func:`notes` before anything is
tried, :func:`explain` once a call failed."""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies_environment, proxy_bypass_environment

import httpx

#: ``None``: no proxy for these, whatever the environment says.
LOCAL: dict[str, None] = {
    "all://127.0.0.1": None,
    "all://localhost": None,
    "all://*.localhost": None,
    "all://[::1]": None,
    "all://host.docker.internal": None,
}
_PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
_NOT_AN_ADDRESS = ("0.0.0.0", "::")  # noqa: S104 - named so they can be refused, never bound
_ADDRESS_IN_TEXT = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+", re.IGNORECASE)


def client(**options: Any) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` that reaches this machine directly. A given ``transport`` already
    takes no proxy from the environment."""
    if options.get("transport") is None:
        options["mounts"] = {**LOCAL, **(options.get("mounts") or {})}
    return httpx.AsyncClient(**options)


def proxies_set() -> list[str]:
    """The proxy variables set in the environment, however they are cased."""
    return [n for n in _PROXIES if os.environ.get(n) or os.environ.get(n.lower())]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _where(url: str) -> str:
    """Host and port, never a password written into the URL."""
    parts = urlsplit(url)
    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""
    return f"{parts.hostname or url.rpartition('@')[2]}{port}"


def _ip(host: str, kind: str) -> bool:
    try:
        return bool(getattr(ipaddress.ip_address(host), kind))
    except ValueError:
        return False


def _loopback(host: str) -> bool:
    return host == "localhost" or host.endswith(".localhost") or _ip(host, "is_loopback")


def _proxy_for(url: str) -> str | None:
    """The variable whose proxy a call to *url* goes through, ``None`` when it goes direct."""
    host, scheme = _host(url), urlsplit(url).scheme
    if not host or _loopback(host) or host == "host.docker.internal":
        return None
    if proxy_bypass_environment(host):
        return None
    found = getproxies_environment()
    if found.get(scheme):
        return f"{scheme.upper()}_PROXY"
    return "ALL_PROXY" if found.get("all") else None


def in_container() -> bool:
    return Path("/.dockerenv").exists()


def notes(url: str) -> list[str]:
    """What about *url* won't work from here, with what to do instead; empty when nothing shows."""
    host, where = _host(url), _where(url)
    if host in _NOT_AN_ADDRESS:
        return [f"{host} is where a server listens, not an address to call. Use 127.0.0.1."]
    if _loopback(host) and in_container():
        return [
            f"evalution runs in a container, where {where} is the container itself. Use "
            "host.docker.internal to reach this computer."
        ]
    proxy = _proxy_for(url)
    private = _ip(host, "is_private") or host.endswith(".local") or (host and "." not in host)
    if proxy and private:
        return [
            f"{host} looks like a local address, but {proxy} sends it through the proxy, which "
            f"can't reach it. Add {host} to NO_PROXY."
        ]
    return []


def _bare(found: re.Match[str]) -> str:
    try:
        parts = urlsplit(found[0])
        return f"{parts.scheme}://{_where(found[0])}{parts.path}"
    except ValueError:
        return "(an address)"


def no_login(text: str) -> str:
    """*text* with each address in it cut to its scheme, host, port and path: what's kept or passed
    on holds no login or query, which can both be keys."""
    return _ADDRESS_IN_TEXT.sub(_bare, text)


def said(exc: Exception) -> str:
    """*exc* in words fit to keep in a run file and show a model: the status and the path of a call
    that answered, why one that didn't, never the URL as it was called."""
    kind = type(exc).__name__
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{kind}: {exc.response.status_code} from {exc.request.url.path}"
    if isinstance(exc, httpx.HTTPError):
        try:
            return f"{kind}: {explain(str(exc.request.url), exc)}"
        except RuntimeError:  # an error raised outside a request
            return kind
    return f"{kind}: {no_login(str(exc))}"


def explain(url: str, exc: Exception) -> str:
    """Why a call to *url* failed, and what to do about it. Never the error's own text, which may
    quote what was sent."""
    if said := notes(url):
        return said[0]
    where = _where(url)
    proxy = _proxy_for(url)
    if isinstance(exc, httpx.ProxyError):
        return (
            f"The proxy in {proxy or 'your environment'} couldn't reach {where}. Check the proxy, "
            f"or add {_host(url)} to NO_PROXY."
        )
    if isinstance(exc, httpx.TimeoutException) and not isinstance(exc, httpx.ConnectTimeout):
        return f"{where} took too long to answer."
    if _loopback(_host(url)):
        return f"Nothing is answering at {where}. Start it, or check the port."
    if proxy:
        return f"Couldn't reach {where} through the proxy in {proxy}. Check the proxy."
    return f"Couldn't reach {where}. Check the address and your network."
