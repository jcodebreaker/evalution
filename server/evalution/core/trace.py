"""What a try did, in the words the traces, the adapters and the grades share."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The actor of the assistant's own work, and of a delegated agent's with no name.
MAIN, HELPER = "main", "helper"


@dataclass(frozen=True)
class Call:
    """One tool call of a try's trajectory.

    :param id: Its call id (also its ``TimelineSpan`` id).
    :param start: Seconds from the try's first message.
    :param end: Seconds from the first message when its result came back, or ``None``.
    :param helper: The delegated agent (its span id) that made it, or ``None`` for the main one.
    :param agent: That delegated agent's name, when its span gives one.
    """

    id: str
    name: str
    args: Mapping[str, Any]
    start: float
    end: float | None = None
    helper: str | None = None
    agent: str | None = None
    #: The MCP server the tool is on, when its name says (``mcp__crm__search`` -> ``crm``).
    server: str | None = None

    @property
    def actor(self) -> str:
        """Who made it: ``main``, else the delegated agent's name (``helper`` when it has none)."""
        if self.helper is None:
            return MAIN
        return self.agent or HELPER


@dataclass
class Section:
    name: str
    text: str
    tokens: int | None = None


def tool_name(raw: str) -> str:
    """A tool's name without its MCP server prefix (``mcp__server__search`` -> ``search``)."""
    return str(raw or "").rsplit("__", 1)[-1]


def tool_server(raw: str) -> str | None:
    """A tool's MCP server, when its name carries one (``mcp__crm__search`` and ``crm__search``
    -> ``crm``), else ``None``."""
    parts = str(raw or "").split("__")
    if len(parts) < 2:
        return None
    server = parts[-2]
    return None if not server or (server == "mcp" and len(parts) == 2) else server
