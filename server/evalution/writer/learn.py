"""How a learn starts (the page's "Learn", and "Learn again"): from the whole app read with no model
(the code map, its facts and a card per file), a slice of it read by the light model, and what
changed, rather than from whatever few files the writer chooses to open.

Before the writer's first step, with no choice of its own involved:

1. Whether anything moved, said for free when it can be: the traces by the drift check, which
   reads them on its own schedule (:mod:`evalution.project.drift`), the code by the commit and a
   clean tree the last learn saw, else by the file hashes. Learned before and nothing moved: no
   model call and no trace read.
2. Otherwise the recent sessions are read once (the turn keeps them for write_understanding) and
   the code map is brought up to date, a file parsed again only when it moved
   (:mod:`evalution.project.codemap`).
3. A few files moved (:data:`PATCH_FILES`, :data:`PATCH_SHARE`) and the traces did not: the light
   model reads a changed file again only when it is in the slice and its facts or definitions'
   lines moved; the writer gets the parts of the understanding that cite the files and an outline
   of the rest, the files' summaries or cards, the definitions the change touched with one step
   out, the diff, and the names around the change it does not cover yet, and patches in one call.
4. Else the slice is chosen (:mod:`evalution.project.slicing`) and read where it moved, the
   folders and the app are summarized bottom up where they moved (:mod:`evalution.project.modules`),
   and the first message carries what the writer understood last time, what changed, the app and
   its modules, the code map ranked and cut to :data:`MAP_CHARS`, the key limits, the slice's
   summaries and the other files' cards. It writes the understanding again and follows a
   definition with walk_code rather than reading files.
5. The understanding's Details is assembled by the engine from the facts (:mod:`.details`), never
   sent to the writer. After a full learn's save, what the map holds that the understanding never
   names is said back once (:func:`left_out`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from evalution.code import NOTHING_CHANGED, Code, CodeError
from evalution.llm import fenced
from evalution.project import (
    codemap,
    drift,
    filememory,
    knowledge,
    modules,
    slicing,
    understanding,
)
from evalution.project.codemap import CodeMap, Definition
from evalution.project.profile import FINGERPRINT_DOC, read_doc
from evalution.targets import Targets
from evalution.traces.profile import render
from evalution.writer.tools import AgentTurn

_logger = logging.getLogger(__name__)

FIRST = """\
Below: the app as a whole and as its modules, a map of its most used definitions (prompts, tools, \
agents, routes and state marked), its key limits and settings, the files that shape its \
behaviour, summarized, and a card for each other file (its role, names and facts). Write the \
understanding from them, covering the whole app: every prompt, prompt builder, tool, agent, route \
and kind of state belongs in it, by its exact name, and so do the key limits and settings that \
shape its behaviour, with their values. For a definition's detail and how it connects, use \
walk_code(name) (its code, what it uses and what uses it) rather than reading whole files, and \
only when the brief lacks what you need: then write it all in one write_understanding call. A \
Details section of exact facts from the code is added to it by itself: write the rest, and leave \
Details out."""

AGAIN = """\
Write the understanding again from what is below (the app's modules, a map of its most used \
definitions, its key limits and settings, and the files that matter most, summarized), covering \
the whole app: every prompt, prompt builder, tool, agent, route and kind of state belongs in it, \
by its exact name, and so do the key limits and settings that shape its behaviour, with their \
values, whatever the last one said. Take from what you understood last time only its ids, for \
what stays, and what the code cannot say (what the traces showed, the admin's marks). For detail, \
use walk_code(name) rather than reading whole files, the changed and added ones first. A Details \
section of exact facts from the code is added by itself: leave it out. Save it all in one \
write_understanding call."""

PATCH = """\
You learned this app before, and only a few files moved since. Change only the claims and nodes \
about the definitions the change touched. Below: the parts of what you understood that cite \
them, in full; an outline of the rest; each file's summary then and now; the definitions the \
change touched, with their code, what they use and what uses them; the diff; and, when there are \
any, the names around the change your understanding does not cover yet. Edit only what the \
change moves, and cover those names, in one patch_understanding call with its note: replace or \
add the claims and nodes it changes, remove what it makes untrue. Leave the rest as it is. \
read_understanding and walk_code are there only if what is below is not enough."""

#: How much a patch's brief carries of the moved files' diff (or text, without git), of the
#: definitions it touched (each, and all), of the parts citing them, and of the outline: a small
#: change is read in one step on a short brief.
CHANGES_CHARS = 6_000
DEFINITION_CHARS = 1_500
DEFINITIONS_CHARS = 6_000
CITED_CHARS = 8_000
OUTLINE_CHARS_ALL = 6_000
#: What a full learn gets of the code map, and of the cards of the files outside the slice.
MAP_CHARS = 25_000
CARDS_CHARS = 16_000
#: Names said back after a save, or put up front in a patch, at most.
NAMES_SENT = 25
#: Key limits a full learn is given.
LIMITS = 40
#: How much of each claim the outline of the rest shows.
OUTLINE_CHARS = 80

