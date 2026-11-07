"""What evalution keeps on each target: documents of kind :data:`KIND` keyed
``<target>/<doc>`` (``bot/profile.md``, ``bot/drift.json``...), read and written through
:func:`read_doc` and :func:`write_doc`. This module owns the profile itself, its versions, the
owner's settings and what was chosen while connecting; the modules that own the other documents
name them.

Every write of the profile is kept as a version (who wrote it, when, and the hash of the
fingerprint it stands for), the newest :data:`PROFILE_VERSIONS` of them, so the page can show
what changed since the version before.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from typing import Any

from evalution.targets import Targets

KIND = "profile"
PROFILE_DOC = "profile.md"
VERSIONS_DOC = "profile_versions.json"
SETTINGS_DOC = "settings.json"
CONNECTION_DOC = "connection.json"
#: What the profile stands for (:mod:`evalution.project.drift` takes it).
FINGERPRINT_DOC = "fingerprint.json"
#: Far past what a whole app's understanding renders to: a guard, never what limits coverage.
PROFILE_CAP = 400_000
PROFILE_VERSIONS = 10
#: How what it knows is kept current: ``manual`` until the owner chooses, as ``auto`` spends.
UPDATES = ("auto", "manual")


def fingerprint_hash(taken: Any) -> str | None:
    """Short hash of a fingerprint, its time left out; ``None`` without one."""
    if not isinstance(taken, dict):
        return None
    body = {k: v for k, v in taken.items() if k != "taken_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def _key(targets: Targets, name: str, doc: str) -> str:
    """:raises LookupError: No such target."""
    targets.conf(name)
    return f"{name}/{doc}"


def read_doc(targets: Targets, name: str, doc: str) -> Any:
    """Document *doc* of target *name*; ``None`` when there is none."""
    return targets.docs.get(KIND, _key(targets, name, doc))


def write_doc(targets: Targets, name: str, doc: str, body: Any) -> None:
    targets.docs.put(KIND, _key(targets, name, doc), body)


def updates(targets: Targets, name: str) -> str:
    """Whether model calls on a code change happen by themselves (``auto``) or on a click."""
    found = read_doc(targets, name, SETTINGS_DOC)
    mode = found.get("updates") if isinstance(found, dict) else None
    return mode if mode in UPDATES else "manual"


def set_updates(targets: Targets, name: str, mode: str) -> None:
    found = read_doc(targets, name, SETTINGS_DOC)
    kept = found if isinstance(found, dict) else {}
    write_doc(targets, name, SETTINGS_DOC, {**kept, "updates": mode})


def connection(targets: Targets, name: str) -> dict[str, Any]:
    """What the owner chose while connecting *name*, and the last change of its adapter."""
    found = read_doc(targets, name, CONNECTION_DOC)
    return found if isinstance(found, dict) else {}


def update_connection(targets: Targets, name: str, **changes: Any) -> None:
    write_doc(targets, name, CONNECTION_DOC, {**connection(targets, name), **changes})


def required_clean(targets: Targets, name: str) -> list[str]:
    """What the owner asks every test of *name* to start clean of; by default nothing."""
    return [str(k) for k in connection(targets, name).get("required_clean") or []]


def read_profile(targets: Targets, name: str) -> str:
    found = read_doc(targets, name, PROFILE_DOC)
    return found if isinstance(found, str) else ""


def write_profile(targets: Targets, name: str, text: str, *, by: str = "admin") -> None:
    """Write the profile and keep it as a new version, with the hash of the fingerprint
    kept (the agent writes the fingerprint first; an admin's edit keeps it)."""
    if len(text) > PROFILE_CAP:
        raise ValueError(f"profile: at most {PROFILE_CAP} characters")
    text = text.rstrip() + "\n"
    write_doc(targets, name, PROFILE_DOC, text)
    versions = profile_versions(targets, name)
    versions.append(
        {
            "version": (versions[-1]["version"] + 1) if versions else 1,
            "at": time.time(),
            "by": by,
            "fingerprint": fingerprint_hash(read_doc(targets, name, FINGERPRINT_DOC)),
            "text": text,
        }
    )
    write_doc(targets, name, VERSIONS_DOC, versions[-PROFILE_VERSIONS:])


def profile_versions(targets: Targets, name: str) -> list[dict[str, Any]]:
    found = read_doc(targets, name, VERSIONS_DOC)
    return found if isinstance(found, list) else []


def profile_view(targets: Targets, name: str) -> dict[str, Any]:
    """The profile text, its version (without the text) and the lines changed since the
    version before: ``[{"kind": "added"|"removed", "text"}]``."""
    text = read_profile(targets, name)
    versions = profile_versions(targets, name)
    latest = versions[-1] if versions and versions[-1]["text"] == text else None
    before = versions[-2]["text"] if latest and len(versions) > 1 else None
    changes = []
    if before is not None:
        for line in difflib.ndiff(before.splitlines(), text.splitlines()):
            if line[:2] in ("+ ", "- ") and line[2:].strip():
                changes.append({"kind": "added" if line[0] == "+" else "removed", "text": line[2:]})
    return {
        "text": text,
        "version": {k: v for k, v in latest.items() if k != "text"} if latest else None,
        "changes": changes,
    }
