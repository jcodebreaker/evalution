"""What the routes return of a stored run (``RunSummary``, ``RunDetail``, ``CaseRow``,
``CaseDetail``), read from its document: no files, no models.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from evalution.core.case import Case
from evalution.core.judgement import Basis, CheckKind, Judgement, Outcome, Route
from evalution.core.rules import (
    ADAPTER_BROKEN,
    APP_NOT_READY,
    PAGE_MAP_BROKEN,
    SERVER_RESTARTED,
    TARGET_GONE,
    VERSION_NOT_BUILT,
    called_of,
    comparable,
    current,
    first_reason,
    label_of,
    layers,
)
from evalution.core.version import described
from evalution.results.record import (
    environment_of,
    isolation_of,
    judgements,
    key_of,
    noted,
    originally,
    pass_marks,
    unjudged_reasons,
    verdict_kind,
    verdict_of,
)

HISTORY = 10
_RANK = {"fail": 0, "flaky": 1, "pass": 2}
#: How a run stored before ``failure`` said an app answered with an error.
_OLD_APP_ANSWER = re.compile(r"^\w+: (?:\w+ )?answered \d+")


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def try_reason(attempt: Mapping[str, Any]) -> str | None:
    """Why a try did not pass, in one line, from what was checked; ``None`` for a pass."""
    return first_reason(current(attempt))


def case_row(
    stem: str,
    name: str,
    detail: Mapping[str, Any] | None,
    case: Case | None = None,
    *,
    last: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A ``CaseRow`` from a case's entry in a run (``None``: never run). ``about`` and ``source``
    are the run's copy of the case, else *case*'s; ``about`` falls back to the first message.
    *last* is the newest run that holds the case; ``stale`` is the run's record of it."""
    entry = detail or {}
    tries = list(entry.get("tries") or [])
    turns = entry.get("turns") or ([{"message": t.message} for t in case.turns] if case else [])
    about = entry.get("about") or (case.about if case else None)
    found = judgements(entry)
    return {
        "case": stem,
        "name": name,
        "about": about or (str(turns[0]["message"]) if turns else None),
        "source": entry.get("source") or (case.source if case else None),
        "verdict": verdict_kind(entry),
        "label": label_of(found),
        "basis": verdict_of(entry).basis.value,
        **noted(entry),
        "tries": [j.outcome.value for j in found],
        "labels": [label_of([j]) for j in found],
        "called": [called_of(t) for t in tries],
        "different": _different(entry),
        "reasons": [first_reason(j) for j in found],
        "unjudged": unjudged_reasons(entry),
        "originally": originally(entry),
        "tools": _mean([t["tools"] for t in tries]) if _steps_scored(entry) else None,
        "reply": _mean([t["reply"] for t in tries if t.get("reply") is not None])
        if _reply_scored(entry)
        else None,
        "seconds": _mean([t["seconds"] for t in tries]),
        "tokens": round(statistics.fmean([t["tokens"] for t in tries])) if tries else None,
        "last_run": last["run"] if last else None,
        "last_at": float(last["started_at"]) if last else None,
        "stale": entry.get("stale"),
        # The layers of the try the test's page opens on (the first that didn't pass, else the
        # first), and how that try played: through the app's page when it has the page's checks.
        "layers": _row_layers(shown) if (shown := _shown_try(found)) else None,
        "plays": _played(found, last),
    }


def _row_layers(found: Judgement) -> dict[str, str]:
    """A row's layers as the try's page says them: ``shown`` is what showed of the replies alone,
    and ``page_health`` the page's own health (``absent`` when it wasn't checked), apart."""
    said = {Outcome.FAILED: "fail", Outcome.UNJUDGED: "unjudged", Outcome.PASSED: "pass"}
    health = next((c for c in found.checks if c.kind is CheckKind.PAGE_HEALTH), None)
    return {
        **layers(found, health=False),
        "page_health": said[health.outcome] if health is not None else "absent",
    }


def _shown_try(found: Sequence[Judgement]) -> Judgement | None:
    """The try a test's page opens on: the first that didn't pass, else the first."""
    return next((j for j in found if j.outcome is not Outcome.PASSED), found[0] if found else None)


def _played(found: Sequence[Judgement], run: Mapping[str, Any] | None) -> str:
    """``page`` when a try has the page's checks (ADR 0010), or *run* played through the page;
    else ``api``."""
    if any(c.kind in (CheckKind.SHOWN, CheckKind.PAGE_HEALTH) for j in found for c in j.checks):
        return "page"
    return "page" if run and run.get("plays") in ("page", "page_only") else "api"


