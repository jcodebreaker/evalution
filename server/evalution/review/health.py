"""How an app is doing, for the Home page: its tiles, its areas, its trend, what needs attention
and the recent runs, read from what is stored (runs, tests, the understanding, drift); never a
model call. It says what was counted, and adds no score: a number that blends passing, agreement
and trend moves for reasons of its own and reads firmer than a few tests can support.

:func:`health` is pure over its inputs; :func:`for_target` gathers them for a target.

* **Tiles**: each test set counts by its own latest full run.
* **Areas**: tests grouped by the understanding's priorities (a test made from a suggested test
  that names one), else by the library family of the template it was made from, else by test
  set.
* **Markers** on the trend: a run today's rules read differently from when it was played, the run
  whose system prompt hash differs from the run before, a
  drift change on the first run after it was seen, an understanding version written for a
  changed app on the first run after it.
"""

from __future__ import annotations

import itertools
import statistics
import time
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.case import Case
from evalution.core.judgement import Basis, Claim, Outcome, Route
from evalution.core.rules import comparable, current, first_reason, judged_tries
from evalution.project import drift, impact, knowledge, understanding
from evalution.project.history import History
from evalution.results.record import (
    agent_models,
    checks_made_under,
    judgements,
    key_of,
    originally,
    same_footing,
    verdict_kind,
    verdict_of,
)
from evalution.results.views import report_facts
from evalution.suite import library
from evalution.suite.cases import CaseError, is_benchmark, slugify
from evalution.suite.store import load_cases, load_set, own_sets
from evalution.targets import Targets

DAY = 24 * 3600
PERIODS = {"7d": 7 * DAY, "30d": 30 * DAY, "all": None}
STRONG, OK = 0.8, 0.6
ATTENTION = 5
RECENT = 5
#: Runs a test must have failed in a row to be called failing again and again.
FAILING_RUNS = 3
DEFAULT_SET = "main"
#: How many characters of a commit a sentence names.
SHORT_COMMIT = 7
#: Seconds a drift check runs after the run it was made for started.
CHECK_SLACK = 60
DRIFT_MARKERS = {
    "prompt_changed": "Prompt changed",
    "code_changed": "Code changed",
    "tool_added": "Tools changed",
    "tool_removed": "Tools changed",
    "args_changed": "Tools changed",
}
#: The library's families, by the kind of state a template needs.
FAMILIES = {"compaction": "Long chats", "memory": "Remembering what you tell it"}
OTHER_FAMILY = "Following what you said"


def level(share: float) -> str:
    return "strong" if share >= STRONG else "ok" if share >= OK else "weak"


def _verdicts(run: Mapping[str, Any]) -> dict[str, str]:
    """``case -> verdict`` of the tests *run* played."""
    out = {}
    for stem, entry in run["cases"].items():
        found = verdict_kind(entry)
        if found not in (None, "unjudged"):
            out[stem] = found
    return out


def _agreed(entry: Mapping[str, Any]) -> bool:
    """Whether every try of a test came out the same."""
    return len({current(t).outcome for t in judged_tries(entry)}) <= 1


