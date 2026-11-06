"""Noticing when a target changes (docs/architecture.md, "Drift").

The fingerprint (:data:`~evalution.project.profile.FINGERPRINT_DOC`) is taken when the profile
is written: tool names with argument key sets, the system prompt's hash and section hashes,
trajectory shape counts, and for a connected repo its last commit and the hashes of the watched
files. :func:`check` takes a fresh one (a digest of the last few sessions, plus ``git rev-parse``)
and writes the differences to :data:`DOC`; it runs before every run and once a day, and
``POST /v1/targets/{target}/changed`` runs its code part in the background. A trace difference
counts only once two checks in a row see it (a small sample can miss a tool or a prompt variant);
a code change counts at once; :func:`check_code` is that part alone, reading no traces, for the
Home page. A case whose expectations name a changed or removed tool, argument or
prompt fact is stale (:func:`stale`).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.case import CONTEXT_CHECKS, Case
from evalution.project.profile import FINGERPRINT_DOC, read_doc, write_doc
from evalution.targets import Targets
from evalution.traces.profile import digest, fingerprint, recent_sessions

_logger = logging.getLogger(__name__)

DOC = "drift.json"
#: Sessions a drift check reads: few, so it stays cheap.
CHECK_SESSIONS = 10
#: Sessions a profile refresh reads.
PROFILE_SESSIONS = 50
#: A move in the share of sessions with delegated work that counts as a shape change.
SHAPE_SHARE = 0.25
KINDS = (
    "tool_added",
    "tool_removed",
    "args_changed",
    "prompt_changed",
    "shape_changed",
    "code_changed",
)


async def take(targets: Targets, name: str, sessions: int) -> tuple[dict[str, Any], dict]:
    """``(digest, fingerprint)`` of *name* now. Without traces those parts are empty."""
    source = targets.source(name)
    groups = []
    if source is not None:
        groups = await recent_sessions(source, targets.trace_attribute(name), sessions)
    found = digest(groups, targets.price)
    taken = fingerprint(found)
    code = targets.code(name)
    if code is not None:
        taken["code"] = await asyncio.to_thread(code.state)
    return found, taken


def _change(kind: str, detail: str, key: str | None = None, **extra: Any) -> dict[str, Any]:
    key = hashlib.sha256(f"{kind}:{key or detail}".encode()).hexdigest()[:10]
    return {"id": key, "kind": kind, "detail": detail, **extra}


def compare(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The typed changes from fingerprint *old* to *new*. Trace parts are compared only when
    the new one saw sessions; tools and their arguments only by what both offered the model (a
    tool merely not called lately is not gone); the prompt only by the main agent's, and only
    when its usual one is none of the variants seen before."""
    changes: list[dict[str, Any]] = []
    if new.get("sessions"):
        before, after = old.get("tools") or {}, new.get("tools") or {}
        if new.get("tools_offered_known") and old.get("tools_offered_known"):
            changes += [
                _change("tool_added", f"new tool {n}", tool=n)
                for n in sorted(set(after) - set(before))
            ]
            for gone in sorted(set(before) - set(after)):
                changes.append(_change("tool_removed", f"tool {gone} is gone", tool=gone))
            for name in sorted(set(before) & set(after)):
                removed = sorted(set(before[name]) - set(after[name]))
                added = sorted(set(after[name]) - set(before[name]))
                if removed or added:
                    words = [*(f"-{a}" for a in removed), *(f"+{a}" for a in added)]
                    changes.append(
                        _change(
                            "args_changed",
                            f"{name}: {' '.join(words)}",
                            tool=name,
                            removed=removed,
                            added=added,
                        )
                    )
        prompt_old, prompt_new = old.get("prompt") or {}, new.get("prompt") or {}
        seen = set(prompt_old.get("hashes") or [prompt_old.get("hash")])
        if prompt_old.get("hash") and prompt_new.get("hash") and prompt_new["hash"] not in seen:
            a, b = prompt_old.get("sections") or {}, prompt_new.get("sections") or {}
            moved = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            changes.append(
                _change(
                    "prompt_changed",
                    "system prompt changed: " + (", ".join(moved[:8]) or "text"),
                    key=prompt_new["hash"],
                    sections=moved,
                )
            )
        shapes_old, shapes_new = old.get("shapes") or {}, new.get("shapes") or {}
        top_old = next(iter(shapes_old.get("sequences") or {}), None)
        top_new = next(iter(shapes_new.get("sequences") or {}), None)
        share = abs(
            float(shapes_new.get("delegated_share") or 0)
            - float(shapes_old.get("delegated_share") or 0)
        )
        if (top_old and top_new and top_old != top_new) or share >= SHAPE_SHARE:
            changes.append(
                _change(
                    "shape_changed",
                    f"usual path {top_old} -> {top_new}; delegation moved {share:.0%}",
                    key=f"{top_new}:{round(float(shapes_new.get('delegated_share') or 0), 1)}",
                )
            )
    code_old, code_new = old.get("code") or {}, new.get("code") or {}
    if code_new and code_old:
        a, b = code_old.get("files") or {}, code_new.get("files") or {}
        files = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if files:
            changes.append(
                _change(
                    "code_changed",
                    f"{len(files)} watched file(s) changed",
                    files=files[:100],
                    since=code_old.get("commit"),
                )
            )
    return changes


async def baseline(targets: Targets, name: str) -> dict[str, Any]:
    """Take the fingerprint the profile now stands for, and clear the drift."""
    _found, taken = await take(targets, name, PROFILE_SESSIONS)
    keep_baseline(targets, name, taken)
    return taken