def _steps_scored(entry: Mapping[str, Any]) -> bool:
    """Whether a test's steps score counts anything. With nothing expected or forbidden it is 1.0
    whatever the agent did, which is no result. Stored without its copy, it may have some."""
    tools = entry.get("expected")
    if tools is None:
        return True
    return bool(
        tools.get("expected")
        or tools.get("never")
        or tools.get("extras") is False
        or tools.get("order") == "exact"
    )


def _reply_scored(entry: Mapping[str, Any]) -> bool:
    """Whether a test's reply score came from a grader. With nothing to grade it is 1.0, which is no
    result; a test stored without its copy may still have had a reference."""
    if entry.get("reply_rubric") or "case" not in entry:
        return True
    return bool(((entry["case"] or {}).get("reply") or {}).get("reference"))


def _different(entry: Mapping[str, Any]) -> int:
    """Tries that got a good result by another route than expected."""
    return sum(
        1 for j in judgements(entry) if j.outcome is Outcome.PASSED and j.route is Route.DIFFERENT
    )


def totals(run: Mapping[str, Any]) -> dict[str, Any]:
    """The run's ``Totals``: cases passed, those by what they rest on, those no one could judge,
    mean scores over cases, median try time, sums."""
    rows = [case_row(k, c["name"], c) for k, c in run["cases"].items()]
    tries = [t for c in run["cases"].values() for t in c["tries"]]
    passed = [r for r in rows if r["verdict"] == "pass"]
    return {
        "passed": len(passed),
        "passed_checked": sum(r["basis"] == Basis.CHECKED for r in passed),
        "passed_model_only": sum(r["basis"] == Basis.MODEL_ONLY for r in passed),
        "unjudged": sum(1 for r in rows if r["verdict"] == "unjudged"),
        "cases": len(rows),
        "tools": _mean([r["tools"] for r in rows if r["tools"] is not None]),
        "reply": _mean([r["reply"] for r in rows if r["reply"] is not None]),
        "median_seconds": statistics.median([t["seconds"] for t in tries]) if tries else 0.0,
        "tokens": sum(t["tokens"] for t in tries),
    }


_SUMMARY_KEYS = (
    "run",
    "target",
    "set",
    "started_at",
    "ended_at",
    "status",
    "prompt",
    "commit",
    "totals",
    "scope",
    "error",
    "stop_reason",
    "failure",
    "rules",
    "judge",
)


def _why_not_played(run: Mapping[str, Any]) -> str | None:
    """Why a test has no result, in plain words; ``None`` when it was just stopped."""
    if run.get("status") != "failed":
        return None
    if _interrupted(run):
        return "the server restarted"
    if run.get("failure") == ADAPTER_BROKEN:
        return "the adapter isn't reading the app"
    if run.get("failure") == PAGE_MAP_BROKEN:
        return "the page lacks what its page map names"
    if run.get("failure") == VERSION_NOT_BUILT:
        return "its version wasn't checked out or built"
    return "the app wasn't ready" if _app_not_ready(run) else "it stopped early"


def _interrupted(run: Mapping[str, Any]) -> bool:
    """The server restarted under it. One stored before that read ``stopped`` says ``failed``."""
    return run.get("stop_reason") == SERVER_RESTARTED


def _app_not_ready(run: Mapping[str, Any]) -> bool:
    """What the failed *run* recorded; a run stored before ``failure`` only has its error text."""
    if run.get("failure"):
        return run["failure"] == APP_NOT_READY
    error = str(run.get("error") or "")
    return (
        TARGET_GONE in (error, run.get("stop_reason"))
        or error.startswith(("Connect", "ReadTimeout"))
        or _OLD_APP_ANSWER.match(error) is not None
    )


