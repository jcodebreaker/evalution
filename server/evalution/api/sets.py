"""The test sets of a target, and the stateful test library."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from evalution.api.deps import Current, invalid, known_set, not_found, target_name
from evalution.project import library_fit
from evalution.suite import library
from evalution.suite.cases import CaseError
from evalution.suite.store import list_sets, save_pass_marks, set_bundle


def sets_router() -> APIRouter:
    router = APIRouter()

    @router.get("/library")
    async def get_library(ws: Current, target: str | None = None) -> dict[str, Any]:
        """The stateful test library, each test with its fit for the target."""
        targets = ws.targets
        name = target_name(targets, target)
        try:
            return {"data": await asyncio.to_thread(library_fit.for_target, targets, name)}
        except library.LibraryError as exc:
            raise invalid(str(exc)) from exc

    @router.get("/evals/sets")
    async def get_sets(ws: Current, target: str | None = None) -> dict[str, Any]:
        """Sets with their case count and latest run, of one target or all."""
        targets, runs = ws.targets, ws.runs
        await runs.recover()
        names = [target_name(targets, target)] if target else targets.names()
        rows = [row for name in names for row in await asyncio.to_thread(runs.set_rows, name)]
        return {"data": rows}

    @router.get("/evals/sets/export")
    async def export(
        ws: Current, target: str | None = None, set_name: str | None = Query(None, alias="set")
    ) -> dict[str, Any]:
        """A target's sets (or one) as ``evalution sets export`` writes them, as JSON:
        ``{target, sets: {<set>: {<path>: text | {base64}}}}``. ``evalution run --server`` plays
        these where the app runs."""
        targets = ws.targets
        name = target_name(targets, target)
        if set_name is not None:
            known_set(targets, name, set_name)
        chosen = [set_name] if set_name else list_sets(targets.docs, name)
        try:
            sets = {s: await asyncio.to_thread(set_bundle, targets.docs, name, s) for s in chosen}
        except CaseError as exc:  # gone since it was listed
            raise not_found("Test set") from exc
        return {"target": name, "sets": sets}

    @router.put("/evals/sets/{set_name}/pass-marks")
    async def put_pass_marks(
        ws: Current, set_name: str, body: dict[str, Any], target: str | None = None
    ) -> dict[str, Any]:
        """The tool-call and reply scores a try of this test set needs to pass, ``{tools,
        reply}`` (0 to 1; one left out stays as it is). Every run after this uses them."""
        targets = ws.targets
        name = target_name(targets, target or body.pop("target", None))
        known_set(targets, name, set_name)
        try:
            marks = await asyncio.to_thread(save_pass_marks, targets.docs, name, set_name, body)
        except CaseError as exc:
            raise invalid(str(exc)) from exc
        return {"target": name, "set": set_name, "pass_marks": marks}

    return router
