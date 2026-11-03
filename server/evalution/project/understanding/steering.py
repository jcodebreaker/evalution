"""What the admin steers: stored in ``steers.json`` and laid over the understanding on read."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.project.profile import read_json
from evalution.project.understanding.document import clean_text
from evalution.targets import Targets

STEERS_FILE = "steers.json"
#: What each claim rests on and whether it may be out of date (:mod:`evalution.project.knowledge`).
KNOWLEDGE_FILE = "knowledge.json"
STEERS_KEPT = 500


def steers(targets: Targets, name: str) -> list[dict[str, Any]]:
    found = read_json(targets, name, STEERS_FILE)
    return found if isinstance(found, list) else []


def steer_entry(body: Mapping[str, Any], doc: Mapping[str, Any]) -> dict[str, Any]:
    """One steer read against *doc*.

    :raises ValueError: A body that is none of the steers.
    :raises LookupError: A claim, node, priority or starter that isn't there.
    """
    for key in ("claim", "node"):
        if key not in body:
            continue
        items = (
            [c for s in doc.get("sections") or [] for c in s["claims"]]
            if key == "claim"
            else doc.get("nodes") or []
        )
        item = next((i for i in items if i["id"] == body[key]), None)
        if item is None:
            raise LookupError(f"no {key} {body[key]}")
        entry = {key: item["id"], "text": item["text" if key == "claim" else "title"]}
        if body.get("verdict") in ("right", "wrong"):
            return {**entry, "verdict": body["verdict"]}
        if fix := clean_text(body.get("fix")):
            return {**entry, "fix": fix[:1_000]}
        raise ValueError('give verdict: "right"|"wrong", or fix: text')
    if "priority" in body:
        if isinstance(body["priority"], dict):
            title = clean_text(body["priority"].get("title"))
            if not title:
                raise ValueError("priority.title: required")
            slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:40] or "own"
            return {"priority": f"admin-{slug}", "title": title[:200], "picked": True}
        known = {p["id"] for p in doc.get("priorities") or []}
        if body["priority"] not in known:
            raise LookupError(f"no priority {body['priority']}")
        if not isinstance(body.get("picked"), bool):
            raise ValueError("picked: true or false")
        return {"priority": body["priority"], "picked": body["picked"]}
    if "starter" in body:
        if body["starter"] not in {s["id"] for s in doc.get("starters") or []}:
            raise LookupError(f"no starter {body['starter']}")
        if not isinstance(body.get("selected"), bool):
            raise ValueError("selected: true or false")
        return {"starter": body["starter"], "selected": body["selected"]}
    raise ValueError("give claim, node, priority or starter")


def overlay(doc: Mapping[str, Any], log: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """*doc* with the steers in *log* laid over it, later ones winning. A verdict or fix
    holds only while the claim's text (the node's title) is the one it was given on."""
    out = copy.deepcopy(dict(doc))
    marks: dict[tuple[str, str], Mapping[str, Any]] = {}
    picked: dict[str, bool] = {}
    added: dict[str, str] = {}
    selected: dict[str, bool] = {}
    for entry in log:
        for key in ("claim", "node"):
            if key in entry:
                marks[(key, entry[key])] = entry
        if "priority" in entry:
            picked[entry["priority"]] = bool(entry["picked"])
            if "title" in entry:
                added[entry["priority"]] = entry["title"]
        if "starter" in entry:
            selected[entry["starter"]] = bool(entry["selected"])

    def mark(item: dict[str, Any], key: str, text: str) -> None:
        entry = marks.get((key, item["id"]))
        if entry is None or entry["text"] != text:
            return
        for field in ("verdict", "fix"):
            if field in entry:
                item[field] = entry[field]

    for section in out.get("sections") or []:
        for claim in section["claims"]:
            mark(claim, "claim", claim["text"])
    for node in out.get("nodes") or []:
        mark(node, "node", node["title"])
    priorities = out.setdefault("priorities", [])
    for priority in priorities:
        priority["picked"] = picked.get(priority["id"], priority["picked"])
    known = {p["id"] for p in priorities}
    priorities += [
        {"id": pid, "title": title, "picked": picked.get(pid, True), "source": "admin"}
        for pid, title in added.items()
        if pid not in known
    ]
    for starter in out.get("starters") or []:
        starter["selected"] = selected.get(starter["id"], starter["selected"])
    return out


def marks(
    targets: Targets, name: str, log: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """``claim:<id>`` or ``node:<id>`` -> how a code change touched it: ``stale`` (``direct`` or
    ``indirect``) and ``flagged``. A flag goes once the owner steered that claim after it."""
    found = read_json(targets, name, KNOWLEDGE_FILE)
    state = found if isinstance(found, dict) else {}
    out: dict[str, dict[str, Any]] = {
        key: {"stale": entry["level"]} for key, entry in (state.get("stale") or {}).items()
    }
    latest = {}
    for entry in log:
        for field in ("claim", "node"):
            if field in entry:
                latest[f"{field}:{entry[field]}"] = float(entry.get("at") or 0)
    for key, flag in (state.get("flags") or {}).items():
        if latest.get(key, 0.0) <= float(flag.get("at") or 0):
            out.setdefault(key, {})["flagged"] = True
    return out


def admin_priorities(targets: Targets, name: str) -> set[str]:
    return {e["priority"] for e in steers(targets, name) if "title" in e}
