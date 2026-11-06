"""The test sets of a target, and the stateful test library."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from evalution.api.deps import invalid, known_set, target_name
from evalution.project import library_fit
from evalution.runs.runner import Runs
from evalution.suite import library
from evalution.suite.cases import CaseError
from evalution.suite.store import save_pass_marks


def sets_router(runs: Runs) -> APIRouter:
    router = APIRouter()
    targets = runs.targets

    @router.get("/library")
    async def get_library(target: str | None = None) -> dict[str, Any]:
        """The stateful test library, each test with its fit for the target."""
        name = target_name(targets, target)
        try:
            return {"data": await asyncio.to_thread(library_fit.for_target, targets, name)}
        except library.LibraryError as exc:
            raise invalid(str(exc)) from exc

    @router.get("/evals/sets")
    async def get_sets(target: str | None = None) -> dict[str, Any]:
        """Sets with their case count and latest run, of one target or all."""
        await runs.recover()
        names = [target_name(targets, target)] if target else targets.names()
        rows = [row for name in names for row in await asyncio.to_thread(runs.set_rows, name)]
        return {"data": rows}

    @router.put("/evals/sets/{set_name}/pass-marks")
    async def put_pass_marks(
        set_name: str, body: dict[str, Any], target: str | None = None
    ) -> dict[str, Any]:
        """The tool-call and reply scores a try of this test set needs to pass, ``{tools,
        reply}`` (0 to 1; one left out stays as it is). Every run after this uses them."""
        name = target_name(targets, target or body.pop("target", None))
        known_set(targets, name, set_name)
        try:
            marks = await asyncio.to_thread(save_pass_marks, targets.docs, name, set_name, body)
        except CaseError as exc:
            raise invalid(str(exc)) from exc
        return {"target": name, "set": set_name, "pass_marks": marks}

    return router
