"""The signed-in person's own routes: who they are, their workspaces and the people in them,
invites, the workspace's API tokens and their own model key (docs/hosting.md).

All of it but ``/v1/me`` is hosted only, and only for a person: an API token can't make tokens,
invite or spend anyone's key, so a leaked one can't dig itself in deeper.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from evalution.accounts import invites, keys, people, tokens
from evalution.accounts.settings import AuthSettings
from evalution.api.deps import invalid, not_found, read_body
from evalution.config import MODEL_SECRET, Config
from evalution.storage.database import LOCAL, Database
from evalution.storage.secrets import SecretKeyError

LOCAL_WORKSPACE = {"id": LOCAL, "name": "Local", "personal": True}


def _workspace(found: people.Workspace) -> dict[str, Any]:
    return {"id": found.id, "name": found.name, "personal": found.personal}


def _no_key() -> dict[str, Any]:
    return {"set": False, "last4": None}


def _local_key(config: Config) -> dict[str, Any]:
    """This server's own model key, as the settings form knows it."""
    key = config.model.api_key() if config.model else config.keys.stored(MODEL_SECRET)
    return {"set": bool(key), "last4": keys.last4(key)}


def _own_key(database: Database, user_id: str) -> dict[str, Any]:
    try:
        key = keys.own_key(database, user_id)
    except SecretKeyError:
        return _no_key()
    return {"set": bool(key), "last4": keys.last4(key)}


def me_router() -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.get("/me")
    async def get_me(request: Request) -> dict[str, Any]:
        """Who is signed in, where, and whether their model key is set; all empty signed out."""
        if request.app.state.auth is None:
            config = request.app.state.runs.targets.config
            model_key = await asyncio.to_thread(_local_key, config)
            return {
                "hosted": False,
                "user": None,
                "workspace": LOCAL_WORKSPACE,
                "workspaces": [LOCAL_WORKSPACE],
                "model_key": model_key,
            }
        database: Database = request.app.state.accounts
        return await asyncio.to_thread(_me, database, request.state.user, _current(request))

    return router


def _current(request: Request) -> str | None:
    return getattr(request.state, "workspace", None)


def _me(database: Database, user_id: str | None, workspace_id: str | None) -> dict[str, Any]:
    found = people.workspace(database, workspace_id) if workspace_id else None
    workspace = _workspace(found) if found else None
    user = people.user(database, user_id) if user_id else None
    return {
        "hosted": True,
        "user": {"id": user.id, "email": user.email, "name": user.name} if user else None,
        "workspace": workspace,
        "workspaces": (
            [_workspace(w) for w in people.workspaces_of(database, user.id)]
            if user
            else [workspace]
            if workspace
            else []
        ),
        "model_key": _own_key(database, user.id) if user else _no_key(),
    }


