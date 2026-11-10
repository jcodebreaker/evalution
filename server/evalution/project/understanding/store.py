"""The understanding kept on a target. Each write renders the profile again."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from evalution.project import filememory
from evalution.project import profile as _profile
from evalution.project.profile import (
    FINGERPRINT_DOC,
    fingerprint_hash,
    read_doc,
    read_profile,
    write_doc,
    write_profile,
)
from evalution.project.understanding.document import with_details
from evalution.project.understanding.rendering import fit_profile, only_named
from evalution.project.understanding.steering import (
    STEERS_DOC,
    STEERS_KEPT,
    overlay,
    steer_entry,
    steers,
)
from evalution.targets import Targets

DOC = "understanding.json"
VERSIONS_DOC = "understanding_versions.json"
VERSIONS = 10
#: How the last learn went, area by area (ADR 0012): kept apart from the understanding, so an
#: area starting writes no version.
PROGRESS_DOC = "learn_progress.json"
#: What a learn's progress says of itself, and of each of its areas.
PROGRESS_STATES = ("learning", "done", "stopped")
AREA_STATES = ("current", "learning", "done", "due", "listed", "failed", "outside")
#: ``facts``: no area read yet, the card from the code's skeleton and real use alone (C1).
OVERVIEW_STATES = ("facts", "first_pass", "final")


def load(targets: Targets, name: str) -> dict[str, Any] | None:
    found = read_doc(targets, name, DOC)
    return found if isinstance(found, dict) else None


def versions(targets: Targets, name: str) -> list[dict[str, Any]]:
    found = read_doc(targets, name, VERSIONS_DOC)
    return found if isinstance(found, list) else []


def save(
    targets: Targets, name: str, doc: dict[str, Any], *, by: str = "agent", replace: bool = False
) -> dict:
    """Store *doc* as a new version (with the hash of the fingerprint kept) and render
    the profile from it, cut to fit (:func:`fit_profile`): what is in it is kept as ``profile``
    (``{areas, of, left_out}``). A save never fails on the profile's size. *replace*: in place of
    the last version, under its number (a learn saving as its areas land keeps one version, the
    last it saved)."""
    kept = versions(targets, name)
    number = (kept[-1]["version"] + 1) if kept else 1
    if replace and kept:
        number, kept = kept[-1]["version"], kept[:-1]
    text, fit = fit_profile(name, doc, steers(targets, name), _profile.PROFILE_CAP)
    doc = {
        **doc,
        "profile": fit,
        "version": number,
        "written_at": time.time(),
        "by": by,
        "fingerprint": fingerprint_hash(read_doc(targets, name, FINGERPRINT_DOC)) or "",
    }
    write_doc(targets, name, VERSIONS_DOC, [*kept, doc][-VERSIONS:])
    write_doc(targets, name, DOC, doc)
    write_profile(targets, name, text, by=by)
    _into_areas(targets, name, doc)
    return doc


def _into_areas(targets: Targets, name: str, doc: Mapping[str, Any]) -> None:
    """Write *doc*'s area sections and nodes back into the areas a learn keeps
    (:mod:`evalution.project.filememory`), so the area's stored section stays the one source: a
    refresh's or a writer's edit is what the next learn merges, not what was learned before it.
    An area's key is left as it is, so this never makes it learned again."""
    sections = {s["id"]: s for s in doc.get("sections") or [] if isinstance(s, dict)}
    # What the engine computes on a node (whether real use saw it) is not what an area wrote.
    nodes = {
        n["id"]: {k: v for k, v in n.items() if k != "seen"}
        for n in doc.get("nodes") or []
        if isinstance(n, dict)
    }
    with targets.guards.knowledge:
        memory = filememory.load(targets, name)
        kept = memory.get("areas") or {}
        moved = False
        for entry in kept.values():
            for n, section in enumerate(entry.get("sections") or []):
                new = sections.get(section.get("id"))
                if new is not None and new != section:
                    entry["sections"][n] = dict(new)
                    moved = True
            for n, node in enumerate(entry.get("nodes") or []):
                new = nodes.get(node.get("id"))
                if new is not None and new != node:
                    entry["nodes"][n] = dict(new)
                    moved = True
        if moved:
            filememory.save(targets, name, {**memory, "areas": kept})


