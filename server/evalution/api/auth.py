"""Who is asking, for every request, and the sign-in routes (docs/hosting.md).

Locally nothing changes: no login, the one workspace :data:`LOCAL`, and :func:`deps.guard` keeps
everyone off but this computer. Hosted, a request is signed in by its ``evalution_login`` cookie
(a browser) or an ``Authorization: Bearer evt_...`` token (the CLI, CI), and runs in one workspace:
the token's own, else the ``X-Workspace`` the page names, else the person's personal one.

A browser sends its cookie with whatever any page asks of it, so a write signed in by cookie must
carry ``X-Requested-With: evalution``, which a page on another site can't add without asking first.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import RequestResponseEndpoint

from evalution.accounts import logins, people, tokens
from evalution.accounts.context import acting_user
from evalution.accounts.oidc import Attempt, Issuer, SignInError
from evalution.accounts.settings import AuthSettings
from evalution.api.accounts import accounts_router, me_router
from evalution.storage.database import LOCAL, Database
from evalution.storage.secrets import KEY_ENV, SecretKeyError, cipher

LOGIN_COOKIE = "evalution_login"
#: The sign-in in progress, sealed with the server's secret key, between ``/auth/login`` and the
#: callback: it ties the callback to the browser that started it.
ATTEMPT_COOKIE = "evalution_signin"
ATTEMPT_SECONDS = 600
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "evalution"
#: Answered signed out too: whether the server is up, and who (if anyone) is signed in.
OPEN_PATHS = frozenset({"/v1/health", "/v1/me"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Who:
    user: str | None = None
    workspace: str | None = None
    by_cookie: bool = False
    refused: tuple[int, str] | None = None


def safe_next(value: Any) -> str:
    """Where to land after signing in: a path on this server, never another site."""
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        return "/"
    parts = urlsplit(value)
    return "/" if parts.scheme or parts.netloc else value


def _who(database: Database, request: Request) -> Who:
    """Who *request* is from, and in which workspace. Blocking."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if header:
        if scheme.lower() != "bearer":
            return Who(refused=(401, "sign in"))
        workspace = tokens.workspace_of(database, value.strip())
        return Who(workspace=workspace) if workspace else Who(refused=(401, "sign in"))
    cookie = request.cookies.get(LOGIN_COOKIE)
    user = logins.user_of(database, cookie) if cookie else None
    if user is None:
        return Who()
    wanted = request.headers.get("x-workspace")
    if wanted:
        if not people.is_member(database, wanted, user):
            return Who(refused=(404, "Workspace not found"))
        return Who(user, wanted, by_cookie=True)
    personal = people.personal_workspace(database, user)
    return Who(user, personal.id if personal else None, by_cookie=True)


