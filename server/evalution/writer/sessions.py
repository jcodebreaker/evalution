"""Where the test writer keeps its sessions: one document of kind :data:`KIND` each, keyed by the
session's id."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from evalution.storage import Docs

KIND = "session"
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SessionStore:
    """The Ask AI sessions of one workspace."""

    def __init__(self, docs: Docs) -> None:
        self.docs = docs

    def write(self, session: Mapping[str, Any]) -> None:
        """:raises ValueError: Not a session id."""
        if not _SESSION_ID.match(str(session["id"])):
            raise ValueError(f"not a session id: {session['id']}")
        self.docs.put(KIND, str(session["id"]), dict(session))

    def chats(self, target: str) -> list[dict[str, Any]]:
        """Every session of *target* with something said in it, latest first, for going back to."""
        found = []
        for _key, session, at in self.docs.items(KIND):
            if session.get("target") != target:
                continue
            asked = next(
                (m.get("text") for m in session.get("messages") or [] if m.get("role") == "admin"),
                None,
            )
            if asked:
                found.append({"id": session["id"], "asked": asked, "at": at})
        return sorted(found, key=lambda chat: chat["at"], reverse=True)

    def usage(self, target: str) -> list[dict[str, Any]]:
        """What each turn kept on *target*'s sessions took (:mod:`evalution.writer.usage`),
        oldest first."""
        turns = [
            turn
            for _key, session, _at in self.docs.items(KIND)
            if session.get("target") == target
            for turn in session.get("usage") or []
            if isinstance(turn, dict)
        ]
        return sorted(turns, key=lambda turn: float(turn.get("at") or 0))

    def load(self, session: str) -> dict[str, Any] | None:
        found = self.docs.get(KIND, session) if _SESSION_ID.match(session) else None
        return found if isinstance(found, dict) else None
