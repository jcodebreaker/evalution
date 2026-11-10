"""One list for a try's "Step by step" (docs/judging.md, "Timeline"): what the person said, each
expected call against the one made, each reply with the lines it was graded on, each state check,
what the page showed and each new conversation, in time order.

The try's API carried the same alignment three ways (``tool_rows``, ``trajectory`` and the plan
steps' ``actual``); this is the one they can all be built from. Pure: from a stored try, its kept
evidence when there is some, and the test's turns, with no model and no files.

Every entry has ``kind``, ``status``, ``span``, ``at`` and ``actor``:

* ``status``: ``passed``, ``failed``, ``unjudged``, or ``None`` for an entry nothing decides (a
  message, a break, an allowed extra call).
* ``at``: seconds from the try's first message; ``None`` when nothing kept says.
* ``actor``: ``person`` for what was sent, ``main`` for the assistant, a delegated agent's name (or
  ``helper``) for its calls, ``page`` for what the page showed, ``code`` for a state check.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.evidence import TryEvidence
from evalution.core.judgement import CheckKind, CheckResult, Outcome
from evalution.core.rules import current
from evalution.core.trace import MAIN

PERSON, PAGE, CODE = "person", "page", "code"
#: The order of entries at the same moment.
_RANK = {
    "before": -1,
    "break": 0,
    "message": 1,
    "call": 2,
    "reply": 3,
    "frame": 4,
    "state": 5,
    "health": 6,
}
#: What a step's status says of the call it lines up.
_CALL_STATUS = {
    "matched": "passed",
    "missing": "failed",
    "wrong_args": "failed",
    "late": "failed",
    "never": "failed",
    "unjudged": "unjudged",
}
#: An extra call that counts against the steps: ``extras`` were off, or the order was ``exact``.
_EXTRA_COUNTS = "not in the case; extras are off"
_OUTCOME = {Outcome.PASSED: "passed", Outcome.FAILED: "failed", Outcome.UNJUDGED: "unjudged"}
#: The checks that grade a reply's words, shown on the last reply.
_REPLY_KINDS = (CheckKind.RUBRIC, CheckKind.REFERENCE, CheckKind.REPLY_TEXT, CheckKind.REPLY_LACKS)


def _worst(statuses: Sequence[str | None]) -> str | None:
    """Failed over unjudged over passed; ``None`` when nothing decided."""
    for status in ("failed", "unjudged", "passed"):
        if status in statuses:
            return status
    return None


def _entry(
    kind: str, status: str | None, span: str | None, at: float | None, actor: str, **more: Any
) -> dict[str, Any]:
    return {"kind": kind, "status": status, "span": span, "at": at, "actor": actor, **more}


def _call_status(step: Mapping[str, Any]) -> str | None:
    status = str(step.get("status") or "")
    if status == "extra":
        return "failed" if step.get("reason") == _EXTRA_COUNTS else None
    return _CALL_STATUS.get(status)


def _calls(attempt: Mapping[str, Any], evidence: TryEvidence | None) -> list[dict[str, Any]]:
    """Each tool step, expected against made. A call never made sits right after the step before
    it, at that step's time."""
    starts: dict[str, float] = {}
    for call in attempt.get("calls") or []:
        starts[str(call.get("id"))] = float(call.get("start") or 0.0)
    if evidence is not None:
        starts.update({t.id: t.start for t in evidence.tools})
    automatic = set(attempt.get("automatic") or ())
    found, last = [], 0.0
    for step in attempt.get("steps") or []:
        if str(step.get("expected") or "").startswith("state:"):
            continue
        span = step.get("span")
        at = starts.get(str(span)) if span else None
        last = at if at is not None else last
        found.append(
            _entry(
                "call",
                _call_status(step),
                span,
                last if attempt.get("calls") is not None or evidence is not None else None,
                str(step.get("actor") or MAIN),
                step=step.get("status"),
                expected=step.get("expected"),
                made=step.get("actual"),
                reason=step.get("reason"),
                automatic=False,
            )
        )
    # Calls the app made by itself weren't scored: shown, deciding nothing.
    for call in attempt.get("calls") or []:
        if call.get("id") in automatic:
            found.append(
                _entry(
                    "call",
                    None,
                    call.get("id"),
                    starts.get(str(call.get("id"))),
                    MAIN if call.get("helper") is None else str(call.get("agent") or "helper"),
                    step="automatic",
                    expected=None,
                    made=f"{call.get('name')}()",
                    reason="the app makes this call by itself",
                    automatic=True,
                )
            )
    return found