def _share(runs: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Tests passing and tests played over *runs*."""
    found = [v for r in runs for v in _verdicts(r).values()]
    return sum(v == "pass" for v in found), len(found)


def _played(runs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in runs if r.get("status") != "running" and _verdicts(r)]


def _full(runs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [r for r in runs if r.get("scope") == "all" and r.get("status") == "done"]


def _by_set(runs: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Each set's comparable runs, newest first: its full runs, else every run it played."""
    sets: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        sets.setdefault(run["set"], []).append(run)
    return {k: _full(v) or v for k, v in sets.items()}


def _source_id(source: str | None) -> str | None:
    """The library test a ``library: <id>`` source names."""
    if source and source.startswith("library:"):
        return source.split(":", 1)[1].strip()
    return None


def _area_of(
    stem: str,
    name: str,
    source: str | None,
    set_title: str,
    known: Mapping[str, Any],
) -> str:
    """The area of one test: its priority's title, else its library family, else its set; a set
    left with the default name says nothing, so the test's own."""
    template = _source_id(source)
    for starter in known["starters"]:
        made = (template and starter.get("library") == template) or (
            str(starter.get("title") or "").strip().casefold() == name.strip().casefold()
            or slugify(str(starter.get("title") or "")) == stem
        )
        if made and starter.get("priority") in known["priorities"]:
            return known["priorities"][starter["priority"]]
    if template and template in known["families"]:
        return known["families"][template]
    return name if set_title.casefold() == DEFAULT_SET else set_title


def _families(templates: Sequence[library.Template]) -> dict[str, str]:
    """``template id -> family name``: by the first kind of state it needs."""
    out = {}
    for t in templates:
        trait = next((n for n in t.needs if n in FAMILIES), None)
        out[t.id] = FAMILIES[trait] if trait else OTHER_FAMILY
    return out


def _markers(
    period: Sequence[Mapping[str, Any]],
    found: Mapping[str, Any],
    versions: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """``run -> marker`` over *period* (oldest first)."""
    events: list[tuple[float, str]] = []
    for change in found.get("changes") or []:
        words = DRIFT_MARKERS.get(change.get("kind"))
        if words and change.get("seen_at"):
            events.append((float(change["seen_at"]) - CHECK_SLACK, words))
    for before, after in itertools.pairwise(versions):
        if before.get("fingerprint") and before["fingerprint"] != after.get("fingerprint"):
            events.append((float(after.get("written_at") or 0), "App changed"))
    out: dict[str, str] = {}
    for i, run in enumerate(period):
        prev = period[i - 1] if i else None
        if any(originally(entry) for entry in run["cases"].values()) or (
            prev and checks_made_under(prev) != checks_made_under(run)
        ):
            out[run["run"]] = "Rules changed"
            continue
        if (
            prev
            and agent_models(prev)
            and agent_models(run)
            and (agent_models(prev) != agent_models(run))
        ):
            out[run["run"]] = "Model changed"
            continue
        if prev and prev.get("prompt") and run.get("prompt") and prev["prompt"] != run["prompt"]:
            out[run["run"]] = "Prompt changed"
    for at, words in sorted(events):
        later = next((r for r in period if float(r["started_at"]) >= at), None)
        if later is not None:
            out.setdefault(later["run"], words)
    return out


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _tries(runs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [t for r in runs for c in r["cases"].values() for t in judged_tries(c)]


def _every_try(runs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Judged or not, a try cost what it cost."""
    return [t for r in runs for c in r["cases"].values() for t in c.get("tries") or []]


def health(
    runs: Sequence[Mapping[str, Any]],
    cases: Mapping[str, Sequence[Case]],
    set_titles: Mapping[str, str],
    doc: Mapping[str, Any] | None,
    versions: Sequence[Mapping[str, Any]],
    found: Mapping[str, Any],
    templates: Sequence[library.Template],
    period: str,
    now: float,
) -> dict[str, Any]:
    """The ``Health`` of an app from its *runs* (newest first), its tests by set, its sets'
    names, its understanding *doc* (steers laid over) and its *versions*, its drift *found* and
    the library *templates*, over *period* (a key of :data:`PERIODS`) ending at *now*."""
    span = PERIODS[period]
    since = now - span if span else 0.0
    played = _played(runs)
    in_period = [r for r in played if float(r["started_at"]) >= since]
    by_set = _by_set(played)
    latest = [r[0] for r in by_set.values()]

    passing, total = _share(latest)
    entries = [(r, stem, e) for r in latest for stem, e in r["cases"].items()]
    previous = [(r[0], r[1]) for r in by_set.values() if len(r) > 1 and same_footing(r[0], r[1])]
    moved = sum(_share([now_run])[0] - _share([old])[0] for now_run, old in previous)
    agreed = sum(1 for r in latest for stem in _verdicts(r) if _agreed(r["cases"][stem]))
    seconds = [float(t["seconds"]) for t in _tries(in_period)]
    earlier = [r for r in played if since - span <= float(r["started_at"]) < since] if span else []
    old_seconds = _median([float(t["seconds"]) for t in _tries(earlier)])
    typical = _median(seconds)
    usd = [float(t["usd"]) for t in _every_try(in_period) if t.get("usd")]
    couldnt_judge = sum(verdict_kind(e) == "unjudged" for _, _, e in entries)
    tiles = {
        "passing": {
            "value": passing,
            "total": total + couldnt_judge,
            "delta": moved if previous else None,
            "model_only": sum(
                verdict_kind(e) == "pass" and verdict_of(e).basis is Basis.MODEL_ONLY
                for _, _, e in entries
            ),
            "unjudged": couldnt_judge,
        },
        "consistent": {"value": agreed, "total": total},
        "reply_seconds": {
            "value": round(typical, 1) if typical is not None else None,
            "delta": round(typical - old_seconds, 1)
            if typical is not None and old_seconds is not None
            else None,
        },
        "cost": {
            "per_question": round(statistics.fmean(usd), 4) if usd else None,
            "period_total": round(sum(usd), 4),
        },
    }

    stored = doc or {}
    known = {
        "priorities": {p["id"]: p["title"] for p in stored.get("priorities") or []},
        "starters": stored.get("starters") or [],
        "families": _families(templates),
    }
    files = {(s, c.case): c for s, found_cases in cases.items() for c in found_cases}
    groups: dict[str, list[bool]] = {}
    for run in latest:
        for stem, found_verdict in _verdicts(run).items():
            entry = run["cases"][stem]
            file = files.get((run["set"], stem))
            area = _area_of(
                stem,
                entry.get("name") or (file.name if file else stem),
                entry.get("source") or (file.source if file else None),
                set_titles.get(run["set"], run["set"]),
                known,
            )
            groups.setdefault(area, []).append(found_verdict == "pass")
    areas = [
        {"name": name, "pct": round(100 * sum(v) / len(v)), "level": level(sum(v) / len(v))}
        for name, v in groups.items()
    ]
    areas.sort(key=lambda a: (-a["pct"], a["name"]))

    oldest_first = list(reversed(in_period))
    markers = _markers(oldest_first, found, versions)
    trend = []
    for run in oldest_first:
        p, _ = _share([run])
        # Every test the run played, as Recent runs counts them: an unjudged one isn't a pass.
        played_here = sum(verdict_kind(e) is not None for e in run["cases"].values())
        point: dict[str, Any] = {
            "run": run["run"],
            "at": float(run["started_at"]),
            "passing": p,
            "total": played_here,
        }
        if run["run"] in markers:
            point["marker"] = markers[run["run"]]
        trend.append(point)

    return {
        "tiles": tiles,
        "areas": areas,
        "trend": trend,
        "attention": [
            *_stopped(runs),
            *attention(played, latest, cases, found),
            *_close_to_limit(latest, cases),
        ][:ATTENTION],
        "report": _report(played) if played else None,
        "report_pending": bool(runs and runs[0].get("analyzing")),
        "recent": [
            {
                "run": r["run"],
                "name": set_titles.get(r["set"], r["set"]),
                "at": float(r["started_at"]),
                "usd": round(sum(float(t.get("usd") or 0) for t in _every_try([r])), 4),
                "results": [
                    kind for e in r["cases"].values() if (kind := verdict_kind(e)) is not None
                ],
            }
            for r in played[:RECENT]
        ],
    }


def _report(played: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The newest run in plain words: what it came to, from its checks, and the model's reading of
    it when there is one."""
    run = played[0]
    stored = run.get("report") or {}
    return {
        "run": run["run"],
        "facts": {
            **report_facts(run),
            "regressions": [
                {"case": item["case"], "title": item["title"]}
                for item in regressions(played)
                if item["run"] == run["run"]
            ],
        },
        "reading": stored.get("reading") or [],
        "well": stored.get("well") or [],
        "next": [_next_item(item) for item in stored.get("next") or []],
    }


#: Share of its time limit a test can use before it's said to be close.
CLOSE_TO_LIMIT = 0.9


def _close_to_limit(
    latest: Sequence[Mapping[str, Any]], cases: Mapping[str, Sequence[Case]]
) -> list:
    """Tests that passed with little of their time limit left: the next slow day fails them."""
    limits = {(s, c.case): c.limits.get("seconds") for s, found in cases.items() for c in found}
    out = []
    for run in latest:
        for stem, entry in run["cases"].items():
            limit = limits.get((run["set"], stem))
            used = max((float(t.get("seconds") or 0) for t in entry.get("tries") or []), default=0)
            if limit and used >= CLOSE_TO_LIMIT * limit and verdict_kind(entry) == "pass":
                out.append(
                    {
                        "title": f"{entry.get('name') or stem} is close to its time limit",
                        "detail": f"It took {used:.0f} s of {limit:.0f} s.",
                        "action": "open_test",
                        "case": stem,
                        "run": run["run"],
                    }
                )
    return out


def _stopped(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The newest run, when it couldn't finish: with nothing judged, it shows nowhere else here."""
    newest = runs[0] if runs else None
    if newest is None or newest.get("status") != "failed":
        return []
    return [
        {
            "title": "The last run stopped",
            "detail": str(newest.get("error") or ""),
            "action": "see_why",
            "run": newest["run"],
        }
    ]


def _next_item(item: Any) -> Mapping[str, Any]:
    """A next step; one written before they could cite anything is bare words, citing nothing."""
    if isinstance(item, str):
        return {"case": None, **Claim(text=item).model_dump(mode="json")}
    return item


def _why(entry: Mapping[str, Any]) -> str | None:
    """The first try that didn't pass: why, from what was checked. A model's reading of it is shown
    as that, on the test's page."""
    return next((reason for j in judgements(entry) if (reason := first_reason(j))), None)


def regressions(played: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Tests failing in their newest run (*played*: newest first) that passed in their last run
    on another commit, where the two can be compared (:func:`evalution.core.rules.comparable`). A
    run without a commit says nothing either way, and a test no one could judge isn't failing."""
    out = []
    seen: set[tuple[str, str]] = set()
    for at, run in enumerate(played):
        for stem, now in _verdicts(run).items():
            if (run["set"], stem) in seen:
                continue
            seen.add((run["set"], stem))
            commit = run.get("commit")
            if now != "fail" or not commit:
                continue
            before = next(
                (
                    r
                    for r in played[at + 1 :]
                    if r["set"] == run["set"] and stem in _verdicts(r) and r.get("commit") != commit
                ),
                None,
            )
            if (
                before
                and before.get("commit")
                and _verdicts(before)[stem] == "pass"
                and comparable(key_of(run, stem), key_of(before, stem))
            ):
                entry = run["cases"][stem]
                old, new = before["commit"][:SHORT_COMMIT], commit[:SHORT_COMMIT]
                out.append(
                    {
                        "title": f"{entry.get('name') or stem} passed on {old}, fails on {new}",
                        "detail": _why(entry) or "",
                        "action": "see_why",
                        "case": stem,
                        "run": run["run"],
                    }
                )
    return out


def attention(
    played: Sequence[Mapping[str, Any]],
    latest: Sequence[Mapping[str, Any]],
    cases: Mapping[str, Sequence[Case]],
    found: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """At most :data:`ATTENTION` things to look at, most pressing first: tests a commit broke,
    tests that failed in each of their last :data:`FAILING_RUNS` runs, tests whose tries disagreed
    in the latest run, drift changes that leave tests stale, findings that did not finish, then
    tests failing in the latest run."""
    again, flaky, failing, blind = [], [], [], []
    for run in latest:
        for stem, entry in run["cases"].items():
            if verdict_kind(entry) == "unjudged":
                blind.append(
                    {
                        "title": f"{entry.get('name') or stem}: couldn't judge",
                        "detail": _why(entry) or "",
                        "action": "see_why",
                        "case": stem,
                        "run": run["run"],
                    }
                )
    for run in latest:
        for stem, now in _verdicts(run).items():
            entry = run["cases"][stem]
            name = entry.get("name") or stem
            if now == "flaky":
                said = verdict_of(entry)
                flaky.append(
                    {
                        "title": f"{name} gives different answers",
                        "detail": f"{said.passed} of {said.judged} tries passed",
                        "action": "open_test",
                        "case": stem,
                        "run": run["run"],
                    }
                )
                continue
            if now != "fail":
                continue
            history = [
                _verdicts(r).get(stem)
                for r in played
                if r["set"] == run["set"] and stem in _verdicts(r)
            ][:FAILING_RUNS]
            persistent = len(history) == FAILING_RUNS and all(v == "fail" for v in history)
            item = {
                "title": f"{name} keeps failing" if persistent else f"{name} fails",
                "detail": _why(entry) or "",
                "action": "see_why",
                "case": stem,
                "run": run["run"],
            }
            (again if persistent else failing).append(item)
    stale = []
    every = [c for found_cases in cases.values() for c in found_cases]
    for change in found.get("changes") or []:
        hit = [c for c in every if drift.stale(c, {"changes": [change]})]
        if hit:
            count = f"{len(hit)} tests" if len(hit) > 1 else hit[0].name
            stale.append(
                {
                    "title": f"{count} may be out of date",
                    "detail": change.get("summary") or change.get("detail") or "",
                    "action": "review",
                    **({"case": hit[0].case} if len(hit) == 1 else {}),
                }
            )
    errors = [
        {
            "title": "A run's findings didn't finish",
            "detail": str(r["analysis_error"]),
            "action": "review",
            "run": r["run"],
        }
        for r in latest
        if r.get("analysis_error")
    ]
    routes = [
        {
            "title": f"{entry.get('name') or stem} took a different route",
            "detail": "The result was good, but not by the expected steps.",
            "action": "see_why",
            "case": stem,
            "run": run["run"],
        }
        for run in latest
        for stem, entry in run["cases"].items()
        if _verdicts(run).get(stem) == "pass"
        and any(
            j.outcome is Outcome.PASSED and j.route is Route.DIFFERENT for j in judgements(entry)
        )
    ]
    broke = regressions(played)
    named = {item["case"] for item in broke}
    again = [item for item in again if item["case"] not in named]
    failing = [item for item in failing if item["case"] not in named]
    return [*broke, *again, *flaky, *stale, *errors, *failing, *blind, *routes][:ATTENTION]


def for_target(targets: Targets, runs: History, name: str, period: str) -> dict[str, Any]:
    """:func:`health` of target *name* from what is stored now."""
    cases: dict[str, list[Case]] = {}
    titles: dict[str, str] = {}
    for slug in own_sets(targets.docs, name):
        try:
            titles[slug] = load_set(targets.docs, name, slug).name
            cases[slug] = load_cases(targets.docs, name, slug)
        except CaseError:
            continue
    stored = understanding.load(targets, name)
    doc = understanding.overlay(stored, understanding.steers(targets, name)) if stored else None
    try:
        templates = library.load_templates(targets.config.library_dir)
    except library.LibraryError:
        templates = []
    history = [run for run in runs.all(name) if not is_benchmark(run.get("set"))]
    found = health(
        history,
        cases,
        titles,
        doc,
        understanding.versions(targets, name),
        drift.read(targets, name),
        templates,
        period,
        time.time(),
    )
    return {
        **found,
        "code_change": impact.card(targets, name, history, cases),
        "knowledge": knowledge.summary(runs, name),
    }
