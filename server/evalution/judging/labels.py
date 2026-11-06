"""Labelled examples: a person's answer on one rubric line of one try, kept in the workspace as a
document of kind :data:`LABEL`.

Its key is ``target/set/case/line/run/try``, the line by :func:`evalution.core.rules.line_key`:
lines have no id of their own, and their words survive a regrade where their place need not. The
body holds what the person said (``holds``: the line really holds there), what the grader said when
they said it (``said``), what the grader says now (``graded``, which a regrade moves, by ``judge``),
and where the evidence is (``run``, ``case``, ``try`` and the line's ``check``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evalution.core.judgement import JudgeStamp
from evalution.storage import Docs

LABEL = "line-label"


def key(target: str, example: Mapping[str, Any]) -> str:
    e = example
    return f"{target}/{e['set']}/{e['case']}/{e['line']}/{e['run']}/{e['try']}"


def grader_of(stamp: JudgeStamp | None) -> dict[str, Any]:
    """Who graded, as an example keeps it: the model and the prompts, either of which changing
    means the line is graded another way."""
    return {
        "model": stamp.model if stamp else None,
        "prompts": stamp.prompt_hash if stamp else None,
    }


def put(docs: Docs, target: str, example: Mapping[str, Any]) -> None:
    docs.put(LABEL, key(target, example), dict(example))


def clear(docs: Docs, target: str, example: Mapping[str, Any]) -> bool:
    """Forget the person's answer; whether there was one."""
    return docs.delete(LABEL, key(target, example))


def of(docs: Docs, target: str, set_name: str) -> list[dict[str, Any]]:
    """Every labelled example of *target*'s set *set_name*."""
    return [body for _, body, _ in docs.items(LABEL, f"{target}/{set_name}/")]


def regraded(docs: Docs, target: str, example: Mapping[str, Any], graded: bool, judge: Any) -> None:
    """What the grader *judge* says of the example's line now."""
    put(docs, target, {**example, "graded": graded, "judge": judge})
