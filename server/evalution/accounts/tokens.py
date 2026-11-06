"""Workspace API tokens, for the CLI and CI: ``Authorization: Bearer evt_...``.

A token belongs to one workspace and reaches only it. It is shown once, when it is made; after
that only its first characters (:data:`PREFIX_LENGTH`) tell tokens apart.

Its scope is :data:`ALL` (everything a token may do) or :data:`TRACES`: only ``POST /v1/traces``.
A traces token goes into an app's environment, next to everything else the app holds, so a leak
of it sends spans to the workspace and reads nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import delete, insert, or_, select, update

from evalution.accounts.ids import digest, new_id, new_secret
from evalution.storage.database import Database
from evalution.storage.identity import api_tokens

MARK = "evt_"
PREFIX_LENGTH = 12
ALL = "all"
TRACES = "traces"
SCOPES = (ALL, TRACES)
#: How stale ``last_used_at`` may get, so a busy token doesn't write on every request.
TOUCH_EVERY = 60.0


@dataclass(frozen=True)
class Token:
    id: str
    name: str
    prefix: str
    created_at: float
    last_used_at: float | None
    scope: str = ALL


def create(
    database: Database, workspace_id: str, user_id: str, name: str, scope: str = ALL
) -> tuple[Token, str]:
    """A new token and its value, which is never available again.

    :raises ValueError: *scope* isn't one of :data:`SCOPES`.
    """
    if scope not in SCOPES:
        raise ValueError(f"scope: {' or '.join(SCOPES)}")
    value = MARK + new_secret()
    token = Token(new_id(), name, value[:PREFIX_LENGTH], time.time(), None, scope)
    with database.engine.begin() as db:
        db.execute(
            insert(api_tokens).values(
                id=token.id,
                workspace_id=workspace_id,
                name=name,
                token_hash=digest(value),
                prefix=token.prefix,
                created_by=user_id,
                created_at=token.created_at,
                scope=scope,
            )
        )
    return token, value


def listed(database: Database, workspace_id: str) -> list[Token]:
    c = api_tokens.c
    with database.engine.begin() as db:
        rows = db.execute(
            select(c.id, c.name, c.prefix, c.created_at, c.last_used_at, c.scope)
            .where(c.workspace_id == workspace_id)
            .order_by(c.created_at, c.id)
        ).all()
    return [Token(*row) for row in rows]


def remove(database: Database, workspace_id: str, token_id: str) -> bool:
    c = api_tokens.c
    with database.engine.begin() as db:
        gone = db.execute(
            delete(api_tokens).where(c.id == token_id, c.workspace_id == workspace_id)
        )
    return bool(gone.rowcount)


def grant_of(database: Database, value: str) -> tuple[str, str] | None:
    """The workspace token *value* reaches and its scope, ``None`` for no live token; notes that
    it was used."""
    if not value.startswith(MARK):
        return None
    c, now = api_tokens.c, time.time()
    hashed = digest(value)
    with database.engine.begin() as db:
        found = db.execute(select(c.workspace_id, c.scope).where(c.token_hash == hashed)).first()
        if found is not None:
            db.execute(
                update(api_tokens)
                .where(
                    c.token_hash == hashed,
                    or_(c.last_used_at.is_(None), c.last_used_at < now - TOUCH_EVERY),
                )
                .values(last_used_at=now)
            )
    return (found.workspace_id, found.scope) if found else None
