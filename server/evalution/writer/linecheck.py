"""Trying a plan's rubric lines before they run (docs/judging.md, "Lines tried before they run").

A line a grader can read two ways fails a good reply or passes a bad one, and a run only shows that
after it has cost tries (docs/accuracy.md: the office line). So before the writer shows a plan, its
lines are tried on replies written to meet them and to miss them:

1. One call on the drafting model (:data:`~evalution.writer.prompts.LINE_DRAFT_INSTRUCTIONS`)
   writes a reply that meets every line and, for each line, a reply that misses only it, a sharper
   wording of it, and the exact words it asks for when exact words are all it asks for.
2. The set's Grader grades them through :func:`evalution.judging.rubric.grade_reply`, the function
   a run grades with: the good reply against every line (one call), each bad reply against its own
   line (one call each). A line it isn't sure of is graded once more there, as in a run.

A plan of N lines costs 2 + N calls, and up to 1 + N more for lines the Grader wasn't sure of. A
line about what the assistant did rather than said is left out: a written reply has no tool calls
to show it by.

A line is flagged when its good reply fails it, its bad reply passes it, or the Grader stays unsure
on either. Its suggestion is a code check (``reply.contains`` or ``reply.lacks``) when the line asks
for exact words and those words tell its two replies apart, else the sharper wording. A drafting or
grading call that fails flags nothing: a missing check never holds a plan up.

Lives in ``writer``, not ``judging``: it is a step of planning, it reads the writer's prompt, and
it uses the Grader from above, as a run does (the import layers allow only that way round).
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalution.core.case import Case
from evalution.core.evidence import Clipped, TryEvidence, TurnRecord
from evalution.core.judgement import CheckResult, Outcome, WhyUnjudged
from evalution.judging.checks import says
from evalution.judging.rubric import grade_reply
from evalution.llm import Caller, ask_json, fenced, json_answer
from evalution.writer.prompts import LINE_DRAFT_INSTRUCTIONS

_logger = logging.getLogger(__name__)

#: Why a line is flagged: its good reply failed it, its bad reply passed it, or the Grader stayed
#: unsure of one of them.
WHY = ("good_failed", "bad_passed", "unsure")


@dataclass(frozen=True)
class LineDraft:
    """What the drafting call wrote for one line."""

    bad: str
    rewrite: str
    contains: tuple[str, ...] = ()
    lacks: tuple[str, ...] = ()
    #: About what the assistant did, which a written reply can't show: not tried.
    acts: bool = False


@dataclass(frozen=True)
class Drafts:
    good: str
    lines: tuple[LineDraft, ...]


def _words(value: Any) -> tuple[str, ...]:
    said = value if isinstance(value, list) else []
    return tuple(w.strip() for w in said if isinstance(w, str) and w.strip())


def parse_drafts(text: str, rubric: Sequence[str]) -> Drafts:
    """The drafting call's answer read strictly: a good reply, and an entry for every line by its
    number.

    :raises ValueError: No JSON object, no good reply, or a line without its entry.
    """
    data = json_answer(text)
    good = data.get("good")
    if not isinstance(good, str) or not good.strip():
        raise ValueError("good: must be text")
    entries = data.get("lines")
    if not isinstance(entries, list):
        raise ValueError("lines: must be a list")
    by_id = {str(e.get("id")).strip(): e for e in entries if isinstance(e, dict)}
    lines = []
    for n, line in enumerate(rubric, 1):
        entry = by_id.get(str(n))
        bad = entry.get("bad") if entry else None
        if not isinstance(bad, str) or not bad.strip():
            raise ValueError(f"no bad reply for {line!r}")
        rewrite = entry.get("rewrite") if entry else None
        lines.append(
            LineDraft(
                bad=bad.strip(),
                rewrite=rewrite.strip() if isinstance(rewrite, str) and rewrite.strip() else line,
                contains=_words(entry.get("contains")),
                lacks=_words(entry.get("lacks")),
                acts=entry.get("acts") is True,
            )
        )
    return Drafts(good=good.strip(), lines=tuple(lines))


def _drafting_input(case: Case, messages: Sequence[str]) -> str:
    numbered = "\n".join(f"{n}. {line}" for n, line in enumerate(case.rubric, 1))
    return "\n\n".join(
        [
            fenced("the person's messages", "\n\n".join(messages) or "(none)"),
            fenced("what a good reply does", case.reference or "(no reference)"),
            fenced("the rubric", numbered),
        ]
    )


def written_try(messages: Sequence[str], reply: str) -> TryEvidence:
    """A try that never ran: the case's messages, and *reply* as the last answer. No traces, so the
    Grader is told it can't see what the assistant did."""
    said = list(messages) or [""]
    turns = tuple(
        TurnRecord(
            kind="message",
            message=Clipped(text=message),
            reply=Clipped(text=reply if at == len(said) - 1 else ""),
            started=0.0,
            ended=0.0,
        )
        for at, message in enumerate(said)
    )
    return TryEvidence(case="plan", n=0, t0=0.0, seconds=0.0, turns=turns)


