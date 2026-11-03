"""A target's profile folder ``<targets>/<target>/``: ``profile.md``, ``profile_versions.json``,
``fingerprint.json``, ``drift.json`` and the rest of what evalution keeps on it as JSON.

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

PROFILE_CAP = 60_000
PROFILE_VERSIONS = 10
SETTINGS_FILE = "settings.json"
CONNECTION_FILE = "connection.json"
#: How what it knows is kept current: ``manual`` until the owner chooses, as ``auto`` spends.
UPDATES = ("auto", "manual")


def fingerprint_hash(taken: Any) -> str | None:
    """Short hash of a fingerprint, its time left out; ``None`` without one."""
    if not isinstance(taken, dict):
        return None
    body = {k: v for k, v in taken.items() if k != "taken_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def read_json(targets: Targets, name: str, file: str) -> Any:
    path = targets.folder(name) / file
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(targets: Targets, name: str, file: str, data: Any) -> None:
    folder = targets.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{file}.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(folder / file)


def updates(targets: Targets, name: str) -> str:
    """Whether model calls on a code change happen by themselves (``auto``) or on a click."""
    found = read_json(targets, name, SETTINGS_FILE)
    mode = found.get("updates") if isinstance(found, dict) else None
    return mode if mode in UPDATES else "manual"


def set_updates(targets: Targets, name: str, mode: str) -> None:
    found = read_json(targets, name, SETTINGS_FILE)
    kept = found if isinstance(found, dict) else {}
    write_json(targets, name, SETTINGS_FILE, {**kept, "updates": mode})


def connection(targets: Targets, name: str) -> dict[str, Any]:
    """What the owner chose while connecting *name*, and the last change of its adapter."""
    found = read_json(targets, name, CONNECTION_FILE)
    return found if isinstance(found, dict) else {}


def update_connection(targets: Targets, name: str, **changes: Any) -> None:
    write_json(targets, name, CONNECTION_FILE, {**connection(targets, name), **changes})


def required_clean(targets: Targets, name: str) -> list[str]:
    """What the owner asks every test of *name* to start clean of; by default nothing."""
    return [str(k) for k in connection(targets, name).get("required_clean") or []]


def read_profile(targets: Targets, name: str) -> str:
    path = targets.folder(name) / "profile.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def write_profile(targets: Targets, name: str, text: str, *, by: str = "admin") -> None:
    """Write the profile and keep it as a new version, with the hash of the fingerprint
    on file (the agent writes the fingerprint first; an admin's edit keeps it)."""
    if len(text) > PROFILE_CAP:
        raise ValueError(f"profile: at most {PROFILE_CAP} characters")
    folder = targets.folder(name)
    folder.mkdir(parents=True, exist_ok=True)
    text = text.rstrip() + "\n"
    (folder / "profile.md").write_text(text, encoding="utf-8")
    versions = profile_versions(targets, name)
    versions.append(
        {
            "version": (versions[-1]["version"] + 1) if versions else 1,
            "at": time.time(),
            "by": by,
            "fingerprint": fingerprint_hash(read_json(targets, name, "fingerprint.json")),
            "text": text,
        }
    )
    write_json(targets, name, "profile_versions.json", versions[-PROFILE_VERSIONS:])


def profile_versions(targets: Targets, name: str) -> list[dict[str, Any]]:
    found = read_json(targets, name, "profile_versions.json")
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
