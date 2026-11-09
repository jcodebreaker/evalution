"""Estimating, starting, reading and stopping runs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from evalution.api import isolation_words
from evalution.api.deps import (
    Current,
    busy,
    invalid,
    known_set,
    not_found,
    read_body,
    target_name,
)
from evalution.core.case import MOST_TRIES, Case
from evalution.core.evidence import TryEvidence
from evalution.core.judgement import Outcome
from evalution.core.rules import current, label_of
from evalution.results.compare import comparison, default_base
from evalution.results.record import pass_marks, verdict_kind
from evalution.results.store import RunStore
from evalution.results.upload import UploadError, read_upload
from evalution.results.views import case_detail, detail, summary, try_reason
from evalution.review import testplan
from evalution.runs import trust
from evalution.runs.isolated import NotIsolated
from evalution.runs.page.target import NO_VIDEO, page_lacks
from evalution.runs.runner import RunBusyError
from evalution.storage.files import FileNameError, Files
from evalution.suite.cases import CaseError, parse_case
from evalution.suite.store import load_case, sent_files

#: The most an uploaded run and its evidence may weigh: each try's texts are capped at 64 kB an
#: item, so this holds a few hundred long tries.
UPLOAD_BYTES = 32 * 2**20


#: What a kept file is served as, by its ending; anything else is bytes to save, not to show.
_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webm": "video/webm"}


def _kept(store: RunStore, run: str, case: str, n: int) -> TryEvidence | None:
    """What was kept of one try; ``None`` when nothing was."""
    return next((e for e in store.evidence(run) if e.case == case and e.n == n), None)


def file_url(run: str, case: str, n: int) -> Callable[[str], str]:
    """Where the page reads one of try *n*'s kept files, by its name."""
    base = f"/v1/evals/runs/{quote(run, safe='')}/cases/{quote(case, safe='')}/tries/{n}/files"
    return lambda name: f"{base}/{quote(name, safe='')}"


def _here(files: Files | None, run: str, case: str, n: int) -> frozenset[str]:
    """The try's files this server holds: none hosted, nor for a run played elsewhere."""
    if files is None:
        return frozenset()
    try:
        return frozenset(files.list(run, case, n))
    except FileNameError:
        return frozenset()


def _read(files: Files, run: str, case: str, n: int, name: str) -> bytes:
    with files.open(run, case, n, name) as found:
        return found.read()


