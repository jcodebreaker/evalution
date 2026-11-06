"""How a learn starts (the page's "Learn", and "Learn again"): from every watched file summarized,
and from what changed, rather than from whatever few files the writer chooses to open.

Before the writer's first step, with no choice of its own involved:

1. The code is compared with what the last learn saw: the same commit on a clean tree is enough,
   with no file hashed (:mod:`evalution.project.filememory`), and the
   traces with the fingerprint the profile stands for (:mod:`evalution.project.drift`). Both are
   free: hashes cached by file time, and one read of the recent sessions, which the turn keeps for
   write_understanding.
2. When the target was learned before and neither moved, no model is called at all: the same
   inputs would give the same understanding, so a call could only spend.
3. Otherwise every watched file with no summary for what it is now is summarized by the light model
   (:func:`filememory.index`): all of them the first time, the changed and added ones after.
4. The writer's first message carries what it would otherwise fetch step by step: what it
   understood last time, what changed, a summary of every file, and the trace digest when the
   traces moved. It writes the understanding again from the summaries, the old one giving only its
   ids and what code cannot say, and reads whole files only for detail.
5. Once it saved, the names the summaries give for prompts, definitions and state that the
   understanding never mentions are said back to it, once (:func:`left_out`).
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
Every file of the app's code you may read is summarized below. Write the understanding from them, \
covering the whole app: every prompt, tool, role and kind of state they name belongs in it. Read a \
whole file (read_code) only for detail a summary does not give."""

AGAIN = """\
Write the understanding again from the summaries below, covering the whole app: every prompt, \
tool, role and kind of state they name belongs in it, whatever the last one said. Take from what \
you understood last time only its ids, for what stays, and what the summaries cannot say (what the \
traces showed, the admin's marks). Read a whole file (read_code) only for detail a summary does \
not give, the changed and added ones first. Save with write_understanding."""

#: Said back once after a learn's save, when the summaries name what it left out.
LEFT_OUT = """\
These are in the code but not in the understanding. Add them with write_understanding, or say in \
one line why they do not belong; then end your turn."""


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
    found, taken = await drift.take(targets, name, drift.PROFILE_SESSIONS, with_code=False)
    before = understanding.written(targets, name)
    traces = _trace_changes(read_doc(targets, name, FINGERPRINT_DOC), taken)
    code = targets.code(name)
    memory = filememory.load(targets, name) if code is not None else {}
    commit = memory.get("commit")
    nothing = f"Nothing changed since {commit[:7] if commit else 'last time'}."
    # The commit and a clean tree say it without hashing a file; else the hashes below do.
    quiet = before is not None and code is not None and traces == []
    if quiet and await asyncio.to_thread(filememory.unmoved, memory, code):
        return Begun(nothing=nothing)
    if code is not None:
        taken["code"] = await asyncio.to_thread(code.state)
    turn.taken = (found, taken)
    parts = [AGAIN if before else FIRST]
    if before:
        parts.append(
            fenced("what you understood last time", json.dumps(before, ensure_ascii=False))
        )
    if code is not None:
        files = taken["code"]["files"]
        moved = filememory.changes(memory, files)
        if before and not moved and traces == []:
            return Begun(nothing=nothing)
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


def left_out(targets: Targets, turn: AgentTurn) -> str | None:
    """Once per learn, after it saved: what the summaries name that the understanding does not,
    said back to the writer; ``None`` when nothing is, or it was said already."""
    name = str(turn.session["target"])
    if turn.reminded or not turn.understood:
        return None
    turn.reminded = True
    doc = understanding.load(targets, name)
    missing = filememory.uncovered(filememory.load(targets, name), doc or {})
    if not missing:
        return None
    lines = "\n".join(f"{path}: {', '.join(names)}" for path, names in missing.items())
    return f"{LEFT_OUT}\n\n" + fenced("named in the code, missing from the understanding", lines)
