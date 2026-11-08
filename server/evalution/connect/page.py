"""How tests reach a target: through its API, or through its own page by a map the test writer
learned and a person approved once (ADR 0010, after v1).

:func:`approve` saves the map tried on evalution's copy into the target's ``page:`` block, the way
the page saves any connected target (:func:`evalution.config.save_page_target`), and keeps it as a
new version of the map in use (:class:`~evalution.project.pagemaps.PageMapStore`), so
:func:`undo` can go back. A target set in ``evalution.yaml`` is never written: its owner adds the
block there. :func:`view` is what the page shows of it all.
"""

from __future__ import annotations

from typing import Any

from evalution.config import ConfigError, page_targets, save_page_target
from evalution.project import pagemaps
from evalution.project.pagemaps import KeptMap
from evalution.runs.page.learn import lacking, refusal
from evalution.runs.page.map import as_block, described, page_only, read_map
from evalution.targets import Targets

#: What a map written into the ``page:`` block by hand is kept as, when an approved one replaces
#: it: the version before, so undoing comes back to it.
BY_HAND = "Written by hand in the target's page: block."


class PageMapError(ValueError):
    """The map can't be approved or undone here; the message says why, in plain words."""


def _plays(targets: Targets, name: str, block: Any) -> str:
    """``api``, ``page`` (its API, its page beside it) or ``page_only`` (its page alone)."""
    if page_only(targets.conf(name)):
        return "page_only"
    return "page" if block is not None else "api"


def _block(targets: Targets, name: str) -> dict[str, Any]:
    """*name*'s block as the page saved it.

    :raises PageMapError: It's set in the config file, which is never written.
    """
    if name not in targets.config.managed:
        raise PageMapError(
            f"{name} is set in {targets.config.file.name}, which evalution never writes: add the "
            "map to its page: block there."
        )
    return dict(page_targets(targets.docs).get(name) or {})


def approve(targets: Targets, name: str) -> KeptMap:
    """Save the map waiting for approval into *name*'s ``page:`` block, status ``approved``, as a
    new version of the map in use. Call :meth:`Targets.reload` after.

    :raises LookupError: No map is waiting.
    :raises PageMapError: The map doesn't read, or the target can't be written from here.
    """
    kept_maps = pagemaps.store(targets)
    kept = kept_maps.proposed(name)
    if kept is None:
        raise LookupError("No tried page map is waiting for approval.")
    block = _block(targets, name)
    try:
        page = read_map(kept.page)
    except ConfigError as exc:
        raise PageMapError(f"The tried map doesn't read: {exc}") from exc
    before = block.get("page")
    if isinstance(before, dict) and pagemaps.in_use(targets, name) is None:
        # A map written by hand before: kept as the version this one replaces.
        kept_maps.save(KeptMap(name, before, status="approved", report=BY_HAND))
    approved = {**as_block(page), "status": "approved"}
    save_page_target(targets.docs, name, {**block, "page": approved})
    kept.page, kept.status = approved, "approved"
    if (code := targets.code(name)) is not None:
        kept.evidence_hashes = pagemaps.hashes_of(approved, code.root.resolve())
    kept_maps.save(kept)
    kept_maps.withdraw(name)
    return kept


def undo(targets: Targets, name: str) -> KeptMap | None:
    """Go back to the map in use before this one, into *name*'s ``page:`` block; with none before
    it, the block goes and tests reach the app through its API again. Call
    :meth:`Targets.reload` after.

    :raises LookupError: No map was approved here.
    :raises PageMapError: The target can't be written from here.
    """
    block = _block(targets, name)
    if page_only(targets.conf(name)) and not pagemaps.store(targets).history(name)[1:]:
        raise PageMapError(
            f"{name} is reached through its page alone: there is no API to go back to, and no "
            "map before this one."
        )
    back = pagemaps.store(targets).undo(name)
    if back is None:
        block.pop("page", None)
    else:
        block["page"] = back.page
    save_page_target(targets.docs, name, block)
    return back


def shot_url(name: str, shot: str) -> str:
    """Where the page reads a screenshot of *name*'s page map."""
    return f"/v1/targets/{name}/page/shots/{shot}"


def _shots(name: str, kept: KeptMap) -> list[str]:
    return [shot_url(name, shot) for shot in kept.shots]


def _described(page: Any) -> tuple[list[dict[str, str]], str | None]:
    try:
        return described(read_map(page)), None
    except ConfigError as exc:
        return [], str(exc)


def _kept(name: str, kept: KeptMap) -> dict[str, Any]:
    words, error = _described(kept.page)
    return {
        "status": kept.status,
        "map": words,
        "error": error,
        "report": kept.report,
        "shots": _shots(name, kept),
        "missing": list(kept.missing),
        # The page as the check before a run (or a try) found it, lacking what's missing.
        "missing_shot": shot_url(name, kept.missing_shot)
        if kept.missing_shot and kept.missing_shot_at
        else None,
        "stale_files": list(kept.stale_files),
        "updated": kept.updated,
    }


def view(targets: Targets, name: str) -> dict[str, Any]:
    """How tests reach *name*, as the page shows it: its API or its page; the map in use and how
    it stands, or one written by hand; the map waiting for approval with its rehearsal's
    screenshots; the versions kept; and whether the page can be learned here, or why not."""
    kept_maps = pagemaps.store(targets)
    block = pagemaps.block_of(targets, name)
    problem = None
    try:
        waiting = kept_maps.proposed(name)
        history = kept_maps.history(name)
    except ValueError as exc:
        waiting, history = None, []
        problem = f"The kept page map doesn't read: {exc}"
    used = pagemaps.in_use(targets, name)
    if used is not None:
        current: dict[str, Any] | None = {**_kept(name, used), "by_hand": False}
    elif block is not None:
        words, error = _described(block)
        current = {
            "status": "by_hand",
            "map": words,
            "error": error,
            "report": BY_HAND,
            "shots": [],
            "missing": [],
            "missing_shot": None,
            "stale_files": [],
            "updated": None,
            "by_hand": True,
        }
    else:
        current = None
    missing = None
    try:
        capabilities = targets.adapter(name).capabilities
        why_not = refusal(targets.config, name, capabilities)
        if why_not is not None:
            missing = lacking(targets.config, name, capabilities)
    except Exception as exc:  # an adapter that can't be made can't be played through its page
        why_not = f"{name}'s adapter can't be made: {exc}"
    return {
        "target": name,
        "plays": _plays(targets, name, block),
        "managed": name in targets.config.managed,
        "current": current,
        "proposed": _kept(name, waiting) if waiting is not None else None,
        "history": [
            {"version": n, "status": kept.status, "updated": kept.updated} for n, kept in history
        ],
        "can_undo": bool(history) and name in targets.config.managed,
        "can_learn": why_not is None,
        "why_not": why_not,
        # What UI testing lacks, when that's why not, with what fixes it (ADR 0010).
        "missing": missing.as_dict() if missing is not None else None,
        "problem": problem,
    }
