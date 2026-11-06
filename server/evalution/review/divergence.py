"""Where a failing try first went another way than a passing one (docs/judging.md, "First
difference"). Code only: no model reads anything here.

A flaky test, or one that got worse against its comparison run, has tries of the same test that
passed and tries that failed. Lined up step by step, the first place they part is where to start
reading a failure: the cause sits there or before it, never after it.

A try's steps are its tool calls in the order they started, then its reply. Calls the app makes by
itself (``automatic``) are left out: they happen whatever the agent decides. Each step carries
what the model was sent when it decided on it: the last model call of the same agent begun by
then, and for the reply the last model call of the assistant.

At the first index where the two differ, in this order:

* ``missing``: one try has a tool call there and the other replied instead;
* ``tool``: both called a tool, different ones;
* ``args``: the same tool with different arguments, the first that differs named;
* ``context``: the same step, and the passing try's model was sent words the failing one's wasn't
  (:func:`missing_words`).

Ids and timestamps differ on every try, so both arguments and the text sent are compared with
them blanked out (:data:`NOISE`). Steps are lined up by index, with no search for a better
alignment: a call inserted early shows as a difference there, which is where the tries parted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalution.core.evidence import ModelCallRecord, TryEvidence
from evalution.core.judgement import Outcome
from evalution.core.rules import current

#: What differs on every try whatever the agent does: uuids, ISO dates and times, clock times,
#: epoch numbers, and long words mixing letters and digits (call, span and request ids).
NOISE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\b"
    r"|\b\d{10,}\b"
    r"|\b(?=\w*\d)(?=\w*[A-Za-z])\w{12,}\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"\w+(?:[.,'-]\w+)*")
#: Shorter words (the, and, was) are in nearly every prompt and say nothing missing; so are these.
MIN_WORD = 4
_COMMON = """
about after also been before being could does each from have here into just like more
most much only other over same some such than that their them then there these they this
those very were what when where which while will with would your
"""
COMMON = frozenset(_COMMON.split())
#: The most missing words named.
MOST_WORDS = 5
_VALUE = 60


@dataclass(frozen=True)
class Step:
    """One step of a try: a tool call, or the reply (``tool`` ``None``).

    :param args: The call's arguments, as made.
    :param span: The call's span; for the reply, the model call that wrote it.
    :param sent: What the model was sent when it decided this step; ``None`` when it wasn't
        captured.
    """

    tool: str | None
    args: Mapping[str, Any]
    span: str | None
    sent: str | None


@dataclass(frozen=True)
class Divergence:
    """The first step where two tries differ. ``pass_span`` / ``fail_span`` are that step's span on
    each side."""

    step: int
    kind: str
    detail: str
    pass_span: str | None
    fail_span: str | None


def _quiet(text: str) -> str:
    return NOISE.sub("·", text)


def _args(text: str) -> dict[str, Any]:
    try:
        found = json.loads(text) if text.strip() else {}
    except ValueError:
        return {"input": text}
    return found if isinstance(found, dict) else {"input": found}


def _decided_by(calls: Sequence[ModelCallRecord], at: float, helper: str | None) -> str | None:
    """What the model that decided a step at *at* was sent: the last call of that agent begun by
    then, when its content was captured."""
    mine = [c for c in calls if c.helper == helper and c.t <= at]
    last = max(mine, key=lambda c: c.t, default=None)
    return last.sent.text if last is not None and last.captured else None


def steps_of(evidence: TryEvidence, automatic: Collection[str] = ()) -> list[Step]:
    """A try's steps: its tool calls but the *automatic* ones (by id) in start order, then its
    reply."""
    tools = sorted((t for t in evidence.tools if t.id not in automatic), key=lambda t: t.start)
    calls = evidence.model_calls
    steps = [
        Step(t.name, _args(t.args.text), t.id, _decided_by(calls, t.start, t.helper)) for t in tools
    ]
    own = [c for c in calls if c.helper is None]
    last = max(own, key=lambda c: c.t, default=None)
    sent = last.sent.text if last is not None and last.captured else None
    return [*steps, Step(None, {}, last.span if last else None, sent)]


def words(text: str) -> list[str]:
    """The words of *text* worth missing, in order and once each: ids and times blanked out, then
    words of at least :data:`MIN_WORD` characters but the :data:`COMMON` ones, case aside."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(_quiet(text).casefold()):
        if len(word) >= MIN_WORD and word not in COMMON:
            seen.setdefault(word, None)
    return list(seen)


def missing_words(passing: str, failing: str) -> list[str]:
    """The words the passing try's model was sent at a step and the failing one's wasn't: each of
    :func:`words` of *passing* not among those of *failing*, in the order first sent. A word sent
    anywhere in *failing* at that step counts as sent, so wording moved around is no difference;
    only what never reached the failing try's model is."""
    there = set(words(failing))
    return [w for w in words(passing) if w not in there]


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= _VALUE else text[: _VALUE - 1] + "…"