def _person(request: Request) -> str:
    """The signed-in person asking. :raises HTTPException: 403, a token is asking."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(403, "sign in to do this")
    return user


def _name(body: dict[str, Any]) -> str:
    try:
        return people.clean_name(body.get("name"))
    except people.AccountError as exc:
        raise invalid(str(exc)) from exc


def accounts_router(settings: AuthSettings) -> APIRouter:
    router = APIRouter(prefix="/v1")

    def member_of(request: Request, workspace_id: str) -> tuple[Database, str]:
        """The database and the person asking, a member of *workspace_id*.

        :raises HTTPException: 404, they aren't (whether it exists is no one else's business).
        """
        user = _person(request)
        database: Database = request.app.state.accounts
        if not people.is_member(database, workspace_id, user):
            raise not_found("Workspace")
        return database, user

    @router.post("/workspaces")
    async def create_workspace(request: Request) -> dict[str, Any]:
        user = _person(request)
        name = _name(await read_body(request))
        made = await asyncio.to_thread(
            people.create_workspace, request.app.state.accounts, user, name
        )
        return _workspace(made)

    @router.patch("/workspaces/{workspace_id}")
    async def rename_workspace(request: Request, workspace_id: str) -> dict[str, Any]:
        name = _name(await read_body(request))
        database, _user = await asyncio.to_thread(member_of, request, workspace_id)
        return _workspace(await asyncio.to_thread(people.rename, database, workspace_id, name))

    @router.get("/workspaces/{workspace_id}/members")
    async def list_members(request: Request, workspace_id: str) -> list[dict[str, Any]]:
        database, _user = await asyncio.to_thread(member_of, request, workspace_id)
        found = await asyncio.to_thread(people.members_of, database, workspace_id)
        return [
            {"id": m.id, "email": m.email, "name": m.name, "joined_at": m.joined_at} for m in found
        ]

    @router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
    async def remove_member(request: Request, workspace_id: str, user_id: str) -> Response:
        """Any member may remove any member; removing yourself is leaving."""
        database, _user = await asyncio.to_thread(member_of, request, workspace_id)
        try:
            await asyncio.to_thread(people.remove, database, workspace_id, user_id)
        except LookupError as exc:
            raise not_found("Member") from exc
        except people.AccountError as exc:
            raise invalid(str(exc)) from exc
        return Response(status_code=204)

    @router.post("/workspaces/{workspace_id}/invites")
    async def invite(request: Request, workspace_id: str) -> dict[str, Any]:
        """A link that lets whoever opens it join, for seven days."""
        database, user = await asyncio.to_thread(member_of, request, workspace_id)
        try:
            code, expires_at = await asyncio.to_thread(invites.create, database, workspace_id, user)
        except people.AccountError as exc:
            raise invalid(str(exc)) from exc
        return {"url": f"{settings.public_url}/invite/{code}", "expires_at": expires_at}

    @router.post("/invites/{code}/accept")
    async def accept(request: Request, code: str) -> dict[str, Any]:
        user = _person(request)
        try:
            joined = await asyncio.to_thread(invites.accept, request.app.state.accounts, code, user)
        except people.AccountError as exc:
            raise invalid(str(exc)) from exc
        if joined is None:
            raise not_found("Invite")
        return {"workspace": _workspace(joined)}

    @router.get("/tokens")
    async def list_tokens(request: Request) -> list[dict[str, Any]]:
        _person(request)
        found = await asyncio.to_thread(
            tokens.listed, request.app.state.accounts, request.state.workspace
        )
        return [
            {
                "id": t.id,
                "name": t.name,
                "prefix": t.prefix,
                "created_at": t.created_at,
                "last_used_at": t.last_used_at,
            }
            for t in found
        ]

    @router.post("/tokens")
    async def create_token(request: Request) -> dict[str, Any]:
        """A token for the workspace, shown this once."""
        user = _person(request)
        name = _name(await read_body(request))
        made, value = await asyncio.to_thread(
            tokens.create, request.app.state.accounts, request.state.workspace, user, name
        )
        return {"id": made.id, "name": made.name, "prefix": made.prefix, "token": value}

    @router.delete("/tokens/{token_id}", status_code=204)
    async def delete_token(request: Request, token_id: str) -> Response:
        _person(request)
        gone = await asyncio.to_thread(
            tokens.remove, request.app.state.accounts, request.state.workspace, token_id
        )
        if not gone:
            raise not_found("Token")
        return Response(status_code=204)

    @router.put("/me/model-key")
    async def put_model_key(request: Request) -> dict[str, Any]:
        """Keep the person's own model key; only its last four characters come back."""
        user = _person(request)
        key = (await read_body(request)).get("key")
        if not isinstance(key, str) or not key.strip():
            raise invalid("key: required")
        last4 = await asyncio.to_thread(keys.set_key, request.app.state.accounts, user, key)
        return {"set": True, "last4": last4}

    @router.delete("/me/model-key", status_code=204)
    async def delete_model_key(request: Request) -> Response:
        user = _person(request)
        await asyncio.to_thread(keys.delete_key, request.app.state.accounts, user)
        return Response(status_code=204)

    return router