def _states(
    attempt: Mapping[str, Any], evidence: TryEvidence | None, checks: Sequence[CheckResult]
) -> list[dict[str, Any]]:
    by_key = {
        c.id.split(":", 2)[2]: c
        for c in checks
        if c.id.startswith("state:") and c.id.count(":") >= 2
    }
    when = {s.key: s.at for s in evidence.states} if evidence is not None else {}
    found = []
    for step in attempt.get("steps") or []:
        expected = str(step.get("expected") or "")
        if not expected.startswith("state:"):
            continue
        key = expected.removeprefix("state:")
        check = by_key.get(key)
        status = (
            _OUTCOME[check.outcome]
            if check is not None
            else {"matched": "passed", "missing": "failed"}.get(str(step.get("status")), "unjudged")
        )
        found.append(
            _entry(
                "state",
                status,
                step.get("span"),
                when.get(key),
                CODE,
                key=key,
                made=step.get("actual"),
                reason=step.get("reason"),
            )
        )
    return found


def _lines(attempt: Mapping[str, Any], checks: Sequence[CheckResult]) -> list[dict[str, Any]]:
    """The checks a reply's words were held to: the rubric lines a model graded, and the words code
    looked for."""
    graded = attempt.get("rubric") or []
    found = []
    for check in checks:
        if check.kind not in _REPLY_KINDS:
            continue
        text, reason = check.why, None
        if check.id.startswith("rubric:"):
            index = int(check.id.split(":", 1)[1])
            if index < len(graded):
                reason = graded[index].get("reason")
        found.append(
            {
                "id": check.id,
                "text": text,
                "status": _OUTCOME[check.outcome],
                "by": check.decided_by.value,
                "role": check.role.value,
                "reason": reason,
            }
        )
    return found


def _shown(checks: Sequence[CheckResult], turn: int) -> str | None:
    """Whether turn *turn* (from 1) showed on the page as its checks say."""
    ref = f"page:{turn}"
    mine = [
        _OUTCOME[c.outcome]
        for c in checks
        if c.kind is CheckKind.SHOWN and any(r.ref == ref for r in c.evidence)
    ]
    return _worst(mine)


