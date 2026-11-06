"""Random ids and the hashes secrets are kept as.

An id names a row and may be shown; a secret (a token, an invite code, a login's cookie value)
is shown once to its holder and kept only as :func:`digest`, so the database can't be used to
sign in.
"""

from __future__ import annotations

import hashlib
import secrets


def new_id() -> str:
    """A url-safe id no one can guess, with no ``:`` (``user:<id>`` names a person's secrets)."""
    return secrets.token_urlsafe(16)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    """How a secret is kept: SHA-256, hex. The secrets are long and random, so no salt is needed
    and the hash can be looked up directly."""
    return hashlib.sha256(value.encode()).hexdigest()