def runs_router() -> APIRouter:
    router = APIRouter()

    @router.get("/evals/runs")
    async def get_runs(
        ws: Current, target: str | None = None, set_name: str | None = Query(None, alias="set")
    ) -> dict[str, Any]:
        """Runs, newest first, of one target and set, or all."""
        runs = ws.runs
        await runs.recover()
        found = await asyncio.to_thread(runs.all, target, set_name)
        return {"data": [summary(r) for r in found]}

    @router.post("/evals/runs/estimate")
    async def estimate(ws: Current, body: dict[str, Any]) -> dict[str, Any]:
        """Tries and chats (a range: :meth:`Runs.estimate`) for a set, one
        case or some, and the token that starts the run."""
        targets, runs = ws.targets, ws.runs
        name = target_name(targets, body.get("target"))
        set_name, tries = body.get("set"), body.get("tries")
        cases = body.get("cases") or ([body["case"]] if body.get("case") else None)
        if not isinstance(set_name, str) or not set_name:
            raise invalid("set: required")
        if cases is not None and not (
            isinstance(cases, list) and all(isinstance(c, str) for c in cases)
        ):
            raise invalid("cases: must be a list of test ids")
        if tries is not None and (
            isinstance(tries, bool) or not isinstance(tries, int) or not 1 <= tries <= MOST_TRIES
        ):
            raise invalid(f"tries: must be a whole number from 1 to {MOST_TRIES}")
        known_set(targets, name, set_name)
        try:
            found = await asyncio.to_thread(runs.estimate, name, set_name, cases, tries)
        except NotIsolated as exc:
            if exc.missing is None:
                raise invalid(str(exc)) from exc
            # What UI testing lacks, for the Run dialog to offer what fixes it.
            said = {"detail": str(exc), "missing": exc.missing.as_dict()}
            return JSONResponse(said, status_code=400)  # type: ignore[return-value]
        except CaseError as exc:
            raise invalid(str(exc)) from exc
        # What the Run dialog says of how its tries are kept apart, with rough timings.
        found["isolation"] = await asyncio.to_thread(
            lambda: isolation_words.for_estimate(
                targets, name, found.get("isolation") or {}, runs.all(name)
            )
        )
        # The code it will play, and whether the images here look like it (ADR 0011).
        found["version"] = await runs.preview(name)
        return found

    @router.post("/evals/runs", status_code=202)
    async def start_run(ws: Current, body: dict[str, Any]) -> dict[str, Any]:
        """Start the run an estimate described, graded with its set's pass marks (pass marks
        sent here are ignored), on the app where ``environment`` names, if it says. Refused
        without its token, or while another run is going."""
        runs = ws.runs
        await runs.recover()
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise invalid("token: ask for an estimate first")
        try:
            return await runs.start(token, env=body.get("environment"))
        except RunBusyError as exc:
            raise busy("A run is already going") from exc
        except KeyError as exc:
            raise invalid("token: unknown or expired; ask for a new estimate") from exc
        except (CaseError, ValueError) as exc:
            raise invalid(str(exc)) from exc

    @router.post("/evals/runs/import")
    async def import_run(ws: Current, request: Request) -> dict[str, Any]:
        """A run played elsewhere, with its evidence (:mod:`evalution.results.upload`), stored
        as this workspace's; sent again, it replaces itself. Its app and set must be here."""
        targets, runs = ws.targets, ws.runs
        body = await read_body(request, UPLOAD_BYTES)
        try:
            # Every view of every case is read once to check it: off the event loop.
            doc, kept = await asyncio.to_thread(read_upload, body)
        except UploadError as exc:
            raise invalid(str(exc)) from exc
        try:
            targets.conf(doc["target"])
        except LookupError as exc:
            raise HTTPException(
                404, "App not found: create the app in the workspace first"
            ) from exc
        known_set(targets, doc["target"], doc["set"])
        before = await asyncio.to_thread(runs.load, doc["run"])
        if before is not None and before["environment"]["where"] != "local":
            raise busy("A run played here has that id")
        await asyncio.to_thread(runs.store.replace, doc, kept)
        return summary(doc)

    @router.get("/evals/runs/{run}")
    async def get_run(ws: Current, run: str, base: str | None = None) -> dict[str, Any]:
        runs = ws.runs
        await runs.recover()
        doc = await asyncio.to_thread(runs.load, run)
        if doc is None:
            raise not_found("Run")
        others = await asyncio.to_thread(runs.all, doc["target"], doc["set"])
        if base and not any(r["run"] == base for r in others):
            raise not_found("Base run")
        return detail(doc, others, base)

    @router.get("/evals/runs/{run}/comparison")
    async def compare_runs(ws: Current, run: str, base: str | None = None) -> dict[str, Any]:
        """*run* against *base*, by default the newest earlier run on another setup. ``base`` is
        null when there's nothing to compare it with. Any run will do, another app's too."""
        runs = ws.runs
        doc = await asyncio.to_thread(runs.load, run)
        if doc is None:
            raise not_found("Run")
        others = await asyncio.to_thread(runs.all, doc["target"], doc["set"])
        chosen = base or default_base(doc, others)
        if chosen is None:
            return {"run": run, "base": None}
        other = await asyncio.to_thread(runs.load, chosen)
        if other is None:
            raise not_found("Base run")
        return comparison(doc, other)

    @router.get("/evals/runs/{run}/cases/{case}")
    async def get_run_case(ws: Current, run: str, case: str) -> dict[str, Any]:
        runs = ws.runs
        doc = await asyncio.to_thread(runs.load, run)
        found = case_detail(doc, case) if doc else None
        if doc is None or found is None:
            raise not_found("Test")
        await asyncio.to_thread(trust.case_view, ws.targets.docs, doc, case, found)
        return found

    @router.get("/evals/runs/{run}/cases/{case}/tries/{n}/files/{name}")
    async def get_try_file(ws: Current, run: str, case: str, n: int, name: str) -> Response:
        """One file kept with try *n* (ADR 0010): a screenshot or the video of a try played
        through the app's page. None hosted, where no page is played, nor for a run played
        elsewhere, which keeps their names only."""
        files = ws.targets.files
        doc = await asyncio.to_thread(ws.runs.load, run) if files is not None else None
        entry = (doc or {}).get("cases", {}).get(case)
        if files is None or entry is None or all(t.get("n") != n for t in entry.get("tries", [])):
            raise not_found("File")
        try:
            data = await asyncio.to_thread(_read, files, run, case, n, name)
        except (FileNameError, FileNotFoundError, IsADirectoryError) as exc:
            raise not_found("File") from exc
        media = _MEDIA.get(PurePosixPath(name).suffix.lower(), "application/octet-stream")
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"}
        if media == "application/octet-stream":
            headers["Content-Disposition"] = "attachment"
        return Response(data, media_type=media, headers=headers)

    @router.put("/evals/runs/{run}/cases/{case}/tries/{n}/rubric/{line}/label")
    async def put_label(
        ws: Current, run: str, case: str, n: int, line: int, body: dict[str, Any]
    ) -> dict[str, Any]:
        """The person agrees, or not, with what the grader says of rubric line *line* (its place
        in the try) on try *n*: a labelled example. The line's agreement after it."""
        agree = body.get("agree")
        if not isinstance(agree, bool):
            raise invalid("agree: must be true or false")
        try:
            return await trust.label(ws.runs.store, ws.targets.docs, run, case, n, line, agree)
        except LookupError as exc:
            raise not_found("Rubric line") from exc
        except ValueError as exc:
            raise invalid(str(exc)) from exc

    @router.delete("/evals/runs/{run}/cases/{case}/tries/{n}/rubric/{line}/label")
    async def delete_label(ws: Current, run: str, case: str, n: int, line: int) -> dict[str, Any]:
        """Forget the person's answer on that line. The line's agreement after it."""
        try:
            return await trust.unlabel(ws.runs.store, ws.targets.docs, run, case, n, line)
        except LookupError as exc:
            raise not_found("Answer") from exc
        except ValueError as exc:
            raise invalid(str(exc)) from exc

    @router.get("/evals/runs/{run}/cases/{case}/compare")
    async def compare_case(
        ws: Current, run: str, case: str, n: int | None = Query(None, alias="try")
    ) -> dict[str, Any]:
        """The case's plan with one try of *run* laid over it: each check's outcome, the
        trajectory and the reply against a good one. The first failed try when none is asked."""
        targets, runs, plans = ws.targets, ws.runs, ws.plans
        doc = await asyncio.to_thread(runs.load, run)
        entry = (doc or {}).get("cases", {}).get(case) if doc else None
        if doc is None or entry is None:
            raise not_found("Test")
        name, set_name = doc["target"], doc["set"]
        try:
            found: Case | None = load_case(targets.docs, name, set_name, case)
        except CaseError:
            found = None
        played = (doc.get("case_versions") or {}).get(case)
        if found is not None and found.version == played:
            plan = await plans.plan(name, set_name, found, pass_marks(doc))
        else:
            # The file changed since this run (or is gone): the run's own copy, in plain words.
            try:
                found = parse_case(
                    {
                        "name": entry["name"],
                        "turns": entry["turns"] or [{"message": "(no message)"}],
                        "tools": entry.get("expected") or {},
                        "reply": (entry.get("case") or {}).get("reply")
                        or {"rubric": entry.get("reply_rubric") or []},
                    },
                    f"{run}/{case}",
                    case,
                )
            except CaseError as exc:
                raise invalid(str(exc)) from exc
            files = sent_files(targets.docs, name, set_name, found)
            plan = testplan.mechanical(found, files, {}, pass_marks(doc))
        tries = entry.get("tries") or []
        reasons = {t["n"]: try_reason(t) for t in tries}
        if not tries:
            return {**plan, "run": run, "verdict": None, "tries": [], "try": None}
        try:
            held = (t["n"] for t in tries if current(t).outcome is not Outcome.PASSED)
            wanted = n or next(held, tries[0]["n"])
            kept = await asyncio.to_thread(_kept, runs.store, run, case, wanted)
            here = await asyncio.to_thread(_here, targets.files, run, case, wanted)
            shown = testplan.compare(
                plan,
                entry,
                n,
                reasons,
                pass_marks(doc),
                [turn.reply.text for turn in kept.turns] if kept else [],
                kept,
                lambda name: file_url(run, case, wanted)(name) if name in here else None,
            )
        except LookupError as exc:
            raise not_found("Try") from exc
        one = next(t for t in (case_detail(doc, case) or {})["tries"] if t["n"] == shown["try"])
        rubric = {
            item["id"]: item["text"]
            for step in shown["steps"]
            for item in step["items"]
            if item["id"].startswith("rubric-")
        }
        rows = await asyncio.to_thread(
            trust.lines_view, targets.docs, doc, case, list(rubric.values()), shown["try"]
        )
        # The plan's stores in plain words, for the ids the try's ``isolation.checked`` names.
        stores = await asyncio.to_thread(isolation_words.try_stores, targets, name)
        page = shown.get("page")
        if page is not None and page.get("video_missing") == NO_VIDEO:
            # No video for want of ffmpeg: what installs it, while it's still missing here.
            lacks = await asyncio.to_thread(page_lacks, True)
            page["video_fix"] = next((m.as_dict() for m in lacks if m.part == "ffmpeg"), None)
        return {
            **shown,
            "isolation": {**shown["isolation"], "stores": stores},
            "agreement": dict(zip(rubric, rows, strict=True)),
            "run": run,
            "verdict": verdict_kind(entry),
            "divergence": entry.get("divergence"),
            "layers": one.get("layers"),
            "tries": [
                {
                    "n": t["n"],
                    "outcome": current(t).outcome.value,
                    "reason": reasons[t["n"]],
                    "label": label_of([current(t)]),
                }
                for t in tries
            ],
            "detail": {
                k: one.get(k)
                for k in ("timeline", "context", "trajectory", "tokens", "error", "analysis")
            },
        }

    @router.post("/evals/runs/{run}/analyze")
    async def analyze_run(ws: Current, run: str) -> dict[str, Any]:
        """Analyse a run again: a retry after ``analysis_error`` (analysis is automatic)."""
        runs = ws.runs
        try:
            started = await runs.analyze(run)
        except RunBusyError as exc:
            raise busy("A run or an analysis is already going") from exc
        if started is None:
            raise not_found("Run")
        return started

    @router.post("/evals/runs/{run}/stop")
    async def stop_run(ws: Current, run: str) -> dict[str, Any]:
        runs = ws.runs
        stopped = await runs.stop(run)
        if stopped is None:
            raise not_found("Run")
        return stopped

    return router
