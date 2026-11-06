"""Whether a try starts already holding what it is to show (README, "Known limits").

A stateful app whose reset leaks keeps what an earlier try left, and a test that the agent
remembers something then passes on the leftover, not on what this try did. So before a try's first
message, an adapter that declares ``probe`` reads the case's own state checks as the session stands,
and its own answer to each says whether it holds: the same reading the check gets after the turns.

Only a check that something is there (a ``*_contains`` key) is read. A clean start holds nothing,
so one that already holds can only be left over, and the try is not judged. A check that something
is missing (``*_lacks``) holds at any clean start, and a leftover can only make it fail, never pass,
so it says nothing here. ``context_*`` checks read the try's own model calls, which can't be left
over.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from evalution.adapters.base import Session, Target
from evalution.core.case import CONTEXT_CHECKS, Case
from evalution.core.rules import LEFTOVER_STATE

_logger = logging.getLogger(__name__)

#: Seconds the probe may take: it reads, and doesn't wait for anything to come true.
PROBE_SECONDS = 30.0


def presence_checks(case: Case, answered: frozenset[str]) -> list[tuple[str, Any]]:
    """*case*'s state checks that something is there, among the keys *answered*, each once and in
    the order the case makes them."""
    found: list[tuple[str, Any]] = []
    for turn in case.turns:
        for key, value in (turn.state or {}).items():
            there = key.endswith("_contains") and key not in CONTEXT_CHECKS and key in answered
            if there and (key, value) not in found:
                found.append((key, value))
    return found


async def leftover(target: Target, session: Session, case: Case) -> str | None:
    """Why a try of *case* can't be judged from *session* as it stands, before its first message: a
    check that something is there already holds. ``None`` when none does, and when the adapter has
    no ``probe``. A probe that fails, in any way, is logged and taken as finding nothing: the try
    plays as it would have without one."""
    declared = target.capabilities
    if not declared.probe:
        return None
    for key, value in presence_checks(case, declared.state_checks):
        try:
            found = await asyncio.wait_for(target.probe(session, {key: value}), PROBE_SECONDS)
        except Exception:  # someone's own adapter can fail in any way at all
            _logger.warning("evals: session %s not probed", session.id, exc_info=True)
            return None
        if found.observed and found.ok:
            said = found.actual or found.reason
            return f"{LEFTOVER_STATE}: {key} {value!r} held before the first message ({said})"
    return None
