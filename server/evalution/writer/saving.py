"""Checking and keeping an understanding, one way for every writer: the chat's write_understanding
and a learn's areas and overview (:mod:`evalution.writer.learn`).

A save keeps what the engine owns beside what was written: Details assembled from the facts
(:mod:`evalution.project.details`), the numbers and paths from the traces, a new drift baseline,
the knowledge links and what the learn saw of the code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from evalution.code import Code, CodeError
from evalution.project import details, drift, filememory, knowledge, understanding
from evalution.project.codemap import CodeMap
from evalution.suite import library
from evalution.targets import Targets


def in_code(cmap: CodeMap | None, code: Code | None) -> Callable[[str, str], bool]:
    """Whether the file or folder a ref names (a full path, its end, or a folder) holds a name:
    as a definition in the code map, else in its text."""

    def holds(path: str, name: str) -> bool:
        if cmap is not None:
            rel = path.strip("/")
            for d in cmap.defs.values():
                near = d.path == rel or d.path.endswith(f"/{rel}") or d.path.startswith(f"{rel}/")
                if d.name == name and near:
                    return True
        try:
            return code is not None and name in code.read(path)
        except (CodeError, OSError):
            return False

    return holds


def traced(found: Mapping[str, Any]) -> set[str]:
    """The tools the traces show: offered to the model, else called."""
    known = found["tools_offered_known"]
    return {n for n, t in found["tools"].items() if t["offered"] or not known}


async def check(
    targets: Targets,
    name: str,
    args: Mapping[str, Any],
    found: Mapping[str, Any],
    cmap: CodeMap | None,
) -> tuple[dict[str, Any], list[str]]:
    """``(what was written, read strictly; the problems)``."""
    try:
        templates = library.load_templates(targets.config.library_dir)
    except library.LibraryError:
        templates = []
    return await asyncio.to_thread(
        understanding.check,
        args,
        traced(found),
        in_code(cmap, targets.code(name)),
        {t.id for t in templates},
        understanding.admin_priorities(targets, name),
    )


async def keep(
    targets: Targets,
    name: str,
    written: dict[str, Any],
    taken: tuple[dict[str, Any], dict[str, Any]],
    cmap: CodeMap | None,
    detail_paths: Iterable[str] | None,
    read: Iterable[str] = (),
    replace: bool = False,
) -> dict[str, Any]:
    """Save *written* (checked) as the new understanding, with its Details: of *detail_paths*
    when a learn gives them, else as they were. *replace*: in place of the last version, one
    this learn saved as its areas landed (:func:`keep_partial`)."""
    found, fingerprint = taken
    if detail_paths is not None and cmap is not None:
        memory = await asyncio.to_thread(filememory.load, targets, name)
        claims = details.assemble(cmap, memory, detail_paths)
    else:
        claims = understanding.details_of(understanding.load(targets, name))
    written = understanding.with_details(written, claims)
    drift.keep_baseline(targets, name, fingerprint)
    saved = understanding.save(targets, name, understanding.build(written, found), replace=replace)
    code = targets.code(name)
    if code is not None:
        await asyncio.to_thread(knowledge.written, targets, name, code, list(read))
        await asyncio.to_thread(filememory.learned, targets, name, code)
    return saved


def keep_partial(
    targets: Targets,
    name: str,
    written: dict[str, Any],
    found: Mapping[str, Any],
    replace: bool,
) -> dict[str, Any]:
    """Save *written* (checked) while a learn goes, so the page shows the areas as they land: its
    Details as they were, and nothing else the engine keeps moved (the drift baseline, the
    knowledge links, what the learn saw of the code), which only a finished learn sets.
    *replace*: in place of the version this learn saved before."""
    claims = understanding.details_of(understanding.load(targets, name))
    whole = understanding.build(understanding.with_details(written, claims), found)
    return understanding.save(targets, name, whole, replace=replace)