def _arg_differs(passing: Mapping[str, Any], failing: Mapping[str, Any]) -> str | None:
    """The first argument that differs, ids and times aside, with both values; ``None`` when all
    agree."""
    for key in dict.fromkeys([*passing, *failing]):
        was, now = passing.get(key), failing.get(key)
        if _quiet(json.dumps(was, sort_keys=True)) != _quiet(json.dumps(now, sort_keys=True)):
            had = _short(was) if key in passing else "(none)"
            got = _short(now) if key in failing else "(none)"
            return f"{key}: “{got}” instead of “{had}”"
    return None


def first_divergence(passing: Sequence[Step], failing: Sequence[Step]) -> Divergence | None:
    """Where *failing* first differs from *passing*, step by step; ``None`` when nothing does."""
    for i, (was, now) in enumerate(zip(passing, failing, strict=False)):
        if (was.tool is None) != (now.tool is None):
            detail = (
                f"{was.tool} not called"
                if now.tool is None
                else f"{now.tool} called; the passing try replied"
            )
            return Divergence(i, "missing", detail, was.span, now.span)
        if was.tool != now.tool:
            return Divergence(i, "tool", f"{now.tool} instead of {was.tool}", was.span, now.span)
        if differs := _arg_differs(was.args, now.args):
            return Divergence(i, "args", f"{now.tool} {differs}", was.span, now.span)
        if was.sent is not None and now.sent is not None:
            lacks = missing_words(was.sent, now.sent)
            if lacks:
                named = ", ".join(f"“{w}”" for w in lacks[:MOST_WORDS])
                return Divergence(i, "context", f"not sent: {named}", was.span, now.span)
    return None


@dataclass(frozen=True)
class Tried:
    """A try to line up: where it was played, and its steps."""

    run: str
    n: int
    steps: Sequence[Step]


def divergence(passed: Iterable[Tried], failed: Iterable[Tried]) -> dict[str, Any] | None:
    """The first difference between a passing and a failing try, as the case detail shows it:
    ``{pass: {run, n, span}, fail: {run, n, span}, step, kind, detail}``; ``None`` when there is no
    pair, or no pair differs.

    Of every pair, the one alike the longest (the latest first difference) is taken: the fewer
    steps before it, the more of what came before could be why. A tie goes to the earliest pair as
    given, so a pass of this run, listed first, before one of the comparison run.
    """
    best: tuple[Tried, Tried, Divergence] | None = None
    passes = list(passed)
    for bad in failed:
        for good in passes:
            found = first_divergence(good.steps, bad.steps)
            if found is not None and (best is None or found.step > best[2].step):
                best = (good, bad, found)
    if best is None:
        return None
    good, bad, found = best
    return {
        "pass": {"run": good.run, "n": good.n, "span": found.pass_span},
        "fail": {"run": bad.run, "n": bad.n, "span": found.fail_span},
        "step": found.step,
        "kind": found.kind,
        "detail": found.detail,
    }


def _tries(
    run: Mapping[str, Any], stem: str, kept: Iterable[TryEvidence], outcome: Outcome
) -> list[Tried]:
    """The tries of test *stem* in *run* that came out as *outcome*, with evidence kept."""
    entry = run["cases"].get(stem) or {}
    wanted = {t["n"]: t for t in entry.get("tries") or [] if current(t).outcome is outcome}
    return [
        Tried(run["run"], e.n, steps_of(e, wanted[e.n].get("automatic") or ()))
        for e in sorted(kept, key=lambda e: e.n)
        if e.case == stem and e.n in wanted
    ]


def of_run(
    run: Mapping[str, Any],
    kept: Sequence[TryEvidence],
    base: Mapping[str, Any] | None = None,
    base_kept: Sequence[TryEvidence] = (),
) -> dict[str, dict[str, Any] | None]:
    """Each test of *run* and its first difference (:func:`divergence`), from the evidence *kept*.
    The passing try is this run's, else one of *base*, the run it is compared with, when it
    played the same version of the test: a test that passed there and fails here got worse."""
    found: dict[str, dict[str, Any] | None] = {}
    for stem in run["cases"]:
        failed = _tries(run, stem, kept, Outcome.FAILED)
        passed = _tries(run, stem, kept, Outcome.PASSED)
        same = (run.get("case_versions") or {}).get(stem) == (
            (base or {}).get("case_versions") or {}
        ).get(stem)
        if failed and not passed and base is not None and same:
            passed = _tries(base, stem, base_kept, Outcome.PASSED)
        found[stem] = divergence(passed, failed) if failed and passed else None
    return found
