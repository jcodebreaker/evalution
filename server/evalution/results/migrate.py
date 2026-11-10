"""Stored runs of older shapes, read as today's (docs/judging.md, "Old runs").

:func:`upcast` is pure and runs on every read, so a run from any version reads as schema
:data:`~evalution.results.record.SCHEMA`. :meth:`~evalution.results.store.RunStore.upcast_all`
rewrites the stored ones; running it again changes nothing.

Schema 0 kept a ``passed`` boolean, an error string and step statuses. Here they become the checks
they were, judged under today's rules with ``rules_from: 0``; ``passed`` stays as ``legacy_passed``.
The strings runs of that age used for why a try stopped are read in this file and nowhere else.
Their judge is on record by key only, so they are comparable with nothing.

Schema 1 didn't say where a run played: every run then played on the computer that kept it, so it
reads as ``local``, with no name.

Schema 2 didn't say how tries were kept apart: no run then played on a copy evalution put back, so
the run and each try read as ``isolation: {"how": "none", ...}``.

Schema 3 didn't say what played (ADR 0011): its ``version`` reads as "what was on disk", with the
commit it recorded, and nothing vouched for (:func:`~evalution.core.version.not_recorded`).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.case import DEFAULT_PASS_MARKS
from evalution.core.judgement import (
    CheckKind,
    CheckResult,
    Claim,
    Decider,
    EvidenceRef,
    JudgeStamp,
    Outcome,
    Role,
    Route,
    WhyUnjudged,
)
from evalution.core.rules import current, judgement, line_outcome, rubric_outcome, tools_bind
from evalution.core.steps import SAY_WHICH, Step, grade_tools, strict_ok
from evalution.core.trace import Call
from evalution.core.version import not_recorded
from evalution.results.record import (
    SCHEMA,
    environment,
    isolation,
    judged_unheld,
    run_isolation,
    unheld,
)
from evalution.results.views import totals

#: What reading something that isn't a run raises, whichever part of it is off.
UNREADABLE = (ValueError, KeyError, TypeError, AttributeError)
_NO_TRACES = "No traces arrived, so this try could not be judged"
_FROM_READS = ("reading the traces:",)
#: What an old try's error said it stopped on, when it wasn't the app's doing.
_UNJUDGED_ERRORS: tuple[tuple[str, WhyUnjudged], ...] = (
    ("grader:", WhyUnjudged.JUDGE_FAILED),
    ("the app's session went away", WhyUnjudged.APP_UNREACHABLE),
    ("ConnectError", WhyUnjudged.APP_UNREACHABLE),
    ("ConnectTimeout", WhyUnjudged.APP_UNREACHABLE),
    ("this target can't start a new conversation", WhyUnjudged.NOT_SUPPORTED),
    ("this target takes no files", WhyUnjudged.NOT_SUPPORTED),
    ("this target cannot stop a turn", WhyUnjudged.NOT_SUPPORTED),
    ("RuntimeError: no filler model", WhyUnjudged.JUDGE_FAILED),
    ("RuntimeError: the filler model", WhyUnjudged.JUDGE_FAILED),
    ("LLMError", WhyUnjudged.JUDGE_FAILED),
)
#: What an old state step's reason said when the check was never really made.
_UNJUDGED_STATES: tuple[tuple[str, WhyUnjudged], ...] = (
    ("no traced model calls", WhyUnjudged.NO_TRACES),
    ("no model call", WhyUnjudged.PARTIAL_TRACE),
    ("this target has no state checks", WhyUnjudged.NOT_SUPPORTED),
)
_OLD_KINDS = {"assistant": "app", "case": "test", "infra": "unclear", "flaky": "unclear"}


def _check(
    id_: str,
    kind: CheckKind,
    outcome: Outcome,
    why: str,
    *,
    by: Decider = Decider.CODE,
    role: Role = Role.MUST,
    unjudged: WhyUnjudged | None = None,
    evidence: Sequence[EvidenceRef] = (),
    score: float | None = None,
) -> CheckResult:
    return CheckResult(
        id=id_,
        kind=kind,
        decided_by=by,
        role=role,
        outcome=outcome,
        why=why,
        unjudged=unjudged,
        evidence=tuple(evidence),
        score=score,
    )


def _prefix_why(text: str, table: Sequence[tuple[str, WhyUnjudged]]) -> WhyUnjudged | None:
    return next((why for start, why in table if text.startswith(start)), None)


def _no_traces(attempt: Mapping[str, Any]) -> bool:
    error = str(attempt.get("error") or "")
    return error == _NO_TRACES or error.startswith(_FROM_READS)


def _state_outcome(step: Mapping[str, Any], attempt: Mapping[str, Any]) -> tuple[Outcome, Any]:
    reason = str(step.get("reason") or "")
    why = _prefix_why(reason, _UNJUDGED_STATES)
    if why:
        return Outcome.UNJUDGED, why
    if step.get("span") and _call_has_no_content(step["span"], attempt):
        return Outcome.UNJUDGED, WhyUnjudged.CONTENT_NOT_CAPTURED
    return (Outcome.PASSED if step.get("status") == "matched" else Outcome.FAILED), None


def _call_has_no_content(span: str, attempt: Mapping[str, Any]) -> bool:
    """Runs of that age read an empty model call as lacking every word."""
    call = next((c for c in attempt.get("context") or [] if c.get("span") == span), None)
    return call is not None and not any(s.get("preview") for s in call.get("sections") or [])


def _state_checks(attempt: Mapping[str, Any]) -> list[CheckResult]:
    found = []
    states = [s for s in attempt.get("steps") or [] if _is_state(s)]
    for i, step in enumerate(states):
        key = str(step["expected"]).removeprefix("state:")
        kind = CheckKind.CONTEXT if key.startswith("context_") else CheckKind.STATE
        outcome, why = _state_outcome(step, attempt)
        refs = [EvidenceRef(ref=f"span:{step['span']}")] if step.get("span") else []
        found.append(
            _check(
                f"state:{i}:{key}",
                kind,
                outcome,
                str(step.get("reason") or ""),
                unjudged=why,
                evidence=refs,
            )
        )
    return found


def _is_state(step: Mapping[str, Any]) -> bool:
    return str(step.get("expected") or "").startswith("state:")


def _never_checks(
    expected: Mapping[str, Any], attempt: Mapping[str, Any], blind: bool
) -> list[CheckResult]:
    called = {
        str(s.get("actual") or "").split("(", 1)[0]
        for s in attempt.get("steps") or []
        if s.get("status") == "never"
    }
    found = []
    for name in expected.get("never") or []:
        if name in called:
            why = f"{name} is never to be called"
            found.append(_check(f"never:{name}", CheckKind.NEVER, Outcome.FAILED, why))
        elif blind:
            found.append(
                _check(
                    f"never:{name}",
                    CheckKind.NEVER,
                    Outcome.UNJUDGED,
                    "no traces arrived",
                    unjudged=WhyUnjudged.NO_TRACES,
                )
            )
        else:
            why = f"{name} was not called"
            found.append(_check(f"never:{name}", CheckKind.NEVER, Outcome.PASSED, why))
    return found


def _first_miss(steps: Sequence[Mapping[str, Any]]) -> str:
    for step in steps:
        status = step.get("status")
        if status in ("matched", "never") or step.get("reason") == "not in the case; allowed":
            continue
        label = step.get("expected") or step.get("actual")
        return f"{label}: {step.get('reason')}" if label else str(step.get("reason"))
    return "the expected calls were made"


def _steps_check(
    expected: Mapping[str, Any],
    attempt: Mapping[str, Any],
    has_outcome: bool,
    mark: float,
    blind: bool,
) -> CheckResult | None:
    if not (
        expected.get("expected")
        or expected.get("extras") is False
        or expected.get("order") == "exact"
    ):
        return None
    role = Role.MUST if tools_bind(expected, has_outcome) else Role.GUIDE
    if blind:
        return _check(
            "steps",
            CheckKind.STEPS,
            Outcome.UNJUDGED,
            "no traces arrived",
            role=role,
            unjudged=WhyUnjudged.NO_TRACES,
        )
    score = float(attempt.get("tools") or 0)
    steps = [s for s in attempt.get("steps") or [] if not _is_state(s)]
    strict = strict_ok(expected, [_as_step(s) for s in steps])
    held = strict if strict is not None else score >= mark
    outcome = Outcome.PASSED if held else Outcome.FAILED
    return _check("steps", CheckKind.STEPS, outcome, _first_miss(steps), role=role, score=score)


def _as_step(stored: Mapping[str, Any]) -> Step:
    return Step(
        str(stored.get("status") or ""),
        stored.get("expected"),
        stored.get("actual"),
        str(stored.get("reason") or ""),
        stored.get("span"),
    )


def _old_line_outcome(line: Mapping[str, Any]) -> Outcome:
    """A line graded under the old merge, by today's rule. A second grading both were sure of can't
    happen any more: it keeps the merge's answer."""
    second = line.get("second")
    if second is None:
        return line_outcome(line, None)
    if line.get("sure", True) and second.get("sure", True):
        return Outcome.PASSED if line.get("pass") else Outcome.FAILED
    if not second.get("sure", True) and line.get("sure", True):
        # Only the first was sure; today it would have stood alone.
        first = (not second["pass"]) if line.get("split") else second["pass"]
        return Outcome.PASSED if first else Outcome.FAILED
    return line_outcome({"pass": line.get("pass"), "sure": False}, second)


