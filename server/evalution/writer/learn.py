"""How a learn starts (the page's "Learn", and "Learn again"): from every watched file summarized,
and from what changed, rather than from whatever few files the writer chooses to open.

Before the writer's first step, with no choice of its own involved:

1. The code is compared with what the last learn saw (:mod:`evalution.project.filememory`), and the
   traces with the fingerprint the profile stands for (:mod:`evalution.project.drift`). Both are
   free: hashes cached by file time, and one read of the recent sessions, which the turn keeps for
   write_understanding.
2. When the target was learned before and neither moved, no model is called at all: the same
   inputs would give the same understanding, so a call could only spend.
3. Otherwise every watched file with no summary for what it is now is summarized by the light model
   (:func:`filememory.index`): all of them the first time, the changed and added ones after.
4. The writer's first message carries what it would otherwise fetch step by step: what it
   understood last time, what changed, a summary of every file, and the trace digest when the
   traces moved. It reads whole files only for detail.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from evalution.llm import fenced
from evalution.project import drift, filememory, understanding
from evalution.project.profile import FINGERPRINT_DOC, read_doc
from evalution.targets import Targets
from evalution.traces.profile import render
from evalution.writer.tools import AgentTurn

FIRST = """\
Every file of the app's code you may read is summarized below. Your understanding should cover \
all of it: read a whole file (read_code) only for detail a summary does not give."""

AGAIN = """\
You learned this app before. What you understood then, what changed since, and a summary of every \
file of its code are below: start from them, not from scratch. Read a whole file only for detail a \
summary does not give, the changed and added ones first. Keep the ids of what stays, change what \
the changes move, and save with write_understanding."""


@dataclass(frozen=True)
class Begun:
    """How a learn starts: *nothing*, what to say instead of calling the model; else *text*, what
    the writer is given with the admin's words."""

    nothing: str | None = None
    text: str = ""


def _trace_changes(baseline: object, taken: dict) -> list[dict] | None:
    """What moved in the traces since *baseline*; ``None`` with no baseline to compare with."""
    if not isinstance(baseline, dict):
        return None
    return [c for c in drift.compare(baseline, taken) if c["kind"] != "code_changed"]


async def begin(targets: Targets, turn: AgentTurn, ask: filememory.Ask) -> Begun:
    """*ask*: the light model, for the summaries."""
    name = str(turn.session["target"])
    found, taken = await turn.take(targets)
    before = understanding.written(targets, name)
    traces = _trace_changes(read_doc(targets, name, FINGERPRINT_DOC), taken)
    code = targets.code(name)
    parts = [AGAIN if before else FIRST]
    if before:
        parts.append(
            fenced("what you understood last time", json.dumps(before, ensure_ascii=False))
        )
    if code is not None:
        files = await asyncio.to_thread(code.hashes)
        moved = filememory.changes(filememory.load(targets, name), files)
        if before and not moved and traces == []:
            commit = filememory.load(targets, name).get("commit")
            return Begun(nothing=f"Nothing changed since {commit[:7] if commit else 'last time'}.")
        await filememory.index(targets, name, code, ask)
        parts.append(filememory.brief(filememory.load(targets, name), files, moved))
    if traces:
        lines = "\n".join(c["detail"] for c in traces)
        parts += [fenced("what moved in the traces", lines)]
        parts += [fenced("the trace digest", render(found))]
    elif traces is None or not before:
        parts.append(fenced("the trace digest", render(found)))
    else:
        parts.append("The traces show nothing new since then.")
    return Begun(text="\n\n".join(parts))