async def identify(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Settle ``request.state.user`` and ``request.state.workspace`` (and :data:`acting_user`)
    before any route runs; refuse what may not go on."""
    settings: AuthSettings | None = request.app.state.auth
    if settings is None:
        request.state.user, request.state.workspace = None, LOCAL
        return await call_next(request)
    who = await asyncio.to_thread(_who, request.app.state.accounts, request)
    if who.refused:
        return JSONResponse({"detail": who.refused[1]}, status_code=who.refused[0])
    path = request.url.path
    if who.workspace is None and path.startswith("/v1/") and path not in OPEN_PATHS:
        return JSONResponse({"detail": "sign in"}, status_code=401)
    if (
        who.by_cookie
        and request.method not in _SAFE_METHODS
        and request.headers.get(CSRF_HEADER) != CSRF_VALUE
    ):
        return JSONResponse({"detail": f"{CSRF_HEADER}: {CSRF_VALUE} is needed"}, status_code=403)
    request.state.user = who.user
    if who.workspace is not None:
        request.state.workspace = who.workspace
    token = acting_user.set(who.user)
    try:
        return await call_next(request)
    finally:
        acting_user.reset(token)


def _seal(attempt: Attempt) -> str:
    return cipher(None).encrypt(json.dumps(asdict(attempt)).encode()).decode()


def _unseal(value: str | None) -> Attempt | None:
    if not value:
        return None
    try:
        found = json.loads(cipher(None).decrypt(value.encode(), ttl=ATTEMPT_SECONDS))
        return Attempt(**found)
    except (InvalidToken, ValueError, TypeError):
        return None


def auth_router(settings: AuthSettings, database: Database) -> APIRouter:
    """``/auth/login``, ``/auth/callback`` and ``/auth/logout``."""
    router = APIRouter(prefix="/auth")
    secure = settings.public_url.startswith("https://")

    @router.get("/login")
    async def login(request: Request, next: str = "/") -> Response:
        """Off to the issuer; back at ``/auth/callback``."""
        issuer: Issuer = request.app.state.issuer
        try:
            url, attempt = await issuer.authorize(safe_next(next))
        except SignInError as exc:
            print(f"evalution: sign-in failed: {exc}", file=sys.stderr)
            return JSONResponse({"detail": "sign-in isn't available"}, status_code=502)
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(
            ATTEMPT_COOKIE,
            _seal(attempt),
            max_age=ATTEMPT_SECONDS,
            path="/auth",
            httponly=True,
            secure=secure,
            samesite="lax",
        )
        return response

    @router.get("/callback")
    async def callback(request: Request, code: str = "", state: str = "") -> Response:
        """The issuer's answer: signed in (made, the first time), then where they were going."""
        attempt = _unseal(request.cookies.get(ATTEMPT_COOKIE))
        if attempt is None:
            return JSONResponse({"detail": "sign-in expired: try again"}, status_code=400)
        if not code or not state:
            return JSONResponse({"detail": "sign-in failed"}, status_code=400)
        issuer: Issuer = request.app.state.issuer
        try:
            identity = await issuer.finish(code, state, attempt)
        except SignInError as exc:
            print(f"evalution: sign-in failed: {exc}", file=sys.stderr)
            return JSONResponse({"detail": "sign-in failed"}, status_code=400)
        user = await asyncio.to_thread(
            people.sign_in,
            database,
            identity.issuer,
            identity.subject,
            identity.email,
            identity.name,
        )
        value = await asyncio.to_thread(logins.start, database, user.id)
        response = RedirectResponse(safe_next(attempt.next), status_code=302)
        response.delete_cookie(ATTEMPT_COOKIE, path="/auth", secure=secure, httponly=True)
        response.set_cookie(
            LOGIN_COOKIE,
            value,
            max_age=logins.LIFETIME,
            path="/",
            httponly=True,
            secure=secure,
            samesite="lax",
        )
        return response

    @router.post("/logout", status_code=204)
    async def logout(request: Request) -> Response:
        if value := request.cookies.get(LOGIN_COOKIE):
            await asyncio.to_thread(logins.end, database, value)
        response = Response(status_code=204)
        response.delete_cookie(LOGIN_COOKIE, path="/", secure=secure, httponly=True)
        return response

    return router


def hosted_settings() -> AuthSettings | None:
    """The sign-in settings, ``None`` locally.

    :raises AuthSettingsError: Hosted, and a sign-in setting is missing.
    :raises RuntimeError: Hosted, and ``EVALUTION_SECRET_KEY`` is missing or not a key.
    """
    settings = AuthSettings.from_env()
    if settings is None:
        return None
    try:
        cipher(None)
    except SecretKeyError as exc:
        raise RuntimeError(f"a hosted evalution needs {KEY_ENV}, a Fernet key: {exc}") from exc
    return settings


def mount_accounts(app: FastAPI, database: Database) -> None:
    """Who is asking for every request, ``/v1/me``, and hosted, sign-in and the account routes.
    Mount before :func:`deps.guard`, so that the guard runs first.

    :raises AuthSettingsError | RuntimeError: Hosted, half set up (:func:`hosted_settings`).
    """
    settings = hosted_settings()
    app.state.auth = settings
    app.state.accounts = database
    app.middleware("http")(identify)
    app.include_router(me_router())
    if settings is None:
        return
    app.state.issuer = Issuer(settings)
    if host := urlsplit(settings.public_url).hostname:
        app.state.allowed_hosts = app.state.allowed_hosts | {host.lower()}
    app.include_router(auth_router(settings, database))
    app.include_router(accounts_router(settings))