def _need(entry: Mapping[str, Any]) -> int | None:
    """``reply.pass`` wasn't stored: a try that passed with lines missed shows it was there."""
    held = [
        sum(1 for line in t.get("rubric") or [] if line.get("pass"))
        for t in entry.get("tries") or []
        if t.get("passed") and t.get("rubric")
    ]
    lines = len(entry.get("reply_rubric") or [])
    return min(held) if held and min(held) < lines else None


def _rubric_checks(lines: Sequence[Mapping[str, Any]], need: int | None) -> list[CheckResult]:
    every = need is None or need >= len(lines)
    role = Role.MUST if every else Role.GUIDE
    checks = []
    for i, line in enumerate(lines):
        outcome = _old_line_outcome(line)
        checks.append(
            _check(
                f"rubric:{i}",
                CheckKind.RUBRIC,
                outcome,
                str(line.get("line") or ""),
                by=Decider.MODEL,
                role=role,
                unjudged=WhyUnjudged.JUDGE_UNSURE if outcome is Outcome.UNJUDGED else None,
                evidence=[EvidenceRef(ref="reply", quote=q) for q in line.get("quotes") or []],
            )
        )
    if every or need is None:
        return checks
    outcome = rubric_outcome([c.outcome for c in checks], need)
    held = sum(c.outcome is Outcome.PASSED for c in checks)
    whole = _check(
        "rubric",
        CheckKind.RUBRIC,
        outcome,
        f"{held} of {len(lines)} lines held, {need} needed",
        by=Decider.MODEL,
        unjudged=WhyUnjudged.JUDGE_UNSURE if outcome is Outcome.UNJUDGED else None,
    )
    return [whole, *checks]