def timeline(
    attempt: Mapping[str, Any],
    evidence: TryEvidence | None = None,
    turns: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """The try's ``timeline[]``, in time order.

    :param attempt: The stored try: its ``steps``, ``calls``, ``rubric``, ``reply_text`` and
        current judgement are read.
    :param evidence: Its kept evidence: turn times, what each turn said and was answered, state
        check times and what the page showed. Without it, the turns come from *turns* and only the
        calls have times.
    :param turns: The test's turns as the run stored them (``entry["turns"]``): messages and
        ``{"break": "conversation"}``.
    """
    checks = current(attempt).checks if attempt.get("judgements") else ()
    found: list[dict[str, Any]] = []
    lines = _lines(attempt, checks)
    if evidence is not None and evidence.turns:
        said = [t for t in evidence.turns if t.kind == "message"]
        page = evidence.page.turns if evidence.page is not None else ()
        t0 = evidence.t0

        def rel(at: float) -> float:
            """A turn's clock time as seconds from the try's start, as calls and states are."""
            return round(max(0.0, at - t0), 2)

        for i, turn in enumerate(evidence.turns):
            if i in evidence.breaks and i > 0:
                found.append(_entry("break", None, None, rel(turn.started), PERSON))
            found.append(
                _entry(
                    "message",
                    None,
                    None,
                    rel(turn.started),
                    PERSON,
                    turn=i + 1,
                    text=turn.message.text,
                    filler=turn.kind != "message",
                )
            )
            last = turn is said[-1] if said else False
            mine = lines if last else []
            status = _worst([line["status"] for line in mine if line["role"] == "must"])
            if turn.error:
                status = "failed"
            found.append(
                _entry(
                    "reply",
                    status,
                    None,
                    rel(turn.ended),
                    MAIN,
                    turn=i + 1,
                    text=turn.reply.text,
                    error=turn.error,
                    lines=mine,
                )
            )
            if i < len(page):
                shown = page[i]
                found.append(
                    _entry(
                        "frame",
                        _shown(checks, i + 1),
                        None,
                        rel(turn.ended),
                        PAGE,
                        turn=i + 1,
                        shown_at=shown.shown_at,
                        text=turn.shown.text if turn.shown else "",
                        instead=shown.instead.text,
                        shot=shown.shot,
                    )
                )
    else:
        n = 0
        for turn in turns:
            if "break" in turn:
                found.append(_entry("break", None, None, None, PERSON))
                continue
            n += 1
            found.append(
                _entry(
                    "message",
                    None,
                    None,
                    None,
                    PERSON,
                    turn=n,
                    text=turn.get("message") or "",
                    filler=False,
                )
            )
        if n or attempt.get("reply_text"):
            status = _worst([line["status"] for line in lines if line["role"] == "must"])
            found.append(
                _entry(
                    "reply",
                    status,
                    None,
                    None,
                    MAIN,
                    turn=n or None,
                    text=attempt.get("reply_text") or "",
                    error=None,
                    lines=lines,
                )
            )
    found += _calls(attempt, evidence)
    found += _states(attempt, evidence, checks)
    found += _around(attempt, checks)
    return _in_order(found)


def _around(attempt: Mapping[str, Any], checks: Sequence[CheckResult]) -> list[dict[str, Any]]:
    """How the copy was put back before the try, and the page's health over the whole of it."""
    found = []
    kept = attempt.get("isolation")
    if isinstance(kept, Mapping) and kept.get("how", "none") != "none":
        status = "failed" if kept.get("problem") else "passed" if kept.get("checked") else None
        found.append(
            _entry(
                "before",
                status,
                None,
                0.0,
                CODE,
                how=kept.get("how"),
                seconds=kept.get("restore_seconds"),
                checked=list(kept.get("checked") or []),
                problem=kept.get("problem"),
            )
        )
    for check in checks:
        if check.kind is CheckKind.PAGE_HEALTH:
            found.append(_entry("health", _OUTCOME[check.outcome], None, None, PAGE, why=check.why))
    return found


def _in_order(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """By time, then by kind at the same moment, else as built. With no times kept at all, as
    built; an entry with no time of its own goes first (what was sent) or last (what came of it)."""
    times = [e["at"] for e in entries if e["at"] is not None]
    if not times:
        return entries
    end = max(times)

    def when(entry: Mapping[str, Any]) -> float:
        if entry["kind"] == "before":
            return -2.0
        if entry["at"] is not None:
            return float(entry["at"])
        return end if entry["kind"] in ("reply", "frame", "state", "health") else -1.0

    def rank(entry: Mapping[str, Any]) -> int:
        if entry["at"] is not None:
            return _RANK[entry["kind"]]
        # With no time: what was sent keeps its place first, what came of it goes after the rest.
        return 0 if when(entry) < 0 else len(_RANK) + _RANK[entry["kind"]]

    return sorted(entries, key=lambda e: (when(e), rank(e)))
