"""A run as a record (docs/judging.md, "The record"): what every reader of a stored run asks of it.
A try keeps its judgements, newest last; the verdict of a test and what it can be compared with are
read from those and nothing else, so every view says the same of one document.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any

from evalution.core.case import DEFAULT_PASS_MARKS
from evalution.core.judgement import Judgement, Outcome, ResultKey, Role, Verdict, VerdictKind
from evalution.core.rules import (
    RULES_VERSION,
    current,
    distrusted,
    judgement,
    verdict,
    verdict_note,
)

SCHEMA = 2
#: Where a run played: ``hosted``, by a hosted server against an app on the internet, or ``local``,
#: on someone's computer or in CI (``evalution run``, uploaded or not). Runs of one app in two
#: places aren't one series: the app, its data and its network differ.
WHERE = ("hosted", "local")
#: How long an environment's name (``--env staging``) may be.
ENV_NAME_CHARS = 64


def environment(where: str, name: Any = None) -> dict[str, Any]:
    """A run's ``environment``.

    :raises ValueError: An unknown *where*, or a *name* that isn't a short text.
    """
    if where not in WHERE:
        raise ValueError(f"environment: where is one of {', '.join(WHERE)}")
    if name is not None and not isinstance(name, str):
        raise ValueError("environment: the name must be text")
    name = (name or "").strip() or None
    if name is not None and (len(name) > ENV_NAME_CHARS or not name.isprintable()):
        raise ValueError(f"environment: the name is at most {ENV_NAME_CHARS} printable characters")
    return {"where": where, "name": name}


def environment_of(run: Mapping[str, Any]) -> tuple[str, str | None]:
    """Where *run* played, as a key to group or match runs by."""
    found = run.get("environment") or {}
    return found.get("where") or "local", found.get("name")


def judgements(entry: Mapping[str, Any]) -> list[Judgement]:
    """The current judgement of each try of a test."""
    return [current(t) for t in entry.get("tries") or []]


def verdict_of(entry: Mapping[str, Any]) -> Verdict:
    """How a test of a run came out."""
    return verdict(judgements(entry), bool(entry.get("must_hold")))


def noted(entry: Mapping[str, Any]) -> dict[str, str]:
    """``note``, what goes on a test's verdict line beside the verdict, when there is something."""
    note = verdict_note(judgements(entry))
    return {"note": note} if note else {}


def verdict_kind(entry: Mapping[str, Any]) -> str | None:
    """The API's word for it; a test that was not played has none."""
    kind = verdict_of(entry).kind
    return None if kind is VerdictKind.NOT_RUN else kind.value


def unjudged_reasons(entry: Mapping[str, Any]) -> list[str]:
    """Why tries of a test could not be judged, each reason once."""
    found: dict[str, None] = {}
    for judged in judgements(entry):
        if judged.outcome is Outcome.UNJUDGED:
            for check in judged.checks:
                if check.role is Role.MUST and check.unjudged:
                    found[check.unjudged.value] = None
    return list(found)


def originally(entry: Mapping[str, Any]) -> str | None:
    """The verdict a test had under the rules it was first judged by, when today's rules say
    otherwise. A rules change is not the app getting better or worse, so readers mark it."""
    now = verdict_kind(entry)
    tries = entry.get("tries") or []
    was = entry.get("legacy_verdict")
    if was is None and tries:
        first = [Judgement.model_validate(t["judgements"][0]) for t in tries]
        if any(a.rules != b.rules for a, b in zip(first, judgements(entry), strict=True)):
            kind = verdict(first, bool(entry.get("must_hold"))).kind
            was = None if kind is VerdictKind.NOT_RUN else kind.value
    return was if was is not None and was != now else None


def pass_marks(run: Mapping[str, Any]) -> dict[str, float]:
    """The pass marks *run* was graded with."""
    return dict(run.get("pass_marks") or DEFAULT_PASS_MARKS)


def judge_model(run: Mapping[str, Any]) -> str | None:
    return (run.get("judge") or {}).get("model")


def agent_models(run: Mapping[str, Any]) -> list[str]:
    """The models the agent itself ran on in *run*, as its traces say; none for a run stored before
    they were kept."""
    found = {
        model
        for entry in run.get("cases", {}).values()
        for attempt in entry.get("tries") or []
        for model in attempt.get("models") or []
    }
    return sorted(found)


def checks_made_under(run: Mapping[str, Any]) -> int:
    """The rules *run*'s checks were made under: those it was played under, however it was read
    again since."""
    return int(run.get("rules_from") or run.get("rules") or 0)


def key_of(run: Mapping[str, Any], stem: str) -> ResultKey:
    """What a result of test *stem* in *run* can be compared by."""
    return ResultKey(
        case_version=(run.get("case_versions") or {}).get(stem),
        rules=int(run.get("rules") or 0),
        rules_from=checks_made_under(run),
        judge_model=judge_model(run),
        pass_marks=tuple(sorted(pass_marks(run).items())),
    )


def same_footing(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Whether two runs were judged the same way: their numbers can be set side by side."""
    model = judge_model(a)
    return (
        model is not None
        and model == judge_model(b)
        and a.get("rules") == b.get("rules")
        and checks_made_under(a) == checks_made_under(b)
    )


def add_judgement(attempt: dict[str, Any], found: Judgement) -> None:
    """Judgements are only ever added."""
    attempt["judgements"].append(found.model_dump(mode="json"))


def regrade_rules(run: dict[str, Any]) -> int:
    """Read *run*'s checks again under today's rules: free, offline, and adds a judgement only to a
    try whose judgement was made under other rules. How many it added. The checks stay the ones made
    then, so it isn't comparable with a run whose checks were made today (``rules_from``)."""
    added = 0
    if run.get("rules") != RULES_VERSION:
        run["rules_from"] = checks_made_under(run)
    for entry in run["cases"].values():
        for attempt in entry.get("tries") or []:
            found = current(attempt)
            if found.rules == RULES_VERSION:
                continue
            add_judgement(
                attempt,
                judgement(
                    found.checks,
                    found.judge,
                    found.route,
                    found.rules if found.rules_from is None else found.rules_from,
                ),
            )
            added += 1
    run["rules"] = RULES_VERSION
    return added


def _needs(entry: Mapping[str, Any]) -> int | None:
    """How many rubric lines the run's copy of a test needs, when not all."""
    return (((entry.get("case") or {}).get("reply")) or {}).get("pass")


def distrust(run: dict[str, Any], unreliable: Mapping[str, Set[str]]) -> int:
    """Read *run* again with the rubric lines, by test, whose grader a person disagrees with too
    often (:func:`evalution.core.rules.distrusted`), and those since trusted again. Like a regrade,
    it adds a judgement, and only to a try that reads differently. How many it added. A run under
    older rules is read under today's first, so its tries don't stand under two."""
    changed = [
        (attempt, found)
        for stem, entry in run["cases"].items()
        for attempt in entry.get("tries") or []
        # A try not judged yet (still being played, or kept from before judgements) has nothing
        # a person's labels could change.
        if attempt.get("judgements")
        and (found := distrusted(current(attempt), unreliable.get(stem, set()), _needs(entry)))
    ]
    if changed and run.get("rules") != RULES_VERSION:
        regrade_rules(run)
        return distrust(run, unreliable)
    for attempt, found in changed:
        add_judgement(attempt, found)
    return len(changed)
