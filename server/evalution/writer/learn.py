"""How a learn starts (the page's "Learn", and "Learn again"): from the whole app, drawn without a
model as a code map and summarized by the light model, and from what changed, rather than from
whatever few files the writer chooses to open.

Before the writer's first step, with no choice of its own involved:

1. Whether anything moved, said for free when it can be: the traces by the drift check, which
   reads them on its own schedule (:mod:`evalution.project.drift`), the code by the commit and a
   clean tree the last learn saw, else by the file hashes, cached by file time
   (:mod:`evalution.project.filememory`). Learned before and nothing moved: no model call and no
   trace read, since the same inputs would give the same understanding.
2. Otherwise the recent sessions are read once (the turn keeps them for write_understanding);
   every watched file with no summary for what it is now is summarized by the light model
   (:func:`filememory.index`); and the code map is brought up to date, a file parsed again only
   when it moved (:mod:`evalution.project.codemap`).
3. A few files moved (:data:`PATCH_FILES`, :data:`PATCH_SHARE`) and the traces did not: the writer
   gets the parts of the understanding that cite them and an outline of the rest, the files'
   summaries then and now, the definitions the change touched with their code and one step out
   (what they use, what uses them), and the diff. It edits just those parts
   (patch_understanding); the rest stays as it was.
4. Else the folders' summaries are brought up to date (:mod:`evalution.project.modules`), and the
   first message carries what the writer understood last time, what changed, the modules, the
   code map ranked and cut to :data:`MAP_CHARS`, and the summaries of the :data:`TOP_FILES` files
   that rank highest. It writes the understanding again from them, the old one giving only its ids
   and what code cannot say, and follows a definition with walk_code rather than reading files.
5. Once it saved, the prompts, tools, agents, routes and state of the map that the understanding
   never names are said back to it, once (:func:`left_out`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from evalution.code import NOTHING_CHANGED, Code, CodeError
from evalution.llm import fenced
from evalution.project import codemap, drift, filememory, knowledge, modules, understanding
from evalution.project.codemap import CodeMap
from evalution.project.profile import FINGERPRINT_DOC, read_doc
from evalution.targets import Targets
from evalution.traces.profile import render
from evalution.writer.tools import AgentTurn

_logger = logging.getLogger(__name__)

FIRST = """\
Below: the app's code as its modules, a map of its most used definitions (prompts, tools, agents, \
routes and state marked), and the files that matter most, summarized. Write the understanding \
from them, covering the whole app: every prompt, tool, agent, route and kind of state belongs in \
it, by its exact name. For a definition's detail and how it connects, use walk_code(name) (its \
code, what it uses and what uses it) rather than reading whole files."""

AGAIN = """\
Write the understanding again from what is below (the app's modules, a map of its most used \
definitions, and the files that matter most, summarized), covering the whole app: every prompt, \
tool, agent, route and kind of state belongs in it, by its exact name, whatever the last one said. \
Take from what you understood last time only its ids, for what stays, and what the code cannot say \
(what the traces showed, the admin's marks). For detail, use walk_code(name) rather than reading \
whole files, the changed and added ones first. Save with write_understanding."""

PATCH = """\
You learned this app before, and only a few files moved since. Below: the parts of what you \
understood that cite them, in full; an outline of the rest; each file's summary then and now; the \
definitions the change touched, with their code, what they use and what uses them; and the diff. \
Edit only what the change moves, in one patch_understanding call with its note: replace or add \
the claims and nodes it changes, remove what it makes untrue. Leave the rest as it is. \
read_understanding and walk_code are there only if what is below is not enough."""

#: How much of the moved files' diff (or text, without git) a patch's brief carries, and of the
#: definitions it touched.
CHANGES_CHARS = 8_000
DEFINITIONS_CHARS = 16_000
#: What a full learn gets of the code map, and how many files' summaries.
MAP_CHARS = 40_000
TOP_FILES = 25
#: Names said back after a save, at most.
NAMES_SENT = 25
#: How much of each claim the outline of the rest shows.
OUTLINE_CHARS = 80

#: A change of at most this many files, or this share of the watched ones, is patched.
PATCH_FILES = 5
PATCH_SHARE = 0.10

#: Said back once after a learn's save, when the code map holds what it left out.
LEFT_OUT = """\
These prompts, tools, agents, routes and state are in the code but not in the understanding. Add \
them (patch_understanding adds without writing the rest again), or say in one line why they do \
not belong; then end your turn."""


@dataclass(frozen=True)
class Begun:
    """How a learn starts: *nothing*, what to say instead of calling the model; else *text*, what
    the writer is given with the admin's words."""

    nothing: str | None = None
    text: str = ""
    #: What making the code map took, and how much of it was sent.
    stats: dict[str, float] = field(default_factory=dict)


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


def _touched(cmap: CodeMap, diff: str, paths: list[str]) -> list[str]:
    """The definitions the change touched: by the diff's hunks, or every one of a file with no
    diff (new, or not a checkout)."""
    spans = codemap.spans(diff)
    return [k for path in paths for k in cmap.overlapping(path, spans.get(path))]