def keep_baseline(targets: Targets, name: str, taken: Mapping[str, Any]) -> None:
    """Keep *taken* as the fingerprint the profile stands for, and clear the drift."""
    write_doc(targets, name, FINGERPRINT_DOC, taken)
    write_doc(targets, name, DOC, {"checked_at": time.time(), "changes": []})


def _same_watch(targets: Targets, name: str, old: dict, code: Mapping[str, Any] | None) -> dict:
    """The fingerprint kept, its code part taken again when what is watched changed (one from
    before everything was watched, or ``code.ignore`` moved): every file would read as changed."""
    if code and (old.get("code") or {}).get("watch") != code.get("watch"):
        old = {**old, "code": code}
        write_doc(targets, name, FINGERPRINT_DOC, old)
    return old


def _counted(found: Sequence[Mapping[str, Any]], kept: Mapping[str, Any], now: float) -> list[dict]:
    """*found* with when each was first counted and the agent's summary, kept while it stays."""
    return [
        {
            **c,
            # When the change was first counted, for the Home page's trend.
            "seen_at": kept.get(c["id"], {}).get("seen_at") or now,
            **(
                {"summary": kept[c["id"]]["summary"]}
                if kept.get(c["id"], {}).get("summary")
                else {}
            ),
        }
        for c in found
    ]


async def check_code(targets: Targets, name: str) -> None:
    """The code part of :func:`check`: quick, and no traces read. The trace changes stay as
    they are; nothing happens before the first fingerprint."""
    code = targets.code(name)
    old = read_doc(targets, name, FINGERPRINT_DOC)
    if code is None or old is None:
        return
    state = await asyncio.to_thread(code.state)
    old = _same_watch(targets, name, old, state)
    last = read(targets, name)
    changes = last.get("changes") or []
    now = time.time()
    fresh = compare({"code": old.get("code")}, {"code": state})
    doc = {
        **last,
        "changes": [
            *(c for c in changes if c["kind"] != "code_changed"),
            *_counted(fresh, {c["id"]: c for c in changes}, now),
        ],
    }
    write_doc(targets, name, DOC, doc)


async def check(targets: Targets, name: str) -> dict[str, Any]:
    """Compare *name* now with its fingerprint and write the drift; the first check takes
    the fingerprint. A summary the agent wrote on a change, and when it was first counted
    (``seen_at``), are kept while the change stays."""
    old = read_doc(targets, name, FINGERPRINT_DOC)
    if old is None:
        await baseline(targets, name)
        return read(targets, name)
    _found, new = await take(targets, name, CHECK_SESSIONS)
    old = _same_watch(targets, name, old, new.get("code"))
    last = read(targets, name)
    kept = {c["id"]: c for c in (last.get("changes") or [])}
    pending = set(last.get("pending") or [])
    found = compare(old, new)
    confirmed = [
        c for c in found if c["kind"] == "code_changed" or c["id"] in kept or c["id"] in pending
    ]
    now = time.time()
    changes = _counted(confirmed, kept, now)
    doc = {
        "checked_at": now,
        "baseline_at": old.get("taken_at"),
        "changes": changes,
        # Seen once: a change only once the next check sees it too.
        "pending": sorted(c["id"] for c in found if c not in confirmed),
    }
    write_doc(targets, name, DOC, doc)
    return doc


async def check_quietly(targets: Targets, name: str) -> None:
    try:
        await check(targets, name)
    except Exception:
        _logger.warning("drift: %s not checked", name, exc_info=True)


def read(targets: Targets, name: str) -> dict[str, Any]:
    return read_doc(targets, name, DOC) or {"checked_at": None, "changes": []}


def note(targets: Targets, name: str, change: str, summary: str) -> dict[str, Any]:
    """Write the agent's behaviour summary onto drift entry *change*.

    :raises LookupError: No such entry.
    """
    doc = read(targets, name)
    for entry in doc.get("changes") or []:
        if entry["id"] == change:
            entry["summary"] = " ".join(summary.split())[:1_000]
            write_doc(targets, name, DOC, doc)
            return entry
    raise LookupError(f"no drift entry {change}")


def _named(case: Case) -> dict[str, set[str]]:
    """``tool -> argument names`` the case's expectations name (``never`` tools too)."""
    names: dict[str, set[str]] = {n: set() for n in case.tools.get("never") or []}
    for step in case.tools.get("expected") or []:
        for spec in step.get("parallel") or [step]:
            names.setdefault(spec["name"], set()).update((spec.get("args") or {}).keys())
    return names


def stale(case: Case, drift: Mapping[str, Any]) -> str | None:
    """Why *case* may be stale under *drift*, in one line; ``None`` when nothing it expects
    changed."""
    named = _named(case)
    checks_context = any(t.state and set(t.state) & set(CONTEXT_CHECKS) for t in case.turns)
    for change in drift.get("changes") or []:
        tool = change.get("tool")
        if change["kind"] == "tool_removed" and tool in named:
            return f"{tool} was removed (drift {change['id']})"
        if change["kind"] == "args_changed" and tool in named:
            gone = named[tool] & set(change.get("removed") or [])
            if gone:
                return f"{tool} no longer takes {', '.join(sorted(gone))} (drift {change['id']})"
        if change["kind"] == "prompt_changed" and checks_context:
            return f"the system prompt changed (drift {change['id']})"
    return None