def _reply_checks(
    entry: Mapping[str, Any], attempt: Mapping[str, Any], mark: float, turn_stopped: bool
) -> list[CheckResult]:
    lines = attempt.get("rubric") or []
    error = str(attempt.get("error") or "")
    if lines:
        return _rubric_checks(lines, _need(entry))
    rubric = entry.get("reply_rubric") or []
    if error.startswith("grader:") or (rubric and _no_traces(attempt)):
        why = WhyUnjudged.JUDGE_FAILED if error.startswith("grader:") else WhyUnjudged.NO_TRACES
        return [
            _check(
                f"rubric:{i}",
                CheckKind.RUBRIC,
                Outcome.UNJUDGED,
                error,
                by=Decider.MODEL,
                unjudged=why,
            )
            for i in range(len(rubric))
        ]
    score = attempt.get("reply")
    if turn_stopped or not isinstance(score, (int, float)) or score == 1.0:
        return []
    # No rubric, and a score that isn't the 1.0 of nothing to grade: a reference was all it had.
    outcome = Outcome.PASSED if score >= mark else Outcome.FAILED
    words = f"reply score {score:.2f}, needs {mark:.2f}"
    return [
        _check(
            "reference", CheckKind.REFERENCE, outcome, words, by=Decider.MODEL, score=float(score)
        )
    ]


