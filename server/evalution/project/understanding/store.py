"""The understanding on file. Each write renders ``profile.md`` again."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from evalution.project.profile import fingerprint_hash, read_json, write_json, write_profile
from evalution.project.understanding.rendering import render_profile
from evalution.project.understanding.steering import (
    STEERS_FILE,
    STEERS_KEPT,
    overlay,
    steer_entry,
    steers,
)
from evalution.targets import Targets

FILE = "understanding.json"
VERSIONS_FILE = "understanding_versions.json"
VERSIONS = 10


def load(targets: Targets, name: str) -> dict[str, Any] | None:
    found = read_json(targets, name, FILE)
    return found if isinstance(found, dict) else None


def versions(targets: Targets, name: str) -> list[dict[str, Any]]:
    found = read_json(targets, name, VERSIONS_FILE)
    return found if isinstance(found, list) else []


def save(targets: Targets, name: str, doc: dict[str, Any], *, by: str = "agent") -> dict:
    """Store *doc* as a new version (with the hash of the fingerprint on file) and render
    ``profile.md`` from it."""
    kept = versions(targets, name)
    doc = {
        **doc,
        "version": (kept[-1]["version"] + 1) if kept else 1,
        "written_at": time.time(),
        "by": by,
        "fingerprint": fingerprint_hash(read_json(targets, name, "fingerprint.json")) or "",
    }
    write_json(targets, name, VERSIONS_FILE, [*kept, doc][-VERSIONS:])
    write_json(targets, name, FILE, doc)
    write_profile(targets, name, render_profile(name, doc, steers(targets, name)), by=by)
    return doc


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
    write_json(targets, name, STEERS_FILE, log)
    if "claim" in entry or "node" in entry:
        write_profile(targets, name, render_profile(name, doc, log), by="admin")
    return entry


def written(targets: Targets, name: str) -> dict[str, Any] | None:
    """What the agent wrote last, with the steers laid over it, so it can keep ids."""
    stored = load(targets, name)
    if stored is None:
        return None
    doc = overlay(stored, steers(targets, name))
    keys = ("one_line", "nodes", "sections", "priorities", "starters", "step_labels")
    return {k: doc.get(k) for k in (*keys, "expected_paths")}
