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
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from evalution.code import Code
from evalution.project import areas, codemap, focus, imports
from evalution.project.codemap import CodeMap
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
    return of_map(targets, name, None, code, code.hashes())


def of_map(
    targets: Targets, name: str, cmap: CodeMap | None, code: Code, prints: Mapping[str, str]
) -> Skeleton:
    """*name*'s skeleton for the watched files' fingerprints *prints*: the one kept for them,
    else made from *cmap* (built when ``None``), a file read again only when it moved, and
    kept. A learn gives the map it just built."""
    key = fingerprint_of(prints)
    body = read_doc(targets, name, DOC)
    kept = Skeleton.of_dict(body)
    if kept is not None and isinstance(body, dict) and body.get("for") == key:
        return kept
    if cmap is None:
        cmap = codemap.build(targets, name, code).map
    found = skeleton(cmap, code.read, kept)
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


# The one agent tested, on a platform that hosts many (next-level C7, C11)


def focused(
    targets: Targets, name: str, code: Code, found: Skeleton
) -> tuple[str | None, set[str] | None]:
    """*name*'s ``code.focus`` when an agent of *found* has that name, and the files the learn
    covers for it (:func:`areas.agent_scope`; ``None``: every watched file, as when
    ``code.include`` names them)."""
    wanted = code.focus
    if not wanted or not any(a.name == wanted for a in focus.agents(found.entries)):
        return None, None
    if code.include:
        return wanted, None
    prints = code.hashes()
    edges = imports.kept_graph(targets, name, code.root, prints)
    return wanted, areas.agent_scope(found.entries, edges, list(prints), wanted)


def shown_for(
    found: Skeleton, used: dict[str, Any] | None, wanted: str | None, scope: set[str] | None
) -> dict[str, Any]:
    """:func:`shown`, limited to the agent tested (*wanted*) and the files its learn covers
    (*scope*): the other agents aren't listed, ``outside_agents`` says how many there are."""
    entries, left = focus.limit(found.entries, wanted, scope)
    page = shown(replace(found, entries=tuple(entries)), used)
    return {**page, "focus": wanted, "outside_agents": left}


async def page(targets: Targets, name: str, code: Code) -> dict[str, Any]:
    """The skeleton as What it learned shows it, limited to the agent tested when there is one.

    :raises CodeError: The code can't be read.
    """
    found = await asyncio.to_thread(current, targets, name, code)
    used = await uses(targets, name)
    wanted, scope = await asyncio.to_thread(focused, targets, name, code, found)
    return shown_for(found, used, wanted, scope)


async def agent_choices(targets: Targets, name: str, code: Code) -> dict[str, Any]:
    """The agents *name*'s code defines, as connecting offers them (:func:`focus.choices`), most
    used first, and the one tested now (``focus``; ``None``: all of them). ``asks``: whether
    there are enough that connecting asks.

    :raises CodeError: The code can't be read.
    """
    found = await asyncio.to_thread(current, targets, name, code)
    used = await uses(targets, name)
    hits = match(found, used).uses if used and used.get("sessions") else None
    prints = await asyncio.to_thread(code.hashes)
    edges = await asyncio.to_thread(imports.kept_graph, targets, name, code.root, prints)
    return {
        "focus": code.focus,
        "asks": focus.asks(found.entries),
        "choices": focus.choices(found.entries, edges, hits),
    }