def _turn_check(attempt: Mapping[str, Any]) -> CheckResult | None:
    error = str(attempt.get("error") or "")
    if not error or _no_traces(attempt) or error.startswith("grader:"):
        return None
    why = _prefix_why(error, _UNJUDGED_ERRORS)
    outcome = Outcome.UNJUDGED if why else Outcome.FAILED
    return _check("turn:0", CheckKind.TURN, outcome, error, unjudged=why)


def _try(
    entry: Mapping[str, Any],
    attempt: dict[str, Any],
    judge: JudgeStamp,
    marks: Mapping[str, float],
) -> None:
    expected = entry.get("expected") or {}
    blind = _no_traces(attempt)
    turn = _turn_check(attempt)
    states = _state_checks(attempt)
    reply = _reply_checks(entry, attempt, float(marks["reply"]), turn is not None)
    has_outcome = bool(entry.get("reply_rubric")) or bool(states)
    aligned = _steps_check(expected, attempt, has_outcome, float(marks["tools"]), blind)
    checks: list[CheckResult] = [*states, *_never_checks(expected, attempt, blind)]
    checks += [aligned] if aligned else []
    checks += [turn] if turn else []
    checks += reply
    if not checks:
        checks.append(
            _check(
                "turn:seen",
                CheckKind.TURN,
                Outcome.UNJUDGED if blind else Outcome.PASSED,
                "no traces arrived" if blind else "the try ran and its traces arrived",
                unjudged=WhyUnjudged.NO_TRACES if blind else None,
            )
        )
    checks += [
        _check("limit:seconds", CheckKind.LIMIT, Outcome.FAILED, str(broken))
        for broken in (attempt.get("limits_broken") or [])[:1]
    ]
    if blind:
        route = Route.UNKNOWN
    elif attempt.get("different_route"):
        route = Route.DIFFERENT
    else:
        steps = [_as_step(s) for s in attempt.get("steps") or [] if not _is_state(s)]
        strict = strict_ok(expected, steps)
        held = (
            strict
            if strict is not None
            else float(attempt.get("tools") or 0) >= float(marks["tools"])
        )
        route = Route.EXPECTED if held else Route.DIFFERENT
    graded = any(c.decided_by is Decider.MODEL for c in checks)
    found = judgement(checks, judge if graded else None, route, rules_from=0)
    attempt["judgements"] = [found.model_dump(mode="json")]
    attempt["legacy_passed"] = bool(attempt.get("passed"))
    attempt.pop("passed", None)
    attempt.pop("different_route", None)
    _analysis(attempt.get("analysis"))


def _unchecked(text: str) -> dict[str, Any]:
    """Words kept from before claims were checked."""
    return Claim(text=text, verified=False).model_dump(mode="json")


def _analysis(found: Any) -> None:
    if not isinstance(found, dict):
        return
    if "kind" in found:
        found["suspected"] = _OLD_KINDS.get(found.pop("kind"), "unclear")
    for key in ("summary", "cause", "detail"):
        if isinstance(found.get(key), str):
            found[key] = _unchecked(found[key])


def _report(report: dict[str, Any]) -> None:
    """Kept whole, and its words shown as what they always were: unchecked."""
    report["verified"] = False
    report["well"] = [{"case": None, **_unchecked(line)} for line in report.get("well") or []]
    report["reading"] = [
        {"case": wrong.get("case"), **_unchecked(str(wrong["text"]))}
        for wrong in report.get("wrong") or []
    ]


def _legacy_verdict(entry: Mapping[str, Any]) -> str | None:
    """What a run of that age called a test: tries without traces didn't count."""
    tries = [t for t in entry.get("tries") or [] if t.get("error") != _NO_TRACES]
    if not entry.get("tries"):
        return None
    if not tries:
        return "unjudged"
    passed = [bool(t.get("passed")) for t in tries]
    return "pass" if all(passed) else "flaky" if any(passed) else "fail"