def _run_test(run: Mapping[str, Any], stem: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """How one test of a run went."""
    found = verdict_of(entry)
    kind = verdict_kind(entry)
    return {
        "case": stem,
        "name": entry.get("name") or stem,
        "verdict": kind,
        "label": label_of(judgements(entry)),
        "basis": found.basis.value,
        **noted(entry),
        "reason": _why_not_played(run) if kind is None else None,
        "unjudged": unjudged_reasons(entry),
        "originally": originally(entry),
        "passed": found.passed,
        "different": _different(entry),
        "tries": found.judged,
    }


def _version(run: Mapping[str, Any]) -> dict[str, Any] | None:
    found = run.get("version")
    return {**found, "said": described(found)} if found else None


def summary(run: Mapping[str, Any]) -> dict[str, Any]:
    """The ``RunSummary`` of *run*."""
    return {
        **{key: run.get(key) for key in _SUMMARY_KEYS},
        # A run stored before these were kept played all of its set, with no prompt on record.
        "scope": run.get("scope") or "all",
        "prompt": run.get("prompt") or "",
        "pass_marks": pass_marks(run),
        "environment": dict(zip(("where", "name"), environment_of(run), strict=True)),
        # How its tries were kept apart (ADR 0009): on a copy put back by a proven plan, or not.
        "isolation": isolation_of(run, run=True),
        # The code it played (ADR 0011), with the line that says it; ``None`` before it knew.
        "version": _version(run),
        # How it reached the app: ``api``, ``page`` (its API with its page beside it) or
        # ``page_only`` (its page alone, every reply what the page showed); a run stored before
        # this was kept says nothing, and reads as it did.
        **({"plays": run["plays"]} if run.get("plays") else {}),
        # When a run asked for while another played was queued; a run that never waited says
        # nothing.
        **({"queued_at": run["queued_at"]} if run.get("queued_at") else {}),
        "case_ids": list(run["cases"]),
        "tests": [_run_test(run, k, c) for k, c in run["cases"].items()],
        "analyzing": bool(run.get("analyzing")),
        "analysis_error": run.get("analysis_error"),
        "interrupted": _interrupted(run),
        "progress": progress(run),
    }


def progress(run: Mapping[str, Any]) -> dict[str, Any]:
    """Tries and tests played of those planned, and what is being played while running."""
    cases = list(run["cases"].values())
    current = run.get("current") if run["status"] == "running" else None

    def planned(c: Mapping[str, Any]) -> int:
        return int(c.get("planned") or len(c["tries"]))

    return {
        "done": sum(len(c["tries"]) for c in cases),
        "total": sum(planned(c) for c in cases),
        "tests_done": sum(1 for c in cases if c["tries"] and len(c["tries"]) >= planned(c)),
        "tests_total": len(cases),
        "current": current or None,
    }


def _pct(now: float, before: float) -> float:
    return round((now - before) / before * 100, 1) if before else 0.0


def _moved(now: float | None, before: float | None) -> float:
    return round(now - before, 3) if now is not None and before is not None else 0.0


def delta(run: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """*run* against *base*: cases whose verdict got better or worse, and the moves. Only results
    that can be compared count; ``changed`` are those where the test or the rules changed."""
    better = worse = changed = 0
    for stem, detail in run["cases"].items():
        now = verdict_kind(detail)
        old_detail = base["cases"].get(stem)
        old = verdict_kind(old_detail) if old_detail else None
        if now not in _RANK or old not in _RANK:
            continue
        if not comparable(key_of(run, stem), key_of(base, stem)):
            changed += 1
            continue
        better += _RANK[now] > _RANK[old]
        worse += _RANK[now] < _RANK[old]
    a, b = run["totals"], base["totals"]
    return {
        "better": better,
        "worse": worse,
        "changed": changed,
        "tools": _moved(a["tools"], b["tools"]),
        "reply": _moved(a["reply"], b["reply"]),
        "median_seconds_pct": _pct(a["median_seconds"], b["median_seconds"]),
    }


def detail(
    run: Mapping[str, Any], others: Sequence[Mapping[str, Any]], base_id: str | None
) -> dict[str, Any]:
    """The ``RunDetail`` of *run*; *others* are the set's runs, newest first. The base is
    *base_id*, else the run before this one where it played."""
    here = environment_of(run)
    earlier = [
        r
        for r in others
        if float(r["started_at"]) < float(run["started_at"]) and environment_of(r) == here
    ]
    base = next((r for r in others if r["run"] == base_id), None) if base_id else None
    if base is None and not base_id and earlier:
        base = earlier[0]
    upto = [r for r in others if float(r["started_at"]) <= float(run["started_at"])]
    stems = list(run["cases"])
    history = [
        {
            "run": r["run"],
            "prompt": r.get("prompt") or "",
            "verdicts": {
                s: verdict_kind(r["cases"][s]) if s in r["cases"] else None for s in stems
            },
        }
        for r in upto[:HISTORY]
    ]
    return {
        **summary(run),
        "cases": [case_row(k, c["name"], c, last=run) for k, c in run["cases"].items()],
        "base": summary(base) if base else None,
        "delta": delta(run, base) if base else None,
        "history": history,
        "analysis": run.get("analysis") if run["status"] != "running" else None,
    }


#: What a stored try keeps that the API's ``Try`` doesn't show.
#: A try's own fields the API doesn't show; ``usd`` is what an older run recorded it cost.
_NOT_IN_A_TRY = ("calls", "messages", "trace_ids", "judgements", "legacy_passed", "usd")


def _judged(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """A try's current judgement, as the API shows it."""
    found = current(attempt)
    return {
        "outcome": found.outcome.value,
        "passed": found.outcome is Outcome.PASSED,
        "basis": found.basis.value,
        "route": found.route.value,
        "different_route": found.outcome is Outcome.PASSED and found.route is Route.DIFFERENT,
        "checks": [c.model_dump(mode="json") for c in found.checks],
        # Which layer held: stored, answered, shown (ADR 0010).
        "layers": layers(found),
    }


def report_facts(run: Mapping[str, Any]) -> dict[str, Any]:
    """What a run came to, from its checks: the report's facts, no model in them."""
    tests = [(k, c, verdict_kind(c), verdict_of(c)) for k, c in run["cases"].items()]
    played = [t for t in tests if t[2] is not None]
    passed = [t for t in played if t[2] == "pass"]
    return {
        "passed": len(passed),
        "total": len(played),
        "model_only": sum(t[3].basis is Basis.MODEL_ONLY for t in passed),
        "held": [
            {
                "case": stem,
                "name": entry.get("name") or stem,
                "passed": found.passed,
                "judged": found.judged,
                "basis": found.basis.value,
                **noted(entry),
            }
            for stem, entry, _, found in passed
        ],
        "failed": [
            {
                "case": stem,
                "name": entry.get("name") or stem,
                "verdict": kind,
                "label": label_of(judgements(entry)),
                "reason": next((r for j in judgements(entry) if (r := first_reason(j))), None),
            }
            for stem, entry, kind, _ in played
            if kind in ("fail", "flaky")
        ],
        "unjudged": [
            {
                "case": stem,
                "name": entry.get("name") or stem,
                "label": label_of(judgements(entry)),
                "reasons": unjudged_reasons(entry),
            }
            for stem, entry, kind, _ in played
            if kind == "unjudged"
        ],
    }


def finding(tries: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Why the test failed in this run: the analysis of its first analysed failed try, as
    ``{summary, cause, fix, suspected}``: the model's reading; ``None`` when it passed or is not
    analysed (yet)."""
    for attempt in tries:
        found = attempt.get("analysis")
        if current(attempt).outcome is Outcome.FAILED and found:
            return {k: found.get(k) for k in ("summary", "cause", "fix", "suspected")}
    return None


def _automatic_rows(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """A try's timeline and trajectory with the calls that weren't scored marked."""
    ids = set(attempt.get("automatic") or ())
    if not ids:
        return {}

    def marked(rows: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
        return [{**r, "automatic": True} if r.get(key) in ids else dict(r) for r in rows]

    return {
        "timeline": marked(attempt.get("timeline") or [], "id"),
        "trajectory": marked(attempt.get("trajectory") or [], "span"),
    }


def case_detail(run: Mapping[str, Any], stem: str) -> dict[str, Any] | None:
    """The ``CaseDetail`` of case *stem* in *run*, or ``None`` when the run has no such case."""
    entry = run["cases"].get(stem)
    if entry is None:
        return None
    tries = [
        {
            **{k: v for k, v in t.items() if k not in _NOT_IN_A_TRY},
            **_judged(t),
            "analysis": t.get("analysis"),
            "reason": try_reason(t),
            "label": label_of([current(t)]),
            "called": called_of(t),
            "retried": t.get("retried") or [],
            "context": t.get("context") or [],
            "trajectory": t.get("trajectory") or [],
            "isolation": isolation_of(t),
            **_automatic_rows(t),
        }
        for t in entry["tries"]
    ]
    return {
        "case": stem,
        "name": entry["name"],
        "about": entry.get("about"),
        "source": entry.get("source"),
        "stale": entry.get("stale"),
        "verdict": verdict_kind(entry),
        "label": label_of(judgements(entry)),
        "basis": verdict_of(entry).basis.value,
        **noted(entry),
        "unjudged": unjudged_reasons(entry),
        "originally": originally(entry),
        "turns": entry["turns"],
        "expected": entry["expected"],
        "reply_rubric": entry["reply_rubric"],
        "finding": finding(entry["tries"]),
        # Where a failing try first went another way than a passing one (review/divergence.py).
        "divergence": entry.get("divergence"),
        "tries": tries,
    }


def latest_rows(
    cases: Sequence[Case],
    runs: Sequence[Mapping[str, Any]],
    stale: Callable[[Case], str | None],
) -> list[dict[str, Any]]:
    """Each case's ``CaseRow`` from the newest of *runs* (newest first) that played it, with
    its newest run of any kind and its ``stale`` reason, as *stale* says now."""
    rows = []
    for case in cases:
        played = next((r for r in runs if (r["cases"].get(case.case) or {}).get("tries")), None)
        last = next((r for r in runs if case.case in r["cases"]), None)
        entry = played["cases"][case.case] if played else None
        row = case_row(case.case, case.name, entry, case, last=last)
        row["stale"] = stale(case)
        rows.append(row)
    return rows


def case_history(stem: str, runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``{run, at, verdict}`` for every run (newest first) that holds case *stem*."""
    return [
        {
            "run": r["run"],
            "at": float(r["started_at"]),
            "verdict": verdict_kind(r["cases"][stem]),
        }
        for r in runs
        if stem in r["cases"]
    ]
