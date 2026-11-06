"""Each signed-in person's own model key, and whose key a workspace's model calls use.

A key is kept encrypted among the person's own secrets (``user:<id>``, never a workspace's), can
be replaced or deleted, and only its last four characters are ever shown. Locally there are no
people: the model key is the one the settings form or the environment gives, as before.
"""

from __future__ import annotations

from evalution.accounts import people
from evalution.storage.database import LOCAL, Database
from evalution.storage.secrets import SecretKeyError, Secrets

#: The secret's name, as a workspace's own (:data:`evalution.config.MODEL_SECRET`).
NAME = "model/api_key"


def _secrets(database: Database, user_id: str) -> Secrets:
    """*user_id*'s secrets. Hosted only, where ``EVALUTION_SECRET_KEY`` is required."""
    return Secrets(database.docs(f"user:{user_id}"), None)


def own_key(database: Database, user_id: str) -> str | None:
    """:raises SecretKeyError: Stored under another key."""
    return (_secrets(database, user_id).get(NAME) or "").strip() or None


def set_key(database: Database, user_id: str, key: str) -> str:
    """Keep *key* as *user_id*'s own; its last four characters."""
    key = key.strip()
    _secrets(database, user_id).set(NAME, key)
    return last4(key)


def delete_key(database: Database, user_id: str) -> None:
    _secrets(database, user_id).set(NAME, None)


def last4(key: str | None) -> str | None:
    """What may be shown of *key*: its last four characters, and nothing of a key too short to
    keep the rest hidden."""
    return key[-4:] if key and len(key) >= 8 else None


def model_key_for(database: Database, workspace_id: str, user_id: str | None) -> str | None:
    """The key a model call in *workspace_id* uses: the key of *user_id*, who the work is for,
    else that of the earliest member who has one (for work no one started, like the daily drift
    check), else none. ``None`` locally, where the workspace's own key stays in charge."""
    if workspace_id == LOCAL:
        return None
    # Members only: someone's key is never spent in a workspace they aren't in.
    candidates = [m.id for m in people.members_of(database, workspace_id)]
    if user_id in candidates:
        candidates.remove(user_id)
        candidates.insert(0, user_id)
    for candidate in candidates:
        try:
            if key := own_key(database, candidate):
                return key
        except SecretKeyError:  # the server's key changed: this one can't be read, try the next
            continue
    return None
