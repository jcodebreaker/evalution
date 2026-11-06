"""Secrets, encrypted in the database (kind ``secret``): a model key, a target's API key.

The encryption key is ``$EVALUTION_SECRET_KEY`` (a Fernet key, for a hosted server), else
``<data_dir>/secret.key``, made on first use and readable by its owner only. A secret is never
returned whole to the page: :meth:`Secrets.hint` gives its last four characters.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from evalution.storage.database import Docs

KEY_ENV = "EVALUTION_SECRET_KEY"
KEY_FILE = "secret.key"
KIND = "secret"


class SecretKeyError(RuntimeError):
    """The secrets can't be read with this key: it changed, or it isn't a key."""


def _cipher(data_dir: Path) -> Fernet:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        path = data_dir / KEY_FILE
        if not path.is_file():
            data_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(Fernet.generate_key())
        key = path.read_text(encoding="ascii").strip()
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise SecretKeyError(f"{KEY_ENV} or {KEY_FILE} is not a Fernet key") from exc


class Secrets:
    """One workspace's secrets, by name (``model.api_key``, ``targets.<name>.api_key``...)."""

    def __init__(self, docs: Docs, data_dir: Path) -> None:
        self.docs = docs
        self._cipher = _cipher(data_dir)

    def get(self, name: str) -> str | None:
        """:raises SecretKeyError: Stored under another key."""
        token = self.docs.get(KIND, name)
        if not isinstance(token, str):
            return None
        try:
            return self._cipher.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretKeyError(f"secret {name!r} was stored under another key") from exc

    def set(self, name: str, value: str | None) -> None:
        """Store *value*; ``None`` or empty removes it."""
        if not value:
            self.docs.delete(KIND, name)
            return
        self.docs.put(KIND, name, self._cipher.encrypt(value.encode()).decode())

    def names(self, prefix: str = "") -> list[str]:
        return self.docs.keys(KIND, prefix)

    def hint(self, name: str) -> str | None:
        """``…abcd``: enough to tell which key it is."""
        value = self.get(name)
        return f"…{value[-4:]}" if value and len(value) >= 8 else ("…" if value else None)
