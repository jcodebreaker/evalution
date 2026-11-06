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

from evalution.code import NOTHING_CHANGED, Code, CodeError
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
You learned this app before, and only a few files moved since. Below: the parts of what you \
understood that cite them, in full; an outline of the rest; each file's summary then and now; and \
what changed in them. Edit only what the change moves, in one patch_understanding call with its \
note: replace or add the claims and nodes it changes, remove what it makes untrue. Leave the rest \
as it is. read_understanding and read_code are there only if what is below is not enough."""

#: How much of the moved files' diff (or text, without git) a patch's brief carries.
CHANGES_CHARS = 16_000
#: How much of each claim the outline of the rest shows.
OUTLINE_CHARS = 80

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


def _parts(doc: dict) -> dict[str, dict]:
    """Each claim and node by ``claim:<id>`` or ``node:<id>``."""
    found = {f"claim:{c['id']}": c for sec in doc.get("sections") or [] for c in sec["claims"]}
    return found | {f"node:{n['id']}": n for n in doc.get("nodes") or []}


def _outline(doc: dict, shown: set[str]) -> str:
    """The understanding by ids and titles, what is shown in full left out: enough to keep ids
    and to see where a new claim goes, at a fraction of the whole."""
    lines = [f"one_line: {doc.get('one_line') or ''}"]
    lines += [
        f"node:{n['id']} ({n.get('kind')}): {n.get('title')}"
        for n in doc.get("nodes") or []
        if f"node:{n['id']}" not in shown
    ]
    for section in doc.get("sections") or []:
        lines.append(f"section {section['id']}: {section.get('title')}")
        lines += [
            f"  claim:{c['id']}: {c['text'][:OUTLINE_CHARS]}"
            for c in section["claims"]
            if f"claim:{c['id']}" not in shown
        ]
    for key in ("priorities", "starters"):
        lines += [f"{key[:-1]}:{i['id']}: {i.get('title')}" for i in doc.get(key) or []]
    return "\n".join(lines)


def _changes_text(code: Code, since: str | None, paths: list[str]) -> str:
    """What changed in *paths*: the diff since the last learn's commit, else their text now."""
    text = ""
    if since:
        try:
            text = code.diff(since, set(paths))
        except CodeError:
            text = ""
        if text == NOTHING_CHANGED:  # moved by fingerprint alone: its text is what's new
            text = ""
    if not text:
        for path in paths:
            try:
                text += fenced(path, code.read(path)) + "\n"
            except (CodeError, OSError):
                continue
    return text[:CHANGES_CHARS] + ("\n… (cut)" if len(text) > CHANGES_CHARS else "")


def _patch_brief(
    targets: Targets,
    name: str,
    code: Code,
    before: dict,
    moved: filememory.Changes,
    memory: dict,
) -> str:
    """What a small change's learn is given: the parts of the understanding that cite the moved
    files in full and an outline of the rest, each file's summary then and now, and the change
    itself, so it can patch in one step."""
    was = filememory.summaries(memory)
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
    parts = _parts(before)
    cited = {key for key, files in links.items() if set(files) & touched and key in parts}
    full = {key: parts[key] for key in sorted(cited)}
    return "\n\n".join(
        [
            PATCH,
            fenced("the parts that cite them", json.dumps(full, ensure_ascii=False) or "(none)"),
            fenced("the rest, in outline", _outline(before, cited)),
            fenced("the files that moved", "\n".join(lines)),
            fenced(
                "what changed in them",
                _changes_text(code, memory.get("commit"), [*moved.changed, *moved.added])
                or "(nothing readable)",
            ),
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
        await filememory.index(targets, name, code, ask)
        clock("summaries")
        if before is not None and not traces and _small(moved, len(files)):
            turn.patch = sorted({*moved.changed, *moved.added})
            brief = await asyncio.to_thread(
                _patch_brief, targets, name, code, before, moved, memory
            )
            return Begun(text=brief)
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