def progress(targets: Targets, name: str) -> dict[str, Any]:
    """How the last learn went: ``state`` (``learning``, ``done``, ``stopped``; ``None`` when no
    learn kept one, as before ADR 0012), ``started_at``, its ``areas`` heaviest first (each
    ``id``, ``title``, ``state`` (:data:`AREA_STATES`), ``weight``, ``files``, ``learned_at``)
    and the ``overview``'s state (``first_pass``, ``final`` or ``None``); ``left_out``: what the
    code's watch left out, counted (``past_cap``, ``too_large``; empty when nothing)."""
    found = read_doc(targets, name, PROGRESS_DOC)
    found = found if isinstance(found, dict) else {}
    state = found.get("state")
    overview = found.get("overview")
    return {
        "state": state if state in PROGRESS_STATES else None,
        "started_at": found.get("started_at"),
        "areas": [
            a
            for a in found.get("areas") or []
            if isinstance(a, dict) and a.get("state") in AREA_STATES
        ],
        "overview": overview if overview in OVERVIEW_STATES else None,
        "left_out": {
            k: int(v)
            for k, v in (found.get("left_out") or {}).items()
            if k in ("past_cap", "too_large") and isinstance(v, int)
        },
    }


def keep_progress(targets: Targets, name: str, body: Mapping[str, Any]) -> None:
    write_doc(targets, name, PROGRESS_DOC, dict(body))


def steer(targets: Targets, name: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Store one steer and render the profile again, so a fix or wrong reaches the agent at
    once.

    :raises LookupError: No understanding yet, or no such claim, node, priority or starter.
    :raises ValueError: A body that is none of the steers.
    """
    doc = load(targets, name)
    if doc is None:
        raise LookupError("no understanding yet")
    entry = {**steer_entry(body, overlay(doc, steers(targets, name))), "at": time.time()}
    log = [*steers(targets, name), entry][-STEERS_KEPT:]
    write_doc(targets, name, STEERS_DOC, log)
    if "claim" in entry or "node" in entry:
        text, _fit = fit_profile(name, doc, log, _profile.PROFILE_CAP)
        write_profile(targets, name, text, by="admin")
    return entry


def written(targets: Targets, name: str) -> dict[str, Any] | None:
    """What the agent wrote last, with the steers laid over it, so it can keep ids. Details is
    left out: the engine writes it, and sent to the model it would cost as much as the code."""
    stored = load(targets, name)
    if stored is None:
        return None
    doc = overlay(with_details(stored, []), steers(targets, name))
    keys = ("one_line", "summary", "nodes", "sections", "priorities", "starters", "step_labels")
    return {k: doc.get(k) for k in (*keys, "expected_paths")}


#: What the writer's copy of the profile may hold: about 8,000 tokens. It is sent at every step.
WRITER_CHARS = 32_000


def writer_profile(targets: Targets, name: str, said: str | None = None) -> str:
    """The profile as the writer is given it, in at most :data:`WRITER_CHARS`: rendered without
    Details (see :func:`written`), the heaviest areas' sections left out first past the cap
    (:func:`fit_profile`), or as kept when there is no understanding to render it from. *said*:
    the conversation so far, when given; then an area's claims are sent only when it names a
    tool, file or name of that area (:func:`only_named`), the rest by title, for
    ``read_understanding``."""
    stored = load(targets, name)
    if stored is None:
        return read_profile(targets, name)[:WRITER_CHARS]
    doc = with_details(stored, [])
    if said is not None:
        doc = only_named(doc, said)
    return fit_profile(name, doc, steers(targets, name), WRITER_CHARS)[0]
