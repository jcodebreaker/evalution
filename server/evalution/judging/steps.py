"""The tool-call grade: align a try's trajectory with
the case's ``tools.expected`` by code, with no model.

Each expected call (a call with ``times: n`` counts n times; each member of a ``parallel`` group
counts once) becomes one :class:`Step` with a human reason, and so does every call that matched
no step (``extra``, so the try shows what really happened). The score is the share of expected
calls matched, less 0.25 for each call to a ``never`` tool and, when ``extras`` is false, less
0.1 for each extra call; the floor is 0.

* ``in_order``: the expected calls appear in order; other calls may come between them.
* ``exact``: ``in_order`` with nothing extra.
* ``any_order``: every expected call appears, anywhere.
* ``parallel``: the members, in any order among themselves, and each one started before the one
  before it finished (same turn, or delegated agents that overlapped). A member that started
  after is ``late``.
"""

from __future__ import annotations

import itertools
import json
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalution.core.case import MATCHERS
from evalution.core.trace import Call

NEVER_PENALTY = 0.25
EXTRA_PENALTY = 0.1
#: An extra call's reason: allowed (it costs nothing), or a miss when ``extras`` is off.
EXTRA_OK = "not in the case; allowed"
EXTRA_OFF = "not in the case; extras are off"
_SHORT = 60


@dataclass
class Step:
    """One line of the alignment (the API's ``Step``)."""

    status: str
    expected: str | None
    actual: str | None
    reason: str
    span: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "reason": self.reason,
            "span": self.span,
        }


