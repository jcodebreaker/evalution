"""Estimating, starting, reading and stopping runs."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from evalution.api.deps import busy, invalid, not_found, set_root, target_name
from evalution.api.plans import CasePlans
from evalution.core.case import Case
from evalution.core.rules import current, label_of
from evalution.results.compare import comparison, default_base
from evalution.results.record import pass_marks, verdict_kind
from evalution.results.views import case_detail, detail, summary, try_reason
from evalution.review import testplan
from evalution.runs.runner import RunBusyError, Runs
from evalution.suite.cases import CaseError, load_case, parse_case


def runs_router(runs: Runs, plans: CasePlans) -> APIRouter:
    router = APIRouter()
    targets = runs.targets

    @router.get("/evals/runs")
    async def get_runs(
        target: str | None = None, set_name: str | None = Query(None, alias="set")
    ) -> dict[str, Any]:
        """Runs, newest first, of one target and set, or all."""
        await runs.recover()
        found = await asyncio.to_thread(runs.all, target, set_name)
        return {"data": [summary(r) for r in found]}

    @router.post("/evals/runs/estimate")
    async def estimate(body: dict[str, Any]) -> dict[str, Any]:
        """Tries, chats and the estimated cost for a set, one case or some, and the token
        that starts the run."""
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
            isinstance(tries, bool) or not isinstance(tries, int) or not 1 <= tries <= 20
        ):
            raise invalid("tries: must be a whole number from 1 to 20")
        set_root(targets, name, set_name)
        try:
            return await asyncio.to_thread(runs.estimate, name, set_name, cases, tries)
        except CaseError as exc:
            raise invalid(str(exc)) from exc

    @router.post("/evals/runs", status_code=202)
    async def start_run(body: dict[str, Any]) -> dict[str, Any]:
        """Start the run an estimate described, graded with its set's pass marks (pass marks
        sent here are ignored). Refused without its token, or while another run is going."""
        await runs.recover()
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise invalid("token: ask for an estimate first")
        try:
            return await runs.start(token)
        except RunBusyError as exc:
            raise busy("A run is already going") from exc
        except KeyError as exc:
            raise invalid("token: unknown or expired; ask for a new estimate") from exc
        except CaseError as exc:
            raise invalid(str(exc)) from exc

    @router.get("/evals/runs/{run}")
    async def get_run(run: str, base: str | None = None) -> dict[str, Any]:
        await runs.recover()
        doc = await asyncio.to_thread(runs.load, run)
        if doc is None:
            raise not_found("Run")
        others = await asyncio.to_thread(runs.all, doc["target"], doc["set"])
        if base and not any(r["run"] == base for r in others):
            raise not_found("Base run")
        return detail(doc, others, base)

    @router.get("/evals/runs/{run}/comparison")
    async def compare_runs(run: str, base: str | None = None) -> dict[str, Any]:
        """*run* against *base*, by default the newest earlier run on another setup. ``base`` is
        null when there's nothing to compare it with. Any run will do, another app's too."""
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
    async def get_run_case(run: str, case: str) -> dict[str, Any]:
        doc = await asyncio.to_thread(runs.load, run)
        found = case_detail(doc, case) if doc else None
        if found is None:
            raise not_found("Test")
        return found

    @router.get("/evals/runs/{run}/cases/{case}/compare")
    async def compare_case(
        run: str, case: str, n: int | None = Query(None, alias="try")
    ) -> dict[str, Any]:
        """The case's plan with one try of *run* laid over it: each check's outcome, the
        trajectory and the reply against a good one. The first failed try when none is asked."""
        doc = await asyncio.to_thread(runs.load, run)
        entry = (doc or {}).get("cases", {}).get(case) if doc else None
        if doc is None or entry is None:
            raise not_found("Test")
        name, set_name = doc["target"], doc["set"]
        root = targets.sets(name)
        try:
            found: Case | None = load_case(set_name, case, root)
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
            plan = testplan.mechanical(found, root / set_name, {}, pass_marks(doc))
        tries = entry.get("tries") or []
        reasons = {t["n"]: try_reason(t) for t in tries}
        if not tries:
            return {**plan, "run": run, "verdict": None, "tries": [], "try": None}
        try:
            shown = testplan.compare(plan, entry, n, reasons, pass_marks(doc))
        except LookupError as exc:
            raise not_found("Try") from exc
        one = next(t for t in (case_detail(doc, case) or {})["tries"] if t["n"] == shown["try"])
        return {
            **shown,
            "run": run,
            "verdict": verdict_kind(entry),
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
    async def analyze_run(run: str) -> dict[str, Any]:
        """Analyse a run again: a retry after ``analysis_error`` (analysis is automatic)."""
        try:
            started = await runs.analyze(run)
        except RunBusyError as exc:
            raise busy("A run or an analysis is already going") from exc
        if started is None:
            raise not_found("Run")
        return started

    @router.post("/evals/runs/{run}/stop")
    async def stop_run(run: str) -> dict[str, Any]:
        stopped = await runs.stop(run)
        if stopped is None:
            raise not_found("Run")
        return stopped

    return router
