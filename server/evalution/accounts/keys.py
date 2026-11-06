"""Whose model key a workspace's model calls use.

STUB: the identity work replaces this body; the signature is the contract. Until then no one has
a key, so a hosted model call fails with the usual "no model key" error. Local never asks: its key
is the environment's, or the one set from the page.
"""

from __future__ import annotations

from evalution.storage import Database


def model_key_for(database: Database, workspace_id: str, user_id: str | None) -> str | None:
    """The key of *user_id*, else of the workspace's earliest member; ``None`` when none has one."""
    return None