def _short(value: Any, limit: int = _SHORT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _is_matcher(value: Any) -> bool:
    return isinstance(value, dict) and len(value) == 1 and next(iter(value)) in MATCHERS


def describe_arg(key: str, value: Any) -> str:
    """One expected argument in words: ``task contains churn``."""
    if not _is_matcher(value):
        return f"{key}={_short(value, 40)}"
    op, arg = next(iter(value.items()))
    if op == "eq":
        return f"{key}={_short(arg, 40)}"
    if op == "contains":
        return f"{key} contains {arg}"
    if op == "re":
        return f"{key} ~ /{arg}/"
    if op == "len":
        return f"{key} length {arg.get('min', 0)}..{arg.get('max', '')}".rstrip(".")
    return f"{key} given"


def describe(spec: Mapping[str, Any]) -> str:
    """An expected call in words: ``hand_off(task contains churn)``."""
    args = spec.get("args") or {}
    return f"{spec['name']}({', '.join(describe_arg(k, v) for k, v in args.items())})"


def actual(call: Call) -> str:
    """A call as made: ``hand_off(task="Write a report…")``."""
    args = ", ".join(f"{k}={_short(v, 40)}" for k, v in call.args.items())
    return _short(f"{call.name}({args})", 160)


def arg_problem(key: str, want: Any, args: Mapping[str, Any]) -> str | None:
    """Why argument *key* fails *want*, or ``None`` when it passes."""
    if key not in args:
        return f"{key} missing"
    got = args[key]
    if not _is_matcher(want):
        want = {"eq": want}
    op, arg = next(iter(want.items()))
    if op == "eq":
        ok = got == arg
    elif op == "contains":
        ok = str(arg).lower() in _text(got).lower()
    elif op == "re":
        ok = re.search(arg, _text(got)) is not None
    elif op == "len":
        size = len(got) if isinstance(got, (list, str, dict)) else len(_text(got))
        ok = arg.get("min", 0) <= size <= arg.get("max", math.inf)
    else:  # any: present
        ok = True
    return None if ok else f"{describe_arg(key, want)} expected, got {_short(got)}"


def problems(spec: Mapping[str, Any], call: Call) -> list[str]:
    """Why *call* does not match *spec*'s arguments (empty when it does)."""
    found = [arg_problem(key, want, call.args) for key, want in (spec.get("args") or {}).items()]
    return [p for p in found if p]


@dataclass
class _Unit:
    spec: Mapping[str, Any]
    label: str
    group: int | None  # index of its parallel step, or None
    call: int | None = None  # index into the work list
    full: bool = False


def _units(expected: Sequence[Mapping[str, Any]]) -> list[_Unit]:
    units: list[_Unit] = []
    for i, step in enumerate(expected):
        if "parallel" in step:
            units.extend(_Unit(m, describe(m), i) for m in step["parallel"])
            continue
        times = int(step.get("times", 1))
        for n in range(times):
            label = describe(step) + (f" ({n + 1} of {times})" if times > 1 else "")
            units.append(_Unit(step, label, None))
    return units


def _claim(unit: _Unit, work: Sequence[Call], used: set[int], lo: int, *, full: bool) -> int | None:
    for index in range(lo, len(work)):
        call = work[index]
        if index in used or call.name != unit.spec["name"]:
            continue
        if full and problems(unit.spec, call):
            continue
        used.add(index)
        unit.call, unit.full = index, full
        return index
    return None


def _align(units: list[_Unit], work: Sequence[Call], ordered: bool) -> None:
    """Give each unit its call: a full match, else a call of that name (wrong arguments)."""
    used: set[int] = set()
    if not ordered:
        for full in (True, False):
            for unit in units:
                if unit.call is None:
                    _claim(unit, work, used, 0, full=full)
        return
    lo, at = 0, 0
    while at < len(units):
        group = units[at].group
        members = [units[at]]
        while group is not None and at + len(members) < len(units):
            if units[at + len(members)].group != group:
                break
            members.append(units[at + len(members)])
        for full in (True, False):
            for unit in members:
                if unit.call is None:
                    _claim(unit, work, used, lo, full=full)
        matched = [u.call for u in members if u.full and u.call is not None]
        if matched:
            lo = max(matched) + 1
        at += len(members)


def _late(units: Sequence[_Unit], work: Sequence[Call]) -> dict[int, str]:
    """``unit index -> reason`` for each parallel member that started after the one before it
    finished."""
    late: dict[int, str] = {}
    groups: dict[int, list[int]] = {}
    for i, unit in enumerate(units):
        if unit.group is not None and unit.full and unit.call is not None:
            groups.setdefault(unit.group, []).append(i)
    for members in groups.values():
        members.sort(key=lambda i: work[units[i].call or 0].start)
        for before, after in itertools.pairwise(members):
            first, second = work[units[before].call or 0], work[units[after].call or 0]
            if first.end is not None and second.start >= first.end:
                gap = second.start - first.end
                late[after] = f"started {gap:.0f} s after the first finished"
    return late


def chosen_calls(
    calls: Sequence[Call], automatic: Collection[str], tools: Mapping[str, Any]
) -> list[Call]:
    """The calls that count as the agent's choice: not those of *automatic* tools, which the app
    makes by itself (a hook that runs on every turn), unless the test names them. They stay in the
    evidence, and a test can still check what they returned."""
    named = {name for name in tools.get("never") or []}
    for step in tools.get("expected") or []:
        named |= {member["name"] for member in step.get("parallel", [step])}
    return [c for c in calls if c.name not in automatic or c.name in named]


def grade_tools(tools: Mapping[str, Any], calls: Sequence[Call]) -> tuple[list[Step], float]:
    """Align *calls* (in start order) with the case's *tools* block.

    :returns: ``(steps, score)``: the expected calls in order, then each call to a ``never``
        tool, then each call that matched nothing (it costs score only when ``extras`` is
        false); score 0..1.
    """
    order = tools.get("order", "in_order")
    extras_ok = bool(tools.get("extras", True)) and order != "exact"
    never = set(tools.get("never") or [])
    ordered_calls = sorted(calls, key=lambda c: (c.start, c.end if c.end is not None else 1e18))
    work = [c for c in ordered_calls if c.name not in never]
    units = _units(tools.get("expected") or [])
    _align(units, work, ordered=order != "any_order")
    late = _late(units, work)
    steps: list[Step] = []
    matched = 0
    for i, unit in enumerate(units):
        if unit.call is None:
            steps.append(Step("missing", unit.label, None, f"no {unit.spec['name']} call", None))
            continue
        call = work[unit.call]
        if not unit.full:
            reason = "; ".join(problems(unit.spec, call))
            steps.append(Step("wrong_args", unit.label, actual(call), reason, call.id))
        elif i in late:
            steps.append(Step("late", unit.label, actual(call), late[i], call.id))
        else:
            matched += 1
            where = " in a delegated agent" if call.helper else ""
            reason = f"called at {call.start:.0f} s{where}"
            steps.append(Step("matched", unit.label, actual(call), reason, call.id))
    never_calls = [c for c in ordered_calls if c.name in never]
    for call in never_calls:
        reason = f"{call.name} is never to be called"
        steps.append(Step("never", None, actual(call), reason, call.id))
    claimed = {u.call for u in units if u.call is not None}
    extra = [c for i, c in enumerate(work) if i not in claimed]
    for call in extra:
        reason = EXTRA_OK if extras_ok else EXTRA_OFF
        steps.append(Step("extra", None, actual(call), reason, call.id))
    base = matched / len(units) if units else 1.0
    penalty = 0.0 if extras_ok else EXTRA_PENALTY * len(extra)
    score = base - NEVER_PENALTY * len(never_calls) - penalty
    return steps, round(max(0.0, score), 3)


def check_line(step: Step) -> str:
    """A state check as the Grader reads it."""
    return f"- check {step.expected}: {step.status} ({step.reason or step.actual or ''})"
