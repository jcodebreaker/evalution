"""What a run is likely to cost, before anyone says yes to it, and how much it may spend before it
is stopped."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.case import Case

#: The cost of one try when the case has no earlier try to go by.
DEFAULT_TRY_USD = 0.40
#: Recent tries of a case its estimate averages.
RECENT_TRIES = 5
#: A filler turn of n messages grows the conversation as it goes: each message is a full turn
#: on a longer chat, so the turn costs about ``c0 * n * (1 + n / FILLER_GROWTH)``.
FILLER_GROWTH = 60
#: Analysis in the estimate: per failed try (:data:`FAIL_SHARE` of the tries), and per run.
ANALYSIS_TRY_USD = 0.03
ANALYSIS_RUN_USD = 0.05
FAIL_SHARE = 0.3
#: One try graded again from its evidence.
GRADE_TRY_USD = 0.03
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


def filler_usd(case: Case, c0: float) -> float:
    """What the case's filler turns add to a try: ``c0 * n * (1 + n / 60)`` per turn of n."""
    return sum(c0 * t.filler * (1 + t.filler / FILLER_GROWTH) for t in case.turns if t.filler)


def per_try_usd(case: Case, runs: Sequence[Mapping[str, Any]], c0: float) -> float:
    """Mean cost of one try of *case* over its :data:`RECENT_TRIES` newest tries (*runs* newest
    first; they already count its fillers). Never run: :data:`DEFAULT_TRY_USD` plus
    :func:`filler_usd`; another test's cost says nothing about it."""
    costs = own_costs(case, runs)
    if costs:
        return statistics.fmean(costs)
    return DEFAULT_TRY_USD + filler_usd(case, c0)


def run_usd(
    cases: Sequence[Case],
    counts: Mapping[str, int],
    runs: Sequence[Mapping[str, Any]],
    c0: float,
) -> float:
    """What *cases* cost at *counts* tries each, with the analysis of the share that fails."""
    tries = sum(counts.values())
    usd = sum(counts[c.case] * per_try_usd(c, runs, c0) for c in cases)
    return usd + ANALYSIS_TRY_USD * FAIL_SHARE * tries + ANALYSIS_RUN_USD


def spend_cap(estimate: float, multiple: float) -> float | None:
    """What a run estimated at *estimate*, at most, may spend: *multiple* times that. Its high end,
    so playing more tries never trips it; only tries far dearer than they used to be do. A
    *multiple* of 0: no cap."""
    return estimate * multiple if multiple > 0 else None


def capped(spent: float, cap: float | None) -> bool:
    """Whether a run that has *spent* this much has reached its *cap*."""
    return cap is not None and spent >= cap


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


def own_costs(case: Case, runs: Sequence[Mapping[str, Any]]) -> list[float]:
    return [
        t["usd"]
        for run in runs
        for t in reversed((run["cases"].get(case.case) or {}).get("tries") or [])
        if t.get("usd")
    ][:RECENT_TRIES]