#: A change of at most this many files, or this share of the watched ones, is patched.
PATCH_FILES = 5
PATCH_SHARE = 0.10

#: In a patch's brief, the change's prompts, tools and the like the understanding does not name.
COVER = "also make sure these are covered, by their names"

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


def _by_module(cmap: CodeMap, paths: Iterable[str]) -> list[str]:
    """*paths* grouped by folder, the folders in name order and the files in rank order within."""
    rank = {p: n for n, p in enumerate(cmap.ranked_files())}
    return sorted(set(paths), key=lambda p: (modules.folder_of(p), rank.get(p, len(rank)), p))


def _cut(text: str, limit: int) -> str:
    return text[:limit] + ("\n… (cut)" if len(text) > limit else "")


def _definitions(cmap: CodeMap, keys: list[str], read: Callable[[str], str]) -> str:
    """Each touched definition's code, and one step out by signature: what it uses and what
    uses it."""
    shown = [
        "\n".join(
            [cmap.defs[k].label(), _cut(cmap.code(k, read), DEFINITION_CHARS), cmap.neighbours(k)]
        )
        for k in keys
    ]
    return _cut("\n\n".join(shown), DEFINITIONS_CHARS)


@dataclass(frozen=True)
class Patch:
    """A small change's brief, the definitions it rests on (the touched ones and one step out),
    and the parts of the understanding that cite the moved files."""

    text: str
    keys: list[str]
    cited: list[str]
    definitions: int