def _zero_to_one(old: Mapping[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(dict(old))
    marks = doc.pop("pass_scores", None) or doc.get("pass_marks") or dict(DEFAULT_PASS_MARKS)
    judge = JudgeStamp(key=str(doc.pop("grader", None) or "strong"))
    doc.update(
        schema=1,
        rules=1,
        pass_marks=marks,
        judge=judge.model_dump(mode="json"),
    )
    for entry in doc.get("cases", {}).values():
        entry["legacy_verdict"] = _legacy_verdict(entry)
        for attempt in entry.get("tries") or []:
            _try(entry, attempt, judge, marks)
    found = doc.get("analysis")
    if isinstance(found, dict):
        _analysis(found)
        for pattern in found.get("patterns") or []:
            _analysis(pattern)
    if isinstance(doc.get("report"), dict):
        _report(doc["report"])
    if "totals" in doc:
        doc["totals"] = totals(doc)
    return doc


def _one_to_two(old: Mapping[str, Any]) -> dict[str, Any]:
    return {**old, "schema": 2, "environment": environment("local")}


def _two_to_three(old: Mapping[str, Any]) -> dict[str, Any]:
    cases = {
        stem: {
            **entry,
            "tries": [{**t, "isolation": isolation()} for t in entry.get("tries") or []],
        }
        for stem, entry in (old.get("cases") or {}).items()
    }
    return {**old, "schema": 3, "isolation": run_isolation(), "cases": cases}


def _three_to_four(old: Mapping[str, Any]) -> dict[str, Any]:
    return {**old, "schema": 4, "version": not_recorded(old.get("commit"))}


#: The rules from which an ``exact`` order and ``extras: false`` are held to strictly.
STRICT_STEPS = 4


def _steps_again(
    tools: Mapping[str, Any], attempt: Mapping[str, Any], mark: float
) -> tuple[bool, list[Step], float]:
    """A stored try's steps read under today's rules: graded again from its calls when it kept
    them, else its stored steps held to today's rule. ``(held, steps, score)``."""
    if isinstance(attempt.get("calls"), list):
        automatic = set(attempt.get("automatic") or ())
        calls = [
            Call(
                str(c["id"]),
                str(c["name"]),
                c.get("args") or {},
                float(c.get("start") or 0.0),
                c.get("end"),
                c.get("helper"),
                c.get("agent"),
                c.get("server"),
            )
            for c in attempt["calls"]
            if c.get("id") not in automatic
        ]
        steps, score = grade_tools(tools, calls)
    else:
        steps = [_as_step(s) for s in attempt.get("steps") or [] if not _is_state(s)]
        score = float(attempt.get("tools") or 0)
    strict = strict_ok(tools, steps)
    return (strict if strict is not None else score >= mark), steps, score


def _strict_try(
    tools: Mapping[str, Any], attempt: Mapping[str, Any], mark: float
) -> dict[str, Any] | None:
    """The judgement to add to a try judged before :data:`STRICT_STEPS` whose steps check reads
    otherwise now, else ``None``."""
    if not attempt.get("judgements"):
        return None
    found = current(attempt)
    if found.rules >= STRICT_STEPS:
        return None
    at = next((i for i, c in enumerate(found.checks) if c.id == "steps"), None)
    if at is None or found.checks[at].outcome not in (Outcome.PASSED, Outcome.FAILED):
        return None
    held, steps, score = _steps_again(tools, attempt, mark)
    if held == (found.checks[at].outcome is Outcome.PASSED):
        return None
    checks = list(found.checks)
    checks[at] = checks[at].model_copy(
        update={
            "outcome": Outcome.PASSED if held else Outcome.FAILED,
            "why": _first_miss([s.as_dict() for s in steps]),
            "score": score,
        }
    )
    route = found.route
    if route in (Route.EXPECTED, Route.DIFFERENT):
        route = Route.EXPECTED if held else Route.DIFFERENT
    was = found.rules if found.rules_from is None else found.rules_from
    return judgement(checks, found.judge, route, rules_from=was).model_dump(mode="json")


def _strict_steps(doc: dict[str, Any]) -> dict[str, Any]:
    """Rules 4: an ``exact`` order or ``extras: false`` is held to strictly, and a delegated
    agent's own calls aren't extras. A try of such a test whose steps check now reads otherwise
    gets a judgement added, so its test says "Originally passed; rules changed". The same object
    when none does."""
    if int(doc.get("rules") or 0) >= STRICT_STEPS:
        return doc
    mark = float((doc.get("pass_marks") or DEFAULT_PASS_MARKS)["tools"])
    added: dict[tuple[str, int], dict[str, Any]] = {}
    for stem, entry in (doc.get("cases") or {}).items():
        tools = entry.get("expected") or {}
        if tools.get("order") != "exact" and tools.get("extras") is not False:
            continue
        for i, attempt in enumerate(entry.get("tries") or []):
            found = _strict_try(tools, attempt, mark)
            if found is not None:
                added[(stem, i)] = found
    if not added:
        return doc
    new = copy.deepcopy(doc)
    for (stem, i), found in added.items():
        new["cases"][stem]["tries"][i]["judgements"].append(found)
    return new


#: The rules from which a try whose put back the end-of-run check found unheld is not judged.
LATE_RESETS = 5


def _late_resets(doc: dict[str, Any]) -> dict[str, Any]:
    """Rules 5: a try judged before the end-of-run check found its copy's put back didn't hold
    (its ``isolation.problem`` says so) is not judged. It gets a judgement added saying why; the
    same object when none does."""
    if int(doc.get("rules") or 0) >= LATE_RESETS:
        return doc
    found = [
        (stem, i, problem)
        for stem, entry in (doc.get("cases") or {}).items()
        for i, attempt in enumerate(entry.get("tries") or [])
        if (problem := unheld(attempt)) is not None
    ]
    if not found:
        return doc
    new = copy.deepcopy(doc)
    for stem, i, problem in found:
        attempt = new["cases"][stem]["tries"][i]
        was = current(attempt)
        rules = was.rules if was.rules_from is None else was.rules_from
        attempt["judgements"].append(judged_unheld(problem, rules).model_dump(mode="json"))
    if "totals" in new:
        new["totals"] = totals(new)
    return new


def _ambiguous(check: Mapping[str, Any]) -> bool:
    return (
        check.get("id") == "steps"
        and check.get("unjudged") == WhyUnjudged.NOT_SUPPORTED.value
        and str(check.get("why") or "").endswith(f": {SAY_WHICH}")
    )


def _ambiguous_tools(doc: dict[str, Any]) -> dict[str, Any]:
    """Rules 5: a steps check not judged because a bare tool name was two servers' tools says so
    (``ambiguous_tool``), not that the target couldn't answer it. Same outcome, its own reason; the
    same object when no try has one."""
    if int(doc.get("rules") or 0) >= LATE_RESETS:
        return doc
    found = [
        (stem, i)
        for stem, entry in (doc.get("cases") or {}).items()
        for i, attempt in enumerate(entry.get("tries") or [])
        if attempt.get("judgements")
        and any(_ambiguous(c) for c in attempt["judgements"][-1].get("checks") or [])
    ]
    if not found:
        return doc
    new = copy.deepcopy(doc)
    for stem, i in found:
        attempt = new["cases"][stem]["tries"][i]
        was = current(attempt)
        checks = [
            c.model_copy(update={"unjudged": WhyUnjudged.AMBIGUOUS_TOOL})
            if _ambiguous(c.model_dump(mode="json"))
            else c
            for c in was.checks
        ]
        rules = was.rules if was.rules_from is None else was.rules_from
        attempt["judgements"].append(
            judgement(checks, was.judge, was.route, rules).model_dump(mode="json")
        )
    return new


def _rules_five(doc: dict[str, Any]) -> dict[str, Any]:
    return _late_resets(_ambiguous_tools(doc))


def upcast(doc: dict[str, Any]) -> dict[str, Any]:
    """*doc* as today's schema and rules: the same object when it already is."""
    schema = int(doc.get("schema") or 0)
    if schema >= SCHEMA:
        return _rules_five(_strict_steps(doc))
    found = _zero_to_one(doc) if schema < 1 else doc
    found = _one_to_two(found) if schema < 2 else found
    found = _two_to_three(found) if schema < 3 else found
    return _rules_five(_strict_steps(_three_to_four(found)))
