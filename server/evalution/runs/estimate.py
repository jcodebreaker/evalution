"""What a run is likely to take, before anyone says yes to it: the messages a try sends, a time
limit for one, and how its tests will play, in the Run dialog's words."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.case import Case
from evalution.runs.page.map import PageMap, proof_words

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


def try_seconds(case: Case, runs: Sequence[Mapping[str, Any]]) -> float:
    """Roughly how long one try of *case* takes: the median of its own newest tries in *runs*
    (newest first), else its messages at what a message took there, else at
    :data:`DEFAULT_MESSAGE_SECONDS` each."""
    own: list[float] = []
    for run in runs:
        entry = (run.get("cases") or {}).get(case.case) or {}
        own += [float(t["seconds"]) for t in entry.get("tries") or [] if t.get("seconds")]
        if len(own) >= RECENT_LATENCIES:
            break
    if own:
        return statistics.median(own[:RECENT_LATENCIES])
    seen = _message_seconds(runs)
    each = statistics.median(seen) if seen else DEFAULT_MESSAGE_SECONDS
    return max(1, messages(case)) * each


def about(seconds: float) -> str:
    """A rough time: "about 40 s", "about 3 min"."""
    if seconds < 90:
        return f"about {max(5, round(seconds / 5) * 5)} s"
    return f"about {round(seconds / 60)} min"


def plays(
    app: str,
    cases: Sequence[Case],
    runs: Sequence[Mapping[str, Any]],
    page: PageMap | None,
    *,
    only: bool = False,
) -> list[dict[str, Any]]:
    """How *cases* will play on *app*, as the Run dialog says it: a line per way of playing and
    what a try keeps, with its tests and roughly how long a try takes. Through the page (*page*;
    *only* when there is no API under it) tests play one at a time, and each keeps the map's
    proof with its own over it; through the API there is nothing to capture."""
    groups: dict[str, list[Case]] = {}
    for case in cases:
        kept = proof_words(page.proof.over(case.page_proof)) if page is not None else ""
        groups.setdefault(kept, []).append(case)
    found = []
    for kept, chosen in groups.items():
        seconds = statistics.mean(try_seconds(case, runs) for case in chosen)
        tests = "1 test" if len(chosen) == 1 else f"{len(chosen)} tests"
        if page is None:
            how, text = "api", f"{tests} through {app}'s API, {about(seconds)} a try."
        else:
            how, way = ("page_only", "page only") if only else ("page", "page")
            text = f"{tests} through {app}'s {way}, one at a time, {about(seconds)} a try; {kept}."
        found.append(
            {
                "how": how,
                "cases": [case.case for case in chosen],
                # A try under half a second still took time: 0 would read as none known.
                "seconds": max(1, round(seconds)),
                "kept": kept or None,
                "text": text,
            }
        )
    return found
