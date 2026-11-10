"""The areas a learn is split into (docs/architecture.md, "Knowing a target"): groups of watched
files, each small enough for one agent with a small context to understand, chosen with no model.

An area is a folder of the code and what is under it. The partition starts from the top folders;
an area of more than :data:`AREA_FILES` files is split into its subfolders, and while there are
fewer than :data:`MIN_AREAS` the largest that can be split is; past :data:`MAX_AREAS`, the
smallest is folded into its neighbour. Every step depends on the paths alone, never on what the
files hold, so an edit never moves a file to another area and the same files give the same areas,
with the same ids, learn after learn: an area whose files did not move is not learned again. What
an area's agent is given is held to :data:`AREA_CHARS` (:mod:`evalution.writer.areas`).

What an area weighs (ADR 0012) is what the app is built from in it and what real use touched
there: its skeleton entries (:mod:`evalution.project.skeleton`) and their trace hits
(:func:`weight`). Its key (:func:`key`) is what its section was learned from: its files'
fingerprints and its hits in :data:`BANDS`, so a few more calls of the same tool don't move it.

An area of more entries than one agent can cover is learned in pieces (:func:`split`): its
folders first, then its files, and a file that holds too many alone (the tools many agents' settings
list) by its entries. The pieces depend on the paths and the entries alone, never on real use, so
the same app splits the same way learn after learn and a piece whose files did not move is not
learned again.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from evalution.project import focus as focused
from evalution.project.skeleton import KINDS, Entry

#: How many areas a learn is split into, at least and at most, and how much one may hold.
MIN_AREAS = 6
MAX_AREAS = 12
AREA_FILES = 25
AREA_CHARS = 40_000
#: The area of the files at the top of the code folder.
ROOT = "root"
#: Where trace hits are cut into bands for an area's key: 0, 1-9, 10-99, 100 and more.
BANDS = (1, 10, 100)


@dataclass(frozen=True)
class Area:
    """*id*: a short slug, stable while its files are; *folders*: the folders it stands for;
    *size*: how much its agent has to read of it. A piece of a heavy area (:func:`split`) says
    which area it is a piece *of*, what it covers (*label*), and, when it shares a file with
    other pieces, the skeleton *entries* it is held to (their ids; none: every entry at its
    files)."""

    id: str
    folders: tuple[str, ...]
    files: tuple[str, ...]
    size: int
    entries: tuple[str, ...] = ()
    of: str = ""
    label: str = ""


def _folder(path: str, depth: int) -> str:
    parts = PurePosixPath(path).parts[:-1]
    return "/".join(parts[:depth]) if parts else ""


def _slug(folders: Iterable[str]) -> str:
    """A readable id; a long one cut, with a short hash of the whole so no two meet."""
    joined = "+".join(f or ROOT for f in folders)
    slug = re.sub(r"[^A-Za-z0-9_.+-]+", "-", joined).strip("-") or ROOT
    if len(slug) <= 48:
        return slug
    return f"{slug[:42]}~{hashlib.sha256(slug.encode()).hexdigest()[:5]}"


def partition(sizes: Mapping[str, int], does: Collection[str] = ()) -> list[Area]:
    """The areas of the files *sizes* names (``path -> how much there is to read of it``, which
    only says each area's size), in folder order. *does*: the files that shape behaviour
    (:func:`evalution.project.slicing.does`); a folder holding none of them is no area of its own
    but joins its nearest neighbour, so areas follow what the code does, not only where it is."""
    groups: dict[str, dict[str, int]] = {}
    depth: dict[str, int] = {}

    def place(files: Mapping[str, int], level: int) -> None:
        for path, size in files.items():
            key = _folder(path, level)
            # A file above this level stays with the folder it sits in.
            groups.setdefault(key, {})[path] = size
            depth[key] = level

    place(sizes, 1)

    def splittable(key: str) -> bool:
        files = groups[key]
        level = depth[key]
        return len({_folder(p, level + 1) for p in files}) > 1

    def split(key: str) -> None:
        files = groups.pop(key)
        level = depth.pop(key)
        place(files, level + 1)

    changed = True
    while changed:
        changed = False
        for key in sorted(groups):
            if len(groups[key]) > AREA_FILES and splittable(key):
                split(key)
                changed = True
                break
        if not changed and len(groups) < MIN_AREAS:
            candidates = [k for k in groups if splittable(k)]
            if candidates:
                split(max(candidates, key=lambda k: (len(groups[k]), k)))
                changed = True
    areas = [(tuple([k]), files) for k, files in sorted(groups.items())]
    shaping = set(does)
    if shaping:
        areas = _fold_helpers(areas, shaping)
    while len(areas) > MAX_AREAS:
        smallest = min(range(len(areas)), key=lambda i: (len(areas[i][1]), i))
        other = smallest - 1 if smallest > 0 else 1
        first, second = sorted((smallest, other))
        merged = (areas[first][0] + areas[second][0], {**areas[first][1], **areas[second][1]})
        areas[first : second + 1] = [merged]
    return [
        Area(_slug(folders), folders, tuple(sorted(files)), sum(files.values()))
        for folders, files in areas
    ]


Group = tuple[tuple[str, ...], dict[str, int]]


def _fold_helpers(areas: list[Group], shaping: set[str]) -> list[Group]:
    """Fold each folder that shapes no behaviour into its nearest neighbour in folder order (the
    one sharing the longest leading path), while that stays within :data:`AREA_FILES`."""

    def common(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a.split("/"), b.split("/"), strict=False):
            if x != y:
                break
            n += 1
        return n

    out = list(areas)
    i = 0
    while i < len(out):
        folders, files = out[i]
        if set(files) & shaping or len(out) == 1:
            i += 1
            continue
        near = [j for j in (i - 1, i + 1) if 0 <= j < len(out)]
        near = [j for j in near if len(out[j][1]) + len(files) <= AREA_FILES]
        if not near:
            i += 1
            continue
        j = max(near, key=lambda j: (common(out[j][0][0], folders[0]), -j))
        first, second = sorted((i, j))
        out[first : second + 1] = [
            (out[first][0] + out[second][0], {**out[first][1], **out[second][1]})
        ]
        i = first
    return out


# What is the agent, of a repository that holds more


#: What makes an agent: the files that hold these are where its scope starts.
CORE = ("prompt", "tool", "model", "agent")
#: Steps through imports from the core (and from what calls it) still in scope.
HOPS = 2


def _paths(entry: Entry) -> list[str]:
    """The files *entry* is at: its own and, folded, its other places (``path:line``)."""
    return [entry.path, *(p.rsplit(":", 1)[0] for p in entry.places)]


def agent_scope(
    entries: Iterable[Entry],
    edges: Mapping[str, Iterable[str]],
    files: Collection[str],
    focus: str | None = None,
) -> set[str] | None:
    """The files of *files* that are the agent, with no model (next-level C7): those that hold
    its prompts, tools, model calls and agents (:data:`CORE`), what they import within
    :data:`HOPS` steps (*edges*: ``path -> the paths it imports``), and the files holding its
    routes, entry points and state that reach the core or are reached by it within as many, with
    what they import. ``None`` when the skeleton names no core (a language the code map reads
    thinly): then every file is in scope, as before. A web client calling its own ``api.get``, a
    mobile project, migrations: none of them reach the agent's code, so none is in scope.

    *focus* (the target's ``code.focus``, next-level C11): the one agent tested on a platform
    that hosts many. The core is then that agent's files alone (:func:`.focus.agent_files`: its
    definition, its prompts, the tools it names), and the files defining the other agents stay
    out even when something reaches them. A focus no agent has: as without one."""
    known = set(files)
    found = list(entries)
    core = {p for e in found if e.kind in CORE for p in _paths(e)} & known
    if not core:
        return None
    others: set[str] = set()
    if focus and (mine := focused.agent_files(focus, found) & known):
        core = mine
        others = {p for a in focused.agents(found) if a.name != focus for p in _paths(a)} - mine
    graph = {k: [v for v in vs if v in known] for k, vs in edges.items() if k in known}
    back: dict[str, set[str]] = {}
    for rel, hits in graph.items():
        for hit in hits:
            back.setdefault(hit, set()).add(rel)
    near = core | _reach(graph, core) | _reach(back, core)
    linked = {p for e in found if e.kind not in CORE for p in _paths(e) if p in near}
    start = core | linked
    return (start | _reach(graph, start)) - others


def _reach(edges: Mapping[str, Iterable[str]], start: Iterable[str]) -> set[str]:
    begin = set(start)
    seen, front = set(begin), begin
    for _ in range(HOPS):
        front = {hit for rel in front for hit in edges.get(rel, ())} - seen
        seen |= front
    return seen - begin


def outside(files: Iterable[str], taken: Collection[str] = ()) -> list[Area]:
    """Files not part of the agent, one area per top folder (``root`` for the files at the top),
    listed and never learned unless asked; an id an area of the agent already has (*taken*) gets
    ``~outside``."""
    by_folder: dict[str, list[str]] = {}
    for path in sorted(files):
        by_folder.setdefault(_folder(path, 1), []).append(path)
    out = []
    for folder, paths in sorted(by_folder.items()):
        slug = _slug([folder])
        out.append(
            Area(slug if slug not in taken else f"{slug}~outside", (folder,), tuple(paths), 0)
        )
    return out


# What an area weighs, and what its section was learned from


def entries_of(area: Area, entries: Iterable[Entry]) -> list[Entry]:
    """The skeleton entries at a file of *area* (only those it is held to, for a piece that
    shares a file), in the skeleton's order."""
    files = set(area.files)
    held = set(area.entries)
    return [e for e in entries if e.path in files and (not held or e.id in held)]


def weight(entries: Sequence[Entry], uses: Mapping[str, int]) -> int:
    """An area's weight: its skeleton *entries* (its prompts, tools, state, agents, model calls,
    routes and entry points) and how often real use touched them (*uses*: entry id -> count)."""
    return len(entries) + sum(int(uses.get(e.id) or 0) for e in entries)


def band(uses: int) -> int:
    """Which of :data:`BANDS` a count of uses falls in: 0 for none."""
    return bisect.bisect_right(BANDS, uses)


def key(
    area: Area, prints: Mapping[str, str], entries: Sequence[Entry], uses: Mapping[str, int]
) -> str:
    """What *area*'s section is learned from: its files' fingerprints (*prints*) and its entries'
    trace hits in bands. The same key: the section still holds, and no model is asked."""
    body = {
        "files": {p: prints.get(p) for p in area.files},
        "hits": sorted((e.id, band(int(uses.get(e.id) or 0))) for e in entries),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


# A heavy area, in pieces

#: A piece in the making: its files, the entry ids it is held to (none: all of its files'), and
#: how many entries it has.
_Unit = tuple[tuple[str, ...], tuple[str, ...], int]


def split(
    area: Area,
    entries: Sequence[Entry],
    piece: int,
    ceiling: int,
    sizes: Mapping[str, int] | None = None,
) -> list[Area]:
    """*area* as it is learned: itself while it holds at most *ceiling* skeleton *entries*, else
    in pieces of at most *piece* entries each. Its folders are kept whole while they fit, then
    its files, packed in path order; a file of more than *piece* entries alone (the tools many
    agents' settings list) is cut by its entries, in the skeleton's order, into nearly equal
    pieces of that file alone. Files with no entry go with the piece before them (the first,
    after). Real use plays no part, so a busier tool never moves a piece; each is
    ``<area>.<n>``, in the order of its first file. *sizes*: each file's size, which only says
    a piece's."""
    mine = entries_of(area, entries)
    if len(mine) <= ceiling:
        return [area]
    count = Counter(e.path for e in mine)
    folders: dict[str, list[str]] = {}
    for path in area.files:
        folders.setdefault(_folder(path, len(PurePosixPath(path).parts)), []).append(path)

    units: list[_Unit] = []
    for folder in sorted(folders):
        files = folders[folder]
        weight = sum(count[p] for p in files)
        if weight <= piece:
            units.append((tuple(files), (), weight))
            continue
        for path in files:
            if count[path] <= piece:
                units.append(((path,), (), count[path]))
                continue
            ids = [e.id for e in mine if e.path == path]
            cuts = math.ceil(len(ids) / piece)
            for n in range(cuts):
                part = ids[n * len(ids) // cuts : (n + 1) * len(ids) // cuts]
                units.append(((path,), tuple(part), len(part)))

    # A file's cut pieces stand alone; the rest are packed in order, past them.
    pieces: list[list[_Unit]] = [[u] for u in units if u[1]]
    packed: list[list[_Unit]] = []
    for unit in (u for u in units if not u[1]):
        if packed and (unit[2] == 0 or sum(u[2] for u in packed[-1]) + unit[2] <= piece):
            packed[-1].append(unit)
        else:
            packed.append([unit])
    if packed and not any(u[2] for u in packed[0]) and (len(packed) > 1 or pieces):
        # Files with no entry ahead of any that have some: with the next piece.
        (packed[1] if len(packed) > 1 else pieces[0])[:0] = packed.pop(0)
    pieces = sorted(pieces + packed, key=lambda units_of: min(p for u in units_of for p in u[0]))

    sizes = sizes or {}
    out = []
    for n, units_of in enumerate(pieces, 1):
        files = tuple(sorted({p for u in units_of for p in u[0]}))
        held = tuple(i for u in units_of for i in u[1])
        shape = Area(area.id, area.folders, files, 0, held)
        covered = entries_of(shape, mine)
        out.append(
            Area(
                f"{area.id}.{n}",
                tuple(sorted({_folder(p, len(PurePosixPath(p).parts)) for p in files})),
                files,
                sum(int(sizes.get(p) or 0) for p in files),
                held,
                area.id,
                f"part {n} of {len(pieces)}: {_covers(covered, files)}",
            )
        )
    return out


#: Skeleton kinds as a piece's label says them.
_SAID = {"entry": "entry point", "model": "model call", "state": "store"}


def _covers(entries: Sequence[Entry], files: Sequence[str]) -> str:
    """What a piece covers, in a few words: ``20 tools, 1 prompt in agents/team.yaml (search to
    write_file)``."""
    count = Counter(e.kind for e in entries)
    kinds = []
    for kind in (*KINDS, *sorted(set(count) - set(KINDS))):
        if count[kind]:
            word = _SAID.get(kind, kind)
            kinds.append(f"{count[kind]} {word if count[kind] == 1 else word + 's'}")
    said = ", ".join(kinds) or "no entry"
    said += f" in {files[0]}" if len(files) == 1 else f" in {len(files)} files"
    if len(entries) > 1:
        said += f" ({entries[0].name} to {entries[-1].name})"
    elif entries:
        said += f" ({entries[0].name})"
    return said
