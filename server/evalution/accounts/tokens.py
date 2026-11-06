"""Workspace API tokens, for the CLI and CI: ``Authorization: Bearer evt_...``.

A token belongs to one workspace and reaches only it. It is shown once, when it is made; after
that only its first characters (:data:`PREFIX_LENGTH`) tell tokens apart.
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
#: How stale ``last_used_at`` may get, so a busy token doesn't write on every request.
TOUCH_EVERY = 60.0


@dataclass(frozen=True)
class Token:
    id: str
    name: str
    prefix: str
    created_at: float
    last_used_at: float | None


def create(database: Database, workspace_id: str, user_id: str, name: str) -> tuple[Token, str]:
    """A new token and its value, which is never available again."""
    value = MARK + new_secret()
    token = Token(new_id(), name, value[:PREFIX_LENGTH], time.time(), None)
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
            )
        )
    return token, value


def listed(database: Database, workspace_id: str) -> list[Token]:
    c = api_tokens.c
    with database.engine.begin() as db:
        rows = db.execute(
            select(c.id, c.name, c.prefix, c.created_at, c.last_used_at)
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


def workspace_of(database: Database, value: str) -> str | None:
    """The workspace token *value* reaches, ``None`` for no live token; notes that it was used."""
    if not value.startswith(MARK):
        return None
    c, now = api_tokens.c, time.time()
    hashed = digest(value)
    with database.engine.begin() as db:
        workspace_id = db.scalar(select(c.workspace_id).where(c.token_hash == hashed))
        if workspace_id is not None:
            db.execute(
                update(api_tokens)
                .where(
                    c.token_hash == hashed,
                    or_(c.last_used_at.is_(None), c.last_used_at < now - TOUCH_EVERY),
                )
                .values(last_used_at=now)
            )
    return workspace_id
