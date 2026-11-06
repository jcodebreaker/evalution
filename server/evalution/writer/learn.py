"""How a learn starts (the page's "Learn", and "Learn again"): from every watched file summarized,
and from what changed, rather than from whatever few files the writer chooses to open.

Before the writer's first step, with no choice of its own involved:

1. Whether anything moved, said for free when it can be: the traces by the drift check, which
   reads them on its own schedule (:mod:`evalution.project.drift`), the code by the commit and a
   clean tree the last learn saw, else by the file hashes, cached by file time
   (:mod:`evalution.project.filememory`). Learned before and nothing moved: no model call and no
   trace read, since the same inputs would give the same understanding.
2. Otherwise the recent sessions are read once (the turn keeps them for write_understanding), and
   every watched file with no summary for what it is now is summarized by the light model
   (:func:`filememory.index`): all of them the first time, the changed and added ones after.
3. A few files moved (:data:`PATCH_FILES`, :data:`PATCH_SHARE`) and the traces did not: the writer
   gets the understanding, those files' summaries then and now, and the parts that cite them, and
   edits just those parts (patch_understanding); the rest stays as it was.
4. Else its first message carries what it would otherwise fetch step by step: what it understood
   last time, what changed, a summary of every file, and the trace digest when the traces moved.
   It writes the understanding again from the summaries, the old one giving only its ids and what
   code cannot say, and reads whole files only for detail.
5. Once it saved, the names the summaries of code files give for prompts and definitions that the
   understanding never mentions are said back to it, once (:func:`left_out`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from evalution.llm import fenced
from evalution.project import drift, filememory, knowledge, understanding
from evalution.project.profile import FINGERPRINT_DOC, read_doc
from evalution.targets import Targets
from evalution.traces.profile import render
from evalution.writer.tools import AgentTurn

_logger = logging.getLogger(__name__)

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

PATCH = """\
You learned this app before, and only a few files moved since. Edit only what they change, with \
patch_understanding: replace or add the claims and nodes the files below move, and remove what \
they make untrue. Leave everything else as it is. Read a whole file only for detail its summary \
does not give."""

#: A change of at most this many files, or this share of the watched ones, is patched.
PATCH_FILES = 5
PATCH_SHARE = 0.10

#: Said back once after a learn's save, when the summaries name what it left out.
LEFT_OUT = """\
These are in the code but not in the understanding. Add them (patch_understanding adds without \
writing the rest again), or say in one line why they do not belong; then end your turn."""


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


def _free_quiet(targets: Targets, name: str) -> bool:
    """Whether the traces are quiet by what is already known, with no session read: the drift
    check, which watches them on its own schedule (before runs, daily), counted no trace change
    since the understanding was saved."""
    changes = drift.read(targets, name).get("changes") or []
    return not [c for c in changes if c["kind"] != "code_changed"]


def _small(moved: filememory.Changes, files: int) -> bool:
    """Few enough files moved that the understanding is edited, not written again."""
    count = len(moved.changed) + len(moved.added) + len(moved.removed)
    return not moved.first and 0 < count <= max(PATCH_FILES, PATCH_SHARE * files)


def _patch_brief(
    targets: Targets,
    name: str,
    before: dict,
    moved: filememory.Changes,
    was: dict[str, str],
) -> str:
    """What a small change's learn is given: the understanding, each moved file's summary then and
    now, and the parts of the understanding that cite them."""
    now = filememory.summaries(filememory.load(targets, name))
    lines = []
    for kind, paths in (
        ("changed", moved.changed),
        ("added", moved.added),
        ("removed", moved.removed),
    ):
        for path in paths:
            then = was.get(path, "(no summary)") if kind != "added" else "(new)"
            after = now.get(path, "(not summarized)") if kind != "removed" else "(gone)"
            lines.append(f"{path} ({kind})\n  then: {then}\n  now: {after}")
    touched = set(moved.changed) | set(moved.removed)
    links = knowledge.load(targets, name).get("links") or {}
    cites = [
        f"{key}: {', '.join(sorted(set(files) & touched))}"
        for key, files in sorted(links.items())
        if set(files) & touched
    ]
    return "\n\n".join(
        [
            PATCH,
            fenced("what you understood", json.dumps(before, ensure_ascii=False)),
            fenced("the files that moved", "\n".join(lines)),
            fenced("the parts that cite them", "\n".join(cites) or "(none)"),
        ]
    )


class _Clock:
    """Where a learn's start spends its seconds, step by step, logged once at its end."""

    def __init__(self) -> None:
        self.at = time.perf_counter()
        self.steps: list[str] = []

    def __call__(self, step: str) -> None:
        now = time.perf_counter()
        self.steps.append(f"{step} {now - self.at:.2f}s")
        self.at = now


async def begin(targets: Targets, turn: AgentTurn, ask: filememory.Ask) -> Begun:
    """*ask*: the light model, for the summaries."""
    clock = _Clock()
    try:
        return await _begin(targets, turn, ask, clock)
    finally:
        _logger.info("learn %s: %s", turn.session["target"], ", ".join(clock.steps))


async def _begin(targets: Targets, turn: AgentTurn, ask: filememory.Ask, clock: _Clock) -> Begun:
    name = str(turn.session["target"])
    before = understanding.written(targets, name)
    code = targets.code(name)
    memory = filememory.load(targets, name) if code is not None else {}
    commit = memory.get("commit")
    nothing = Begun(nothing=f"Nothing changed since {commit[:7] if commit else 'last time'}.")
    # Nothing changed, said for free: the traces by the drift check, the code by its commit and a
    # clean tree, else by the hashes. Reading the traces costs seconds, so only a learn does it.
    clock("documents")
    if before is not None and code is not None and _free_quiet(targets, name):
        unmoved = await asyncio.to_thread(filememory.unmoved, memory, code)
        clock("commit and tree")
        if unmoved:
            return nothing
        hashed = await asyncio.to_thread(code.hashes)
        clock("hashes")
        if not filememory.changes(memory, hashed):
            return nothing
    found, taken = await turn.take(targets)
    clock("traces and code state")
    traces = _trace_changes(read_doc(targets, name, FINGERPRINT_DOC), taken)
    parts = [AGAIN if before else FIRST]
    if before:
        parts.append(
            fenced("what you understood last time", json.dumps(before, ensure_ascii=False))
        )
    if code is not None:
        files = taken["code"]["files"]
        moved = filememory.changes(memory, files)
        was = filememory.summaries(memory)
        await filememory.index(targets, name, code, ask)
        clock("summaries")
        if before is not None and not traces and _small(moved, len(files)):
            turn.patch = sorted({*moved.changed, *moved.added})
            return Begun(text=_patch_brief(targets, name, before, moved, was))
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
    """Once per learn, after it saved: what the summaries of code files name that the
    understanding does not (of the moved files only, for a small change), said back to the
    writer; ``None`` when nothing is, or it was said already."""
    name = str(turn.session["target"])
    if turn.reminded or not turn.understood:
        return None
    turn.reminded = True
    doc = understanding.load(targets, name)
    memory = filememory.load(targets, name)
    missing = filememory.uncovered(memory, doc or {}, turn.patch)
    if not missing:
        return None
    lines = "\n".join(f"{path}: {', '.join(names)}" for path, names in missing.items())
    return f"{LEFT_OUT}\n\n" + fenced("named in the code, missing from the understanding", lines)