def _definitions(cmap: CodeMap, keys: list[str], read: Callable[[str], str]) -> str:
    """Each touched definition's code, and one step out: what it uses and what uses it."""
    parts = [f"{cmap.defs[k].label()}\n{cmap.code(k, read)}\n{cmap.neighbours(k)}" for k in keys]
    text = "\n\n".join(parts)
    return text[:DEFINITIONS_CHARS] + ("\n… (cut)" if len(text) > DEFINITIONS_CHARS else "")


def _patch_brief(
    targets: Targets,
    name: str,
    code: Code,
    cmap: CodeMap,
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
    changed = [*moved.changed, *moved.added]
    diff = _changes_text(code, memory.get("commit"), changed)
    hit = _touched(cmap, diff, changed)
    return "\n\n".join(
        [
            PATCH,
            fenced("the parts that cite them", json.dumps(full, ensure_ascii=False) or "(none)"),
            fenced("the rest, in outline", _outline(before, cited)),
            fenced("the files that moved", "\n".join(lines)),
            fenced(
                "the definitions the change touched",
                _definitions(cmap, hit, code.read) or "(none in the map)",
            ),
            fenced("the diff", diff or "(nothing readable)"),
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
    stats: dict[str, float] = {}
    if code is not None:
        files = taken["code"]["files"]
        moved = filememory.changes(memory, files)
        await filememory.index(targets, name, code, ask)
        clock("summaries")
        made = await asyncio.to_thread(codemap.build, targets, name, code)
        turn.map = cmap = made.map
        stats = {
            "map_seconds": made.seconds,
            "map_files_parsed": made.parsed,
            "map_defs": len(cmap.defs),
            "map_refs": cmap.refs,
        }
        clock("code map")
        if before is not None and not traces and _small(moved, len(files)):
            turn.patch = sorted({*moved.changed, *moved.added})
            brief = await asyncio.to_thread(
                _patch_brief, targets, name, code, cmap, before, moved, memory
            )
            return Begun(text=brief, stats={**stats, "map_chars": len(brief)})
        await modules.index(targets, name, cmap, ask)
        clock("modules")
        kept = filememory.load(targets, name)
        shown = cmap.ranked_files()[:TOP_FILES]
        drawn = cmap.repo_map(MAP_CHARS)
        stats["map_chars"] = len(drawn)
        folders = "\n\n".join(f"{f}/: {said}" for f, said in modules.summaries(kept).items())
        parts += [
            fenced("the app's modules", folders or "(none summarized)"),
            fenced("the code map: its most used definitions, by file", drawn or "(empty)"),
            filememory.brief(kept, files, moved, shown),
        ]
    if traces:
        lines = "\n".join(c["detail"] for c in traces)
        parts += [fenced("what moved in the traces", lines)]
        parts += [fenced("the trace digest", render(found))]
    elif traces is None or not before:
        parts.append(fenced("the trace digest", render(found)))
    else:
        parts.append("The traces show nothing new since then.")
    return Begun(text="\n\n".join(parts), stats=stats)


#: Names that say nothing of the app: HTTP verbs, short acronyms, dates, file names.
_GENERIC = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Y{2,4}(-?MM(-?DD)?)?|README|LICENSE|CHANGELOG)$"
    r"|\.\w{1,5}$|^\d|^[A-Z]{1,4}$"
)
#: Files whose definitions are prose, not code.
_PROSE = re.compile(r"(^|/)(docs?|adr)/|\.(rst|txt|adoc)$", re.IGNORECASE)


def uncovered(cmap: CodeMap, doc: dict, paths: list[str] | None) -> dict[str, list[str]]:
    """``path -> names``: the prompts, tools, agents, routes and state the code map holds (in
    *paths*, when given) that the understanding never mentions, ignoring case; private, generic
    and prose ones left out, at most :data:`NAMES_SENT`."""
    said = json.dumps(doc, ensure_ascii=False).casefold()
    out: dict[str, list[str]] = {}
    count = 0
    for d in sorted(cmap.behaviour(paths), key=lambda d: (d.path, d.line)):
        name = d.name
        if count >= NAMES_SENT or _PROSE.search(d.path) or name.startswith("_"):
            continue
        if _GENERIC.search(name) or name.casefold() in said:
            continue
        if name not in out.setdefault(d.path, []):
            out[d.path].append(name)
            count += 1
    return {p: n for p, n in out.items() if n}


def left_out(targets: Targets, turn: AgentTurn) -> str | None:
    """Once per learn, after it saved: what the code map holds that the understanding does not
    name (of the moved files only, for a small change), said back to the writer; ``None`` when
    nothing is, or it was said already."""
    name = str(turn.session["target"])
    if turn.reminded or not turn.understood or turn.map is None:
        return None
    turn.reminded = True
    doc = understanding.load(targets, name)
    missing = uncovered(turn.map, doc or {}, turn.patch)
    if not missing:
        return None
    lines = "\n".join(f"{path}: {', '.join(names)}" for path, names in missing.items())
    return f"{LEFT_OUT}\n\n" + fenced("named in the code, missing from the understanding", lines)
