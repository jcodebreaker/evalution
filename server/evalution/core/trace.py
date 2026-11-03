"""What a try did, in the words the traces, the adapters and the grades share."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Call:
    """One tool call of a try's trajectory.

    :param id: Its call id (also its ``TimelineSpan`` id).
    :param start: Seconds from the try's first message.
    :param end: Seconds from the first message when its result came back, or ``None``.
    :param helper: The delegated agent (its span id) that made it, or ``None`` for the main one.
    """

    id: str
    name: str
    args: Mapping[str, Any]
    start: float
    end: float | None = None
    helper: str | None = None


@dataclass
class Section:
    name: str
    text: str
    tokens: int | None = None


def tool_name(raw: str) -> str:
    """A tool's name without its MCP server prefix (``mcp__server__search`` -> ``search``)."""
    return str(raw or "").rsplit("__", 1)[-1]
