"""Invite links: a code anyone holding it may use, as often as they like, for seven days.

Only the code's hash is kept, so the link can be shown once, when it is made, and never again.
"""

from __future__ import annotations

import time

from sqlalchemy import insert, select

from evalution.accounts import people
from evalution.accounts.ids import digest, new_secret
from evalution.storage.database import Database
from evalution.storage.identity import invites

LIFETIME = 7 * 24 * 3600


def create(database: Database, workspace_id: str, user_id: str) -> tuple[str, float]:
    """A new code for *workspace_id* and when it expires.

    :raises people.AccountError: A personal workspace, which is no one else's.
    """
    found = people.workspace(database, workspace_id)
    if found is None or found.personal:
        raise people.AccountError("a personal workspace can't be shared")
    code, expires_at = new_secret(), time.time() + LIFETIME
    with database.engine.begin() as db:
        db.execute(
            insert(invites).values(
                code_hash=digest(code),
                workspace_id=workspace_id,
                created_by=user_id,
                expires_at=expires_at,
            )
        )
    return code, expires_at


def accept(database: Database, code: str, user_id: str) -> people.Workspace | None:
    """Join the workspace *code* invites to; ``None`` when there is no such live invite."""
    c = invites.c
    with database.engine.begin() as db:
        workspace_id = db.scalar(
            select(c.workspace_id).where(c.code_hash == digest(code), c.expires_at > time.time())
        )
    if workspace_id is None:
        return None
    people.join(database, workspace_id, user_id)
    return people.workspace(database, workspace_id)