def why_flagged(good: CheckResult, bad: CheckResult) -> str | None:
    """Which of :data:`WHY` the gradings of a line's two replies come to; ``None`` when it read
    both right. A grading that failed (no answer) says nothing either way."""
    if good.outcome is Outcome.FAILED:
        return "good_failed"
    if bad.outcome is Outcome.PASSED:
        return "bad_passed"
    if WhyUnjudged.JUDGE_UNSURE in (good.unjudged, bad.unjudged):
        return "unsure"
    return None


def suggestion(line: str, draft: LineDraft, good: str) -> dict[str, Any] | None:
    """What to put in place of *line*: a code check when its exact words tell the good reply from
    the bad, as ``reply.contains`` and ``reply.lacks`` read them; else the sharper wording, unless
    that is the line itself."""

    def has_all(reply: str, words: Sequence[str]) -> bool:
        return all(says(reply, w) for w in words)

    def has_any(reply: str, words: Sequence[str]) -> bool:
        return any(says(reply, w) for w in words)

    contains, lacks = draft.contains, draft.lacks
    if contains and has_all(good, contains) and not has_all(draft.bad, contains):
        return {"contains": list(contains)}
    if lacks and not has_any(good, lacks) and has_any(draft.bad, lacks):
        return {"lacks": list(lacks)}
    if " ".join(draft.rewrite.split()).casefold() == " ".join(line.split()).casefold():
        return None
    return {"line": draft.rewrite}


async def weak_lines(case: Case, drafter: Caller, grader: Caller) -> list[dict[str, Any]]:
    """The lines of *case* to flag, each ``{"line", "why", "use"?}`` (``use``: what
    :func:`swapped` puts in its place). Never raises."""
    if not case.rubric:
        return []
    messages = [t.message for t in case.turns if t.is_message]
    try:
        drafts = await ask_json(
            drafter,
            LINE_DRAFT_INSTRUCTIONS,
            _drafting_input(case, messages),
            lambda text: parse_drafts(text, case.rubric),
        )
    except Exception:
        _logger.warning("writer: rubric lines not tried", exc_info=True)
        return []
    tried = [(line, d) for line, d in zip(case.rubric, drafts.lines, strict=True) if not d.acts]
    if not tried:
        return []
    good, *bad = await asyncio.gather(
        grade_reply(grader, written_try(messages, drafts.good), "", [line for line, _ in tried]),
        *(grade_reply(grader, written_try(messages, d.bad), "", [line]) for line, d in tried),
    )
    flags = []
    for (line, draft), good_check, bad_graded in zip(tried, good.checks, bad, strict=True):
        why = why_flagged(good_check, bad_graded.checks[0])
        if why is None:
            continue
        flag: dict[str, Any] = {"line": line, "why": why}
        if use := suggestion(line, draft, drafts.good):
            flag["use"] = use
        flags.append(flag)
    return flags


def swapped(file: Mapping[str, Any], line: str, use: Mapping[str, Any]) -> dict[str, Any]:
    """*file* with rubric *line* replaced by *use*: a new wording in its place, or the line gone and
    its words added to ``reply.contains`` or ``reply.lacks``. A ``reply.pass`` above the lines left
    comes down to them.

    :raises ValueError: *line* isn't in the rubric.
    """
    out = copy.deepcopy(dict(file))
    reply = out.setdefault("reply", {})
    rubric = list(reply.get("rubric") or [])
    at = rubric.index(line)
    if "line" in use:
        rubric[at] = str(use["line"])
    else:
        del rubric[at]
        for key in ("contains", "lacks"):
            if use.get(key):
                have = list(reply.get(key) or [])
                reply[key] = have + [w for w in use[key] if w not in have]
    if rubric:
        reply["rubric"] = rubric
        if isinstance(reply.get("pass"), int):
            reply["pass"] = min(reply["pass"], len(rubric))
    else:
        reply.pop("rubric", None)
        reply.pop("pass", None)
    return out
