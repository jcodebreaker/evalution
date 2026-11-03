"""Where the test writer keeps its sessions: JSON files next to the runs."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SessionFiles:
    """Evals agent sessions as JSON files, ``<dir>/agent/<session>.json``, next to the runs."""

    def __init__(self, root: Path) -> None:
        self.dir = root / "agent"

    def _path(self, session: str) -> Path | None:
        return self.dir / f"{session}.json" if _SESSION_ID.match(session) else None

    def write(self, session: Mapping[str, Any]) -> None:
        path = self._path(str(session["id"]))
        if path is None:
            raise ValueError(f"not a session id: {session['id']}")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def load(self, session: str) -> dict[str, Any] | None:
        path = self._path(session)
        if path is None or not path.is_file():
            return None
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _logger.warning("evals: session file %s unreadable", path, exc_info=True)
            return None
