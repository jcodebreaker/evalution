"""What the page says of a target's isolation plan (ADR 0009) outside the Isolation section: Home's
"How tests run" card, the Run dialog's lines and a try's "Before" step. Read from saved files
only: no Docker, no model, nothing sent."""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Mapping
from typing import Any

from evalution.adapters.base import Isolation
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.project import isolating
from evalution.targets import Targets

#: What a store holds, by words in the places the probe found the app writing, the first match.
_HOLDS = (
    (re.compile(r"memory[_-]?index"), "Memory index"),
    (re.compile(r"conversation|message|chat|thread|round"), "Conversations"),
    (re.compile(r"memor"), "Memories"),
    (re.compile(r"vector|embedding|index"), "Search index"),
    (re.compile(r"upload|attachment|document|artifact|file"), "Files"),
    (re.compile(r"session"), "Sessions"),
    (re.compile(r"user|account|profile"), "Accounts"),
)
#: A copy's own names (``eval-<target>-<slot>-…``), which say nothing of what a store holds.
_COPY = re.compile(r"eval-[a-z0-9_-]*?-\d+[a-z0-9_-]*")
#: How long the last rehearsal took, as its report says it.
_REHEARSED = re.compile(r"Rehearsed on \S+ in (\d+(?:\.\d+)?)s")
#: Prefixes of a locator that is a name, not a place.
_NAMED = ("volume:", "env:", "service:", "package:", "socket:")


def _holds(store: PlannedStore, target: str) -> str | None:
    """What *store* holds in a person's words, from where the probe found the app writing in it;
    ``None`` when nothing says."""
    if store.kind == "memory":
        return "The app's memory while it runs"
    where = [text.rsplit(": ", 1)[-1] for text in store.seen]
    where += [text.split(":", 1)[-1] for text in store.found_as]
    text = _COPY.sub(" ", " ".join(where).lower()).replace(target.lower(), " ")
    return next((words for pattern, words in _HOLDS if pattern.search(text)), None)


def _own_name(store: PlannedStore) -> str:
    """The store's own name: the database, the volume, the file, or the setting."""
    locator = store.locator
    for prefix in _NAMED:
        if locator.startswith(prefix):
            return locator.removeprefix(prefix)
    if locator.startswith("/"):
        return locator.rstrip("/").rsplit("/", 1)[-1] or locator
    return locator.split(":", 1)[-1]


def store_words(plan: Plan) -> list[dict[str, Any]]:
    """Each store of *plan* as the page names it: its kind, its own name, what it holds, and
    whether the plan puts it back (``identity`` and ``none`` don't)."""
    return [
        {
            "id": s.id,
            "kind": s.kind,
            "name": _own_name(s),
            "holds": _holds(s, plan.target),
            "reset": s.strategy in ("relocate", "snapshot"),
        }
        for s in plan.stores
    ]


def _load(targets: Targets, name: str) -> tuple[Plan | None, str | None]:
    try:
        return PlanStore(targets.config.data_dir).load(name), None
    except ValueError as exc:
        return None, f"The saved plan doesn't read: {exc}"


def _declared(targets: Targets, name: str) -> tuple[str, str | None]:
    """How tries are kept apart without a plan (``identity`` or ``none``), and what the adapter
    says of it."""
    try:
        caps = targets.adapter(name).capabilities
    except Exception:  # an adapter that doesn't load says nothing here; Connection says why
        return "none", None
    how = "identity" if caps.isolation is Isolation.IDENTITY else "none"
    return how, caps.isolation_note


def card(targets: Targets, name: str) -> dict[str, Any]:
    """What Home says of how tests run on *name*, whatever the plan's status: its stores in plain
    words, what it leaves alone, when it was proven, the files a commit changed under a stale
    one, where a failed one leaked, and what the adapter promises without one."""
    plan, problem = _load(targets, name)
    plain, note = _declared(targets, name)
    stale_files: list[str] = []
    if plan is not None and plan.status == "stale":
        code = targets.code(name)
        found = isolating.changed_files(plan, code.root.resolve()) if code else []
        stale_files = found or isolating.noted(plan)
    return {
        "status": plan.status if plan else "none",
        "version": PlanStore(targets.config.data_dir).version(name) if plan else None,
        "updated": plan.updated if plan else None,
        "stores": store_words(plan) if plan else [],
        "exclude": list(plan.exclude) if plan else [],
        "leaks": list(plan.leaks) if plan else [],
        "stale_files": stale_files,
        "plain": plain,
        "note": note,
        "can_run": targets.code(name) is not None,
        "problem": problem,
    }


def _median(values: Iterable[Any]) -> float | None:
    found = [float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
    return round(statistics.median(found), 1) if found else None


def timings(plan: Plan | None, runs: Iterable[Mapping[str, Any]]) -> dict[str, float | None]:
    """Rough seconds for a run on copies, from what was seen last: starting the copies and putting
    one back before a try (the newest run *runs*, newest first, played on copies), and rehearsing
    the plan (its report). ``None`` where nothing was seen."""
    start = restore = None
    for run in runs:
        if (run.get("isolation") or {}).get("how") != "proven":
            continue
        tries = [t for e in (run.get("cases") or {}).values() for t in e.get("tries") or []]
        restore = _median((t.get("isolation") or {}).get("restore_seconds") for t in tries)
        start = _median([run.get("copy_start_seconds")])
        break
    rehearsed = _REHEARSED.search(plan.report) if plan else None
    return {
        "start_seconds": start,
        "restore_seconds": restore,
        "rehearse_seconds": round(float(rehearsed.group(1))) if rehearsed else None,
    }


def for_estimate(
    targets: Targets, name: str, how: Mapping[str, Any], runs: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """An estimate's ``isolation`` (how, and the plan's version) with what the Run dialog says of
    it: the plan's status, its stores, and rough timings."""
    plan, _problem = _load(targets, name)
    return {
        **how,
        "status": plan.status if plan else "none",
        "stores": store_words(plan) if plan else [],
        **timings(plan, runs),
    }


def try_stores(targets: Targets, name: str) -> list[dict[str, Any]]:
    """*name*'s plan's stores as the page names them, for a try's ``checked`` ids; none when it
    has no plan that reads (an older run's ids then show as they are)."""
    plan, _problem = _load(targets, name)
    return store_words(plan) if plan else []
