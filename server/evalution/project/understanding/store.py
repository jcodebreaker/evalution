"""The understanding kept on a target. Each write renders the profile again."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from evalution.project.profile import (
    FINGERPRINT_DOC,
    fingerprint_hash,
    read_doc,
    read_profile,
    write_doc,
    write_profile,
)
from evalution.project.understanding.document import with_details
from evalution.project.understanding.rendering import render_profile
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
AREA_STATES = ("current", "learning", "done", "due", "listed", "failed")
OVERVIEW_STATES = ("first_pass", "final")


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
    the profile from it. *replace*: in place of the last version, under its number (a learn
    saving as its areas land keeps one version, the last it saved)."""
    kept = versions(targets, name)
    number = (kept[-1]["version"] + 1) if kept else 1
    if replace and kept:
        number, kept = kept[-1]["version"], kept[:-1]
    doc = {
        **doc,
        "version": number,
        "written_at": time.time(),
        "by": by,
        "fingerprint": fingerprint_hash(read_doc(targets, name, FINGERPRINT_DOC)) or "",
    }
    write_doc(targets, name, VERSIONS_DOC, [*kept, doc][-VERSIONS:])
    write_doc(targets, name, DOC, doc)
    write_profile(targets, name, render_profile(name, doc, steers(targets, name)), by=by)
    return doc


def progress(targets: Targets, name: str) -> dict[str, Any]:
    """How the last learn went: ``state`` (``learning``, ``done``, ``stopped``; ``None`` when no
    learn kept one, as before ADR 0012), ``started_at``, its ``areas`` heaviest first (each
    ``id``, ``title``, ``state`` (:data:`AREA_STATES`), ``weight``, ``files``, ``learned_at``)
    and the ``overview``'s state (``first_pass``, ``final`` or ``None``)."""
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
        write_profile(targets, name, render_profile(name, doc, log), by="admin")
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


def writer_profile(targets: Targets, name: str) -> str:
    """The profile as the writer is given it: rendered without Details (see :func:`written`), or
    as kept when there is no understanding to render it from."""
    stored = load(targets, name)
    if stored is None:
        return read_profile(targets, name)
    return render_profile(name, with_details(stored, []), steers(targets, name))
