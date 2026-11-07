"""The areas a learn is split into (docs/architecture.md, "Knowing a target"): groups of watched
files, each small enough for one agent with a small context to understand, chosen with no model.

An area is a folder of the code and what is under it. The partition starts from the top folders;
an area of more than :data:`AREA_FILES` files is split into its subfolders, and while there are
fewer than :data:`MIN_AREAS` the largest that can be split is; past :data:`MAX_AREAS`, the
smallest is folded into its neighbour. Every step depends on the paths alone, never on what the
files hold, so an edit never moves a file to another area and the same files give the same areas,
with the same ids, learn after learn: an area whose files did not move is not learned again. What
an area's agent is given is held to :data:`AREA_CHARS` (:mod:`evalution.writer.areas`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

#: How many areas a learn is split into, at least and at most, and how much one may hold.
MIN_AREAS = 6
MAX_AREAS = 12
AREA_FILES = 25
AREA_CHARS = 40_000
#: The area of the files at the top of the code folder.
ROOT = "root"


@dataclass(frozen=True)
class Area:
    """*id*: a short slug, stable while its files are; *folders*: the folders it stands for;
    *size*: how much its agent has to read of it."""

    id: str
    folders: tuple[str, ...]
    files: tuple[str, ...]
    size: int


def _folder(path: str, depth: int) -> str:
    parts = PurePosixPath(path).parts[:-1]
    return "/".join(parts[:depth]) if parts else ""


def _slug(folders: Iterable[str]) -> str:
    joined = "+".join(f or ROOT for f in folders)
    return re.sub(r"[^A-Za-z0-9_.+-]+", "-", joined).strip("-")[:48] or ROOT


def partition(sizes: Mapping[str, int]) -> list[Area]:
    """The areas of the files *sizes* names (``path -> how much there is to read of it``, which
    only says each area's size), in folder order."""
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
