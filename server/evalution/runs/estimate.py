"""What a run is likely to take, before anyone says yes to it: the messages a try sends, and a
time limit for one."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.case import Case

#: Seconds a message is assumed to take with no earlier try to go by.
DEFAULT_MESSAGE_SECONDS = 30
#: Room left in a time limit over what a try is expected to take.
LIMIT_MARGIN = 2
MIN_LIMIT_SECONDS, MAX_LIMIT_SECONDS = 60, 14_400
#: Recent tries the seconds per message go by.
RECENT_LATENCIES = 20


def messages(case: Case) -> int:
    """The messages one try of *case* sends: its message turns and its generated ones."""
    return sum(t.filler or (1 if t.is_message else 0) for t in case.turns)


def _message_seconds(runs: Sequence[Mapping[str, Any]]) -> list[float]:
    """Seconds per message of the newest tries in *runs* (newest first). A filler turn sends more
    than its file says, so a test with one says nothing here."""
    found: list[float] = []
    for run in runs:
        for entry in (run.get("cases") or {}).values():
            planned = (entry.get("case") or {}).get("turns") or []
            sent = len(entry.get("turns") or [])
            if not sent or any(isinstance(t, dict) and "filler" in t for t in planned):
                continue
            found += [t["seconds"] / sent for t in entry.get("tries") or [] if t.get("seconds")]
        if len(found) >= RECENT_LATENCIES:
            break
    return found[:RECENT_LATENCIES]


def suggest_seconds(case: Case, runs: Sequence[Mapping[str, Any]]) -> tuple[int, str]:
    """A time limit for one try of *case*, and where it came from: its messages, times what a
    message took in *runs* (newest first), else a stated default, with room to spare."""
    count = max(1, messages(case))
    seen = _message_seconds(runs)
    each = statistics.median(seen) if seen else DEFAULT_MESSAGE_SECONDS
    wanted = math.ceil(count * each * LIMIT_MARGIN / 30) * 30
    seconds = min(MAX_LIMIT_SECONDS, max(MIN_LIMIT_SECONDS, wanted))
    many = "message" if count == 1 else "messages"
    if seen:
        said = f"{count} {many} at about {each:.0f} s each, from {len(seen)} earlier tries, doubled"
    else:
        said = f"{count} {many} at an assumed {each} s each (no runs yet), doubled"
    return seconds, said