def _patch_brief(
    targets: Targets,
    name: str,
    code: Code,
    cmap: CodeMap,
    before: dict,
    moved: filememory.Changes,
    memory: dict,
) -> Patch:
    """What a small change's learn is given: the parts of the understanding that cite the moved
    files in full and an outline of the rest, each file's summary then and now, the definitions
    the change touched with one step out, and the diff, so it can patch in one step. No map: the
    change is all it is about."""
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
            after = now.get(path) or cmap.card(path) if kind != "removed" else "(gone)"
            lines.append(f"{path} ({kind})\n  then: {then}\n  now: {after}")
    touched = set(moved.changed) | set(moved.removed)
    links = knowledge.load(targets, name).get("links") or {}
    parts = understanding.parts(before)
    cited = {key for key, files in links.items() if set(files) & touched and key in parts}
    full = json.dumps({key: parts[key] for key in sorted(cited)}, ensure_ascii=False)
    changed = [*moved.changed, *moved.added]
    diff = _changes_text(code, memory.get("commit"), changed)
    hit = cmap.innermost(_touched(cmap, diff, changed))
    near = {n for k in hit for n in (*cmap.callees(k), *cmap.callers(k))}
    definitions = _definitions(cmap, hit, code.read)
    missing = uncovered(cmap, before, sorted({*hit, *near}))
    names = "\n".join(f"{path}: {', '.join(found)}" for path, found in missing.items())
    text = "\n\n".join(
        [
            PATCH,
            fenced("the parts that cite them", _cut(full, CITED_CHARS)),
            fenced("the rest, in outline", _cut(_outline(before, cited), OUTLINE_CHARS_ALL)),
            fenced("the files that moved", "\n".join(lines)),
            fenced("the definitions the change touched", definitions or "(none in the map)"),
            fenced("the diff", diff or "(nothing readable)"),
            *([fenced(COVER, names)] if names else []),
        ]
    )
    return Patch(text, sorted({*hit, *near}), sorted(cited), len(definitions))


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
        made = await asyncio.to_thread(codemap.build, targets, name, code)
        turn.map = cmap = made.map
        stats = {
            "map_seconds": made.seconds,
            "map_files_parsed": made.parsed,
            "map_defs": len(cmap.defs),
            "map_refs": cmap.refs,
            "static_facts": sum(len(cmap.facts(p)) for p in cmap.files),
        }
        clock("code map")
        turn.details = _by_module(cmap, cmap.files)
        if before is not None and not traces and _small(moved, len(files)):
            # The light model reads a changed file again only when it is in the slice and what it
            # offers moved (its facts, its definitions' lines); never another file.
            sliced = set(memory.get("slice") or ())
            due = [p for p in [*moved.changed, *moved.added] if p in sliced & made.reshaped]
            if due:
                await filememory.index(targets, name, code, ask, due)
            patch = await asyncio.to_thread(
                _patch_brief, targets, name, code, cmap, before, moved, memory
            )
            turn.patch, turn.cited = patch.keys, patch.cited
            stats |= {"patch_chars": patch.definitions, "slice_files": len(due)}
            return Begun(text=patch.text, stats=stats)
        cap = targets.conf(name).code.summarize  # type: ignore[union-attr]
        churn = await asyncio.to_thread(code.churn, slicing.CHURN_MONTHS)
        chosen = slicing.choose(cmap, churn, cap)
        await asyncio.to_thread(filememory.keep_slice, targets, name, chosen)
        await filememory.index(targets, name, code, ask, chosen)
        stats["slice_files"] = len(chosen)
        clock("slice")
        await modules.index(targets, name, cmap, ask)
        clock("modules")
        kept = filememory.load(targets, name)
        drawn = cmap.repo_map(MAP_CHARS)
        limits = "\n".join(f"{d.path}:{d.line} {d.sig}" for d in key_limits(cmap))
        stats["map_chars"] = len(drawn) + len(limits)
        folders = "\n\n".join(f"{f}/: {said}" for f, said in modules.summaries(kept).items())
        rest = [p for p in cmap.ranked_files() if p not in set(chosen)]
        cards = _cut("\n".join(cmap.card(p) for p in rest), CARDS_CHARS)
        parts += [
            fenced("the app", modules.app_summary(kept) or "(not summarized)"),
            fenced("the app's modules", folders or "(none summarized)"),
            fenced("the code map: its most used definitions, by file", drawn or "(empty)"),
            fenced("key limits and settings", limits or "(none found)"),
            filememory.brief(kept, files, moved, chosen),
            fenced("the other files, by rank: role, names, facts", cards or "(none)"),
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
#: Files whose definitions are prose or settings, not code: headings and keys are no names to
#: remind of.
_NOT_CODE = re.compile(r"(^|/)(docs?|adr)/|\.(md|mdx|rst|txt|adoc|ya?ml|json|toml)$", re.IGNORECASE)
#: Names of constants that set how the app behaves.
_LIMIT = re.compile(
    r"BUDGET|MAX|MIN|LIMIT|TIMEOUT|SECONDS|TTL|RETR|THRESHOLD|CAP\b|_CAP|WINDOW|TOKENS|CHARS"
)


def key_limits(cmap: CodeMap) -> list[Definition]:
    """The constants that set the app's limits, highest-ranked first, at most :data:`LIMITS`: those
    of code files named like a limit, or used by a prompt builder or a tool."""
    used = {
        k
        for key, d in cmap.defs.items()
        if d.kind in ("prompt-builder", "tool")
        for k in cmap.callees(key)
    }
    found = [
        cmap.defs[k]
        for k in cmap.rank()
        if cmap.defs[k].kind == "constant"
        and not _NOT_CODE.search(cmap.defs[k].path)
        and (_LIMIT.search(cmap.defs[k].name) or k in used)
    ]
    return found[:LIMITS]


def uncovered(
    cmap: CodeMap, doc: dict, keys: list[str] | None, also: Iterable[Definition] = ()
) -> dict[str, list[str]]:
    """``path -> names``: the prompts, prompt builders, tools, agents, routes and state of code
    files the code map holds (of the definitions *keys*, when given), and the definitions *also*
    (the key limits), that the understanding never mentions, ignoring case; private and generic
    ones left out, at most :data:`NAMES_SENT`."""
    # Details is the engine's: what counts is what the writer itself said.
    rest = understanding.with_details(doc, []) if doc.get("sections") else doc
    said = json.dumps(rest, ensure_ascii=False).casefold()
    out: dict[str, list[str]] = {}
    count = 0
    every = [*cmap.behaviour(keys), *also]
    for d in sorted(every, key=lambda d: (d.path, d.line)):
        name = d.name
        if count >= NAMES_SENT or _NOT_CODE.search(d.path) or name.startswith("_"):
            continue
        if _GENERIC.search(name) or name.casefold() in said:
            continue
        if name not in out.setdefault(d.path, []):
            out[d.path].append(name)
            count += 1
    return {p: n for p, n in out.items() if n}


def left_out(targets: Targets, turn: AgentTurn) -> str | None:
    """Once per full learn, after it saved: what the code map holds that the understanding does not
    name, its key limits too, said back to the writer; ``None`` when nothing is, it was said
    already, or this is a patch (told up front, so that its save ends it)."""
    name = str(turn.session["target"])
    if turn.reminded or not turn.understood or turn.map is None:
        return None
    turn.reminded = True
    doc = understanding.load(targets, name)
    if turn.patch is not None:
        return None  # a patch was told up front
    missing = uncovered(turn.map, doc or {}, None, key_limits(turn.map))
    if not missing:
        return None
    lines = "\n".join(f"{path}: {', '.join(names)}" for path, names in missing.items())
    return f"{LEFT_OUT}\n\n" + fenced("named in the code, missing from the understanding", lines)
