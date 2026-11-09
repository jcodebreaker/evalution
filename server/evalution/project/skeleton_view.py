"""The skeleton as What it learned shows it (ADR 0012): made from the code map, kept under the
code's fingerprint, and joined to what real use touched.

:func:`current` makes it again only when a watched file moved, and then reads again only the files
that moved (:func:`evalution.project.codemap.build` and :func:`.skeleton.skeleton` both go by each
file's fingerprint). :func:`uses` is the part of the trace digest a skeleton is matched to, kept a
few minutes so opening the page doesn't read the traces each time. No model anywhere.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from evalution.code import Code
from evalution.project import codemap
from evalution.project.profile import read_doc, write_doc
from evalution.project.skeleton import Entry, Skeleton, fingerprint_of, match, skeleton
from evalution.targets import Targets
from evalution.traces.profile import digest, recent_sessions

_logger = logging.getLogger(__name__)

DOC = "skeleton.json"
USES_DOC = "skeleton_uses.json"
#: How long what real use touched is kept before the traces are read again; while none was seen,
#: a minute, so an app's first traces show soon.
USES_SECONDS = 15 * 60
NONE_SECONDS = 60
#: Conversations read for it.
SESSIONS = 50
#: The longest a page waits on the traces; past it, what was kept is shown.
TRACE_SECONDS = 8.0


def current(targets: Targets, name: str, code: Code) -> Skeleton:
    """*name*'s skeleton now: the one kept when no watched file moved since, else made again
    from the code map (itself parsed again only where files moved) and kept."""
    prints = code.hashes()
    key = fingerprint_of(prints)
    body = read_doc(targets, name, DOC)
    kept = Skeleton.of_dict(body)
    if kept is not None and isinstance(body, dict) and body.get("for") == key:
        return kept
    made = codemap.build(targets, name, code)
    found = skeleton(made.map, code.read, kept)
    write_doc(targets, name, DOC, {**found.as_dict(), "for": key})
    return found


def _kept_uses(found: dict[str, Any]) -> dict[str, Any]:
    """The part of a digest the skeleton is matched to."""
    return {
        "sessions": found.get("sessions", 0),
        "asks": found.get("asks", 0),
        "tools": {
            n: {"calls": t.get("calls", 0), "offered": bool(t.get("offered"))}
            for n, t in (found.get("tools") or {}).items()
        },
        "prompts": found.get("prompts") or [],
        "models": found.get("models") or {},
        "routes": found.get("routes") or {},
    }


async def uses(targets: Targets, name: str, now: float | None = None) -> dict[str, Any] | None:
    """What real use touched lately (the digest's tools, prompts, models and routes, with the
    window it covers): kept for :data:`USES_SECONDS`; ``None`` when the traces can't be read
    and nothing was kept."""
    now = time.time() if now is None else now
    kept = read_doc(targets, name, USES_DOC)
    kept = kept if isinstance(kept, dict) else None
    keep = USES_SECONDS if kept and kept.get("sessions") else NONE_SECONDS
    if kept is not None and now - float(kept.get("taken_at") or 0) < keep:
        return kept
    try:
        groups = await asyncio.wait_for(
            recent_sessions(targets.source(name), targets.trace_attribute(name), SESSIONS),
            TRACE_SECONDS,
        )
    except Exception:
        _logger.warning("Couldn't read %s's traces for its skeleton", name, exc_info=True)
        return kept
    spans = [s for rows in groups for s in rows]
    fresh = {
        **_kept_uses(digest(groups)),
        "taken_at": now,
        "since": min((s.start for s in spans), default=None),
        "until": max((s.end for s in spans), default=None),
    }
    write_doc(targets, name, USES_DOC, fresh)
    return fresh


def _matchable(entry: Entry) -> bool:
    """Whether use could be counted for *entry*: a prompt built in code has no text to match."""
    return entry.kind != "prompt" or bool(entry.text or entry.hashes)


def shown(found: Skeleton, used: dict[str, Any] | None) -> dict[str, Any]:
    """The skeleton and its hits, as the page reads them: each entry's ``uses`` is how often real
    use touched it, ``0`` when it didn't in the window read, ``None`` with no use to go by."""
    hits = match(found, used)
    window = None
    if used is not None and hits.sessions:
        window = {
            "sessions": hits.sessions,
            "asks": hits.asks,
            "since": used.get("since"),
            "until": used.get("until"),
            "taken_at": used.get("taken_at"),
        }
    return {
        "fingerprint": found.fingerprint,
        "entries": [
            {k: v for k, v in e.as_dict().items() if k != "hashes"}
            | {"uses": hits.uses.get(e.id, 0) if window and _matchable(e) else None}
            for e in found.entries
        ],
        "unseen": [dict(u) for u in hits.unseen],
        "window": window,
    }
