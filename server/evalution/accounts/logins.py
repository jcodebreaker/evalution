"""Signed-in browsers: a login is a row here, and the browser's cookie holds a random value whose
hash names it. Signing out deletes the row, so a copied cookie stops working at once."""

from __future__ import annotations

import time

from sqlalchemy import delete, insert, select

from evalution.accounts.ids import digest, new_secret
from evalution.storage.database import Database
from evalution.storage.identity import logins

LIFETIME = 30 * 24 * 3600


def start(database: Database, user_id: str) -> str:
    """A new login for *user_id*: the cookie's value."""
    value, now = new_secret(), time.time()
    with database.engine.begin() as db:
        db.execute(
            insert(logins).values(
                id_hash=digest(value), user_id=user_id, created_at=now, expires_at=now + LIFETIME
            )
        )
    return value


def user_of(database: Database, value: str) -> str | None:
    """Who the cookie *value* signs in, ``None`` when it is no live login."""
    c = logins.c
    with database.engine.begin() as db:
        return db.scalar(
            select(c.user_id).where(c.id_hash == digest(value), c.expires_at > time.time())
        )


def end(database: Database, value: str) -> None:
    """Sign out, and drop every expired login while here."""
    c = logins.c
    with database.engine.begin() as db:
        db.execute(
            delete(logins).where((c.id_hash == digest(value)) | (c.expires_at <= time.time()))
        )
