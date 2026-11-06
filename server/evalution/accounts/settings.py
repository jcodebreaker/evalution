"""How a hosted evalution signs people in, read from the environment once at start.

No ``EVALUTION_OIDC_ISSUER``: local, no login, one workspace. With it, hosted: every request is
signed in (OIDC) or carries a workspace API token, and what needs this computer's folders (finding
apps on disk, git hooks, reading an app's code) is off.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ISSUER_ENV = "EVALUTION_OIDC_ISSUER"
CLIENT_ID_ENV = "EVALUTION_OIDC_CLIENT_ID"
CLIENT_SECRET_ENV = "EVALUTION_OIDC_CLIENT_SECRET"  # noqa: S105 - the variable's name, not a secret
PUBLIC_URL_ENV = "EVALUTION_PUBLIC_URL"


class AuthSettingsError(ValueError):
    """Hosted, but a setting it needs is missing."""


@dataclass(frozen=True)
class AuthSettings:
    issuer: str
    client_id: str
    client_secret: str
    #: Where people reach this server, e.g. ``https://evals.example.com``: the sign-in callback.
    public_url: str

    @classmethod
    def from_env(cls) -> AuthSettings | None:
        """The settings, or ``None`` when local. :raises AuthSettingsError: hosted, half set."""
        issuer = os.environ.get(ISSUER_ENV, "").strip()
        if not issuer:
            return None
        values = {
            name: os.environ.get(name, "").strip()
            for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV, PUBLIC_URL_ENV)
        }
        if missing := [name for name, value in values.items() if not value]:
            raise AuthSettingsError(f"{ISSUER_ENV} is set, so these are needed too: {missing}")
        return cls(
            issuer=issuer.rstrip("/"),
            client_id=values[CLIENT_ID_ENV],
            client_secret=values[CLIENT_SECRET_ENV],
            public_url=values[PUBLIC_URL_ENV].rstrip("/"),
        )
