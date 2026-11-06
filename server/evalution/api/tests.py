"""The tests of a set: their text, their plans and drafts of new ones."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from evalution.api.deps import Current, invalid, known_set, not_found, target_name
from evalution.project import drift
from evalution.results.views import case_history, finding, latest_rows
from evalution.suite.cases import CaseError
from evalution.suite.store import (
    case_text,
    changes_in_text,
    load_case,
    load_cases,
    load_set,
    write_case_text,
)
from evalution.suite.store import save_case as write_case
from evalution.targets import Targets
from evalution.writer.draft import draft, from_traces, from_try, sources


def _case_text(targets: Targets, target: str, set_name: str, case: str) -> str:
    known_set(targets, target, set_name)
    try:
        return case_text(targets.docs, target, set_name, case)
    except CaseError as exc:
        raise not_found("Test") from exc


def tests_router() -> APIRouter:
    router = APIRouter()

    @router.get("/evals/sets/{set_name}/cases")
    async def get_cases(ws: Current, set_name: str, target: str | None = None) -> dict[str, Any]:
        """Cases with the latest verdict, scores, newest run and stale mark."""
        targets, runs = ws.targets, ws.runs
        name = target_name(targets, target)
        known_set(targets, name, set_name)
        found = drift.read(targets, name)
        try:
            rows = await asyncio.to_thread(
                lambda: latest_rows(
                    load_cases(targets.docs, name, set_name),
                    runs.all(name, set_name),
                    lambda case: drift.stale(case, found),
                )
            )
        except CaseError as exc:
            raise invalid(str(exc)) from exc
        return {"data": rows}

    @router.get("/evals/sets/{set_name}/cases/{case}")
    async def get_case(
        ws: Current, set_name: str, case: str, target: str | None = None
    ) -> dict[str, Any]:
        """A case file, its stale mark and every run that holds it, newest first."""
        targets, runs = ws.targets, ws.runs
        name = target_name(targets, target)
        known_set(targets, name, set_name)
        try:
            found = load_case(targets.docs, name, set_name, case)
        except CaseError as exc:
            raise not_found("Test") from exc
        every = await asyncio.to_thread(runs.all, name, set_name)
        played = next((r for r in every if (r["cases"].get(case) or {}).get("tries")), None)
        return {
            "case": case,
            "file": found.raw,
            "stale": drift.stale(found, drift.read(targets, name)),
            "history": case_history(case, every),
            "finding": finding(played["cases"][case]["tries"]) if played else None,
        }

    @router.get("/evals/sets/{set_name}/cases/{case}/file")
    async def get_case_file(
        ws: Current, set_name: str, case: str, target: str | None = None
    ) -> PlainTextResponse:
        """The test's file as written, to edit by hand."""
        targets = ws.targets
        name = target_name(targets, target)
        text = await asyncio.to_thread(_case_text, targets, name, set_name, case)
        return PlainTextResponse(text, media_type="application/json")

    @router.put("/evals/sets/{set_name}/cases/{case}/file", response_model=None)
    async def put_case_file(
        ws: Current, set_name: str, case: str, request: Request, target: str | None = None
    ) -> dict[str, Any] | JSONResponse:
        """Save the test's file as typed, once it reads as a valid test; the test as saved. A file
        that changes what the test checks is sent back with the difference and a token: sending
        that token in ``X-Reviewed`` saves exactly that text over exactly the file reviewed."""
        targets = ws.targets
        name = target_name(targets, target)
        base = await asyncio.to_thread(_case_text, targets, name, set_name, case)
        try:
            text = (await request.body()).decode("utf-8")
            changes = await asyncio.to_thread(
                changes_in_text, targets.docs, name, set_name, case, text
            )
            token = hashlib.sha256(base.encode() + b"\0" + text.encode()).hexdigest()
            if changes and request.headers.get("x-reviewed") != token:
                return JSONResponse(
                    {
                        "detail": "This changes what the test checks.",
                        "changes": changes,
                        "token": token,
                    },
                    status_code=409,
                )
            found = await asyncio.to_thread(
                write_case_text, targets.docs, name, set_name, case, text
            )
        except UnicodeDecodeError as exc:
            raise invalid("The file must be UTF-8 text") from exc
        except CaseError as exc:
            raise invalid(str(exc)) from exc
        return {"case": found.case, "file": found.raw}

    @router.get("/evals/sets/{set_name}/cases/{case}/plan")
    async def get_case_plan(
        ws: Current, set_name: str, case: str, target: str | None = None
    ) -> dict[str, Any]:
        """What the case checks, step by step, in plain words; no run needed."""
        targets, plans = ws.targets, ws.plans
        name = target_name(targets, target)
        known_set(targets, name, set_name)
        try:
            found = load_case(targets.docs, name, set_name, case)
            eval_set = load_set(targets.docs, name, set_name)
        except CaseError as exc:
            raise not_found("Test") from exc
        return await plans.plan(name, set_name, found, dict(eval_set.pass_marks))

    @router.post("/evals/sets/{set_name}/cases")
    async def save_case(
        ws: Current, set_name: str, body: dict[str, Any], target: str | None = None
    ) -> dict[str, Any]:
        """Save a case into the set; with ``run: true`` also the estimate that starts it."""
        targets, runs = ws.targets, ws.runs
        name = target_name(targets, target or body.pop("target", None))
        known_set(targets, name, set_name)
        body = dict(body)
        run = body.pop("run", False)
        if not isinstance(run, bool):
            raise invalid("run: must be true or false")
        try:
            stem = await asyncio.to_thread(write_case, targets.docs, name, set_name, body)
            if run:
                estimate = await asyncio.to_thread(runs.estimate, name, set_name, [stem], None)
                return {"case": stem, "estimate": estimate}
        except CaseError as exc:
            raise invalid(str(exc)) from exc
        return {"case": stem}

    @router.get("/evals/sources")
    async def get_sources(
        ws: Current, target: str | None = None, limit: int = Query(20, ge=1, le=50)
    ) -> dict[str, Any]:
        """The target's recent traces in Phoenix, newest first."""
        targets = ws.targets
        name = target_name(targets, target)
        phoenix = targets.source(name)
        if phoenix is None:
            return {"data": []}
        return {"data": await sources(phoenix, targets.trace_attribute(name), limit)}

    @router.post("/evals/sets/{set_name}/cases/draft")
    async def draft_case(
        ws: Current, set_name: str, body: dict[str, Any], target: str | None = None
    ) -> dict[str, Any]:
        """A case drafted from a trace or session in Phoenix (``trace_id`` or ``session_id``)
        or from an eval try (``run``, ``case``, ``try``); returned, not saved."""
        targets, runs = ws.targets, ws.runs
        name = target_name(targets, target or body.get("target"))
        known_set(targets, name, set_name)
        if isinstance(body.get("trace_id"), str) or isinstance(body.get("session_id"), str):
            phoenix = targets.source(name)
            if phoenix is None:
                raise invalid("this app has no traces")
            try:
                found = await from_traces(
                    phoenix,
                    targets.trace_attribute(name),
                    body.get("session_id"),
                    body.get("trace_id"),
                )
            except LookupError as exc:
                raise not_found("Conversation") from exc
        elif (
            isinstance(body.get("run"), str)
            and isinstance(body.get("case"), str)
            and isinstance(body.get("try"), int)
        ):
            doc = await asyncio.to_thread(runs.load, body["run"])
            if doc is None:
                raise not_found("Run")
            others = await asyncio.to_thread(runs.all, doc["target"], doc["set"])
            started = sorted(float(r["started_at"]) for r in others)
            number = started.index(float(doc["started_at"])) + 1
            try:
                found = from_try(doc, targets.docs, body["case"], body["try"], number)
            except LookupError as exc:
                raise not_found("Try") from exc
        else:
            raise invalid("give trace_id or session_id, or run, case and try")
        try:
            eval_set = await asyncio.to_thread(load_set, targets.docs, name, set_name)
        except CaseError as exc:
            raise invalid(str(exc)) from exc
        return await draft(runs.caller(eval_set.grader), *found)

    return router
