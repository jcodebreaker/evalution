"""A target's page map as the test writer learns it and a person approves it, kept current like a
claim (ADR 0010, after v1; ADR 0005).

The map itself is data a fixed script follows (:mod:`evalution.runs.page.map`); what is kept here
is its life around the target's ``page:`` block, under ``<data_dir>/page-maps/<target>/``:

* ``proposed.json``: a map the writer proposed and rehearsed on evalution's copy (status
  ``tried``), with the rehearsal's screenshots, waiting for a person to approve it once.
* ``map.json``: the map in use, saved into the target's ``page:`` block when it was approved,
  with every version in ``versions/<n>.json`` (the newest :data:`VERSIONS_KEPT`), so it can be
  undone. Its status is ``approved``, ``stale`` (a commit changed a file its evidence cites) or
  ``broken`` (the check before a run didn't find what it names).

A map's evidence cites project files (``evidence: {entry: {file, line}}``), with their hashes kept
here (:attr:`KeptMap.evidence_hashes`). :func:`mark_stale` is the claims' rule for them, as the
isolation plan's is (:func:`evalution.project.isolating.mark_stale`): free, no model, and only
commits move it. A map in use is only followed while it is the target's ``page:`` block as it was
saved: one edited by hand since is the person's, and nothing here marks it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from evalution.isolation.plan import hashes
from evalution.project.isolating import committed
from evalution.targets import Targets

_logger = logging.getLogger(__name__)

FORMAT = 1
VERSIONS_KEPT = 20
#: What a stale map's report says, after what was said before.
STALE_NOTE = "Out of date: a commit changed {files}."
_TARGET = re.compile(r"^[A-Za-z0-9_-]+$")
#: Entries whose evidence names code, as ``path:line``.
_CITE = "{file}:{line}"


@dataclass
class KeptMap:
    """A page map and how far it is proven.

    :param page: The ``page:`` block, as a run reads it.
    :param status: ``tried`` (rehearsed, waiting for approval), ``approved``, ``stale`` or
        ``broken``.
    :param evidence_hashes: ``path -> sha256`` of each project file its evidence cites.
    :param report: What the rehearsal, or the last check before a run, found, in plain words.
    :param shots: The rehearsal's screenshots, kept in the file store at *shots_at*
        (``{run, case, n}``).
    :param missing: What the last check before a run didn't find on the page, in words.
    :param stale_files: The files a commit changed under the evidence, when marked ``stale``.
    """

    target: str
    page: dict[str, Any]
    status: str = "tried"
    evidence_hashes: dict[str, str] = field(default_factory=dict)
    report: str = ""
    shots: list[str] = field(default_factory=list)
    shots_at: dict[str, Any] | None = None
    missing: list[str] = field(default_factory=list)
    stale_files: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({"version": FORMAT, **asdict(self)}, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> KeptMap:
        """:raises ValueError: Not a kept page map, or one saved by a newer evalution."""
        data = json.loads(text)
        if not isinstance(data, dict) or "target" not in data or "page" not in data:
            raise ValueError("not a kept page map")
        version = data.pop("version", FORMAT)
        if not isinstance(version, int) or version > FORMAT:
            raise ValueError(f"page map format {version} is newer than this evalution")
        return cls(**data)


def cited(page: dict[str, Any]) -> list[str]:
    """The project files *page*'s evidence cites, as ``path:line``."""
    evidence = page.get("evidence") if isinstance(page.get("evidence"), dict) else {}
    out = []
    for said in evidence.values():
        if isinstance(said, dict) and isinstance(said.get("file"), str):
            out.append(_CITE.format(file=said["file"], line=said.get("line") or 1))
    return out


def hashes_of(page: dict[str, Any], root: Path) -> dict[str, str]:
    """``path -> sha256`` of each project file *page*'s evidence cites, under *root*."""
    return hashes(root, cited(page))


def moved(found: dict[str, str], root: Path) -> list[str]:
    """The files of *found* (``path -> sha256``) that changed or are gone under *root*."""
    out = []
    for rel, digest in sorted(found.items()):
        path = root / rel
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            out.append(rel)
    return out


def same(a: Any, b: Any) -> bool:
    """Whether two ``page:`` blocks are the same map, whatever status each says."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False

    def plain(block: dict[str, Any]) -> str:
        return json.dumps({k: v for k, v in block.items() if k != "status"}, sort_keys=True)

    return plain(a) == plain(b)


class PageMapStore:
    """Each target's page maps under ``<data_dir>/page-maps/<target>/`` (see the module's
    docstring)."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "page-maps"

    def _dir(self, target: str) -> Path:
        if not _TARGET.match(target):
            raise ValueError(f"not a target name: {target!r}")
        return self.root / target

    def _versions(self, target: str) -> list[tuple[int, Path]]:
        folder = self._dir(target) / "versions"
        if not folder.is_dir():
            return []
        return sorted((int(p.stem), p) for p in folder.glob("*.json") if p.stem.isdigit())

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def _read(self, path: Path) -> KeptMap | None:
        return KeptMap.from_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    # Waiting for approval

    def proposed(self, target: str) -> KeptMap | None:
        """The map tried on the copy and waiting for a person's approval; ``None`` for none.

        :raises ValueError: A file that isn't a kept map this evalution reads.
        """
        return self._read(self._dir(target) / "proposed.json")

    def propose(self, kept: KeptMap) -> None:
        """Keep *kept*, tried, as the one waiting for approval, in place of any before it."""
        kept.updated = time.time()
        self._write(self._dir(kept.target) / "proposed.json", kept.to_json())

    def withdraw(self, target: str) -> None:
        (self._dir(target) / "proposed.json").unlink(missing_ok=True)

    # In use

    def load(self, target: str) -> KeptMap | None:
        """The map in use, as last saved; ``None`` when there is none.

        :raises ValueError: A file that isn't a kept map this evalution reads.
        """
        return self._read(self._dir(target) / "map.json")

    def save(self, kept: KeptMap) -> int:
        """Store *kept* as the map in use and as a new version; its number."""
        kept.updated = time.time()
        found = self._versions(kept.target)
        number = found[-1][0] + 1 if found else 1
        text = kept.to_json()
        self._write(self._dir(kept.target) / "versions" / f"{number}.json", text)
        self._write(self._dir(kept.target) / "map.json", text)
        for _, old in found[: max(0, len(found) + 1 - VERSIONS_KEPT)]:
            old.unlink()
        return number

    def version(self, target: str) -> int | None:
        found = self._versions(target)
        return found[-1][0] if found else None

    def history(self, target: str) -> list[tuple[int, KeptMap]]:
        """Every kept version with its number, the oldest first; the last is the map in use."""
        return [
            (n, KeptMap.from_json(p.read_text(encoding="utf-8"))) for n, p in self._versions(target)
        ]

    def undo(self, target: str) -> KeptMap | None:
        """Drop the version in use and go back to the one before; ``None`` when none is left.

        :raises LookupError: The target has no map in use.
        """
        found = self._versions(target)
        if not found:
            raise LookupError(f"{target} has no page map")
        found.pop()[1].unlink()
        current = self._dir(target) / "map.json"
        if not found:
            current.unlink(missing_ok=True)
            return None
        text = found[-1][1].read_text(encoding="utf-8")
        self._write(current, text)
        return KeptMap.from_json(text)


def store(targets: Targets) -> PageMapStore:
    return PageMapStore(targets.config.data_dir)


def block_of(targets: Targets, name: str) -> dict[str, Any] | None:
    """*name*'s ``page:`` block as its config has it now; ``None`` when it has none."""
    try:
        found = targets.conf(name).options.get("page")
    except LookupError:
        return None
    return found if isinstance(found, dict) else None


def in_use(targets: Targets, name: str) -> KeptMap | None:
    """The kept map *name* plays by: the one in use, while the target's ``page:`` block is still
    that map. ``None`` when it has none, or its block was edited by hand since."""
    try:
        kept = store(targets).load(name)
    except ValueError:
        return None
    if kept is None or not same(kept.page, block_of(targets, name)):
        return None
    return kept


def _root(targets: Targets, name: str) -> Path | None:
    code = targets.code(name)
    return code.root.resolve() if code is not None else None


def mark_stale(targets: Targets, name: str) -> list[str]:
    """Save *name*'s approved map again as ``stale`` when a commit changed a file its evidence
    cites; the files, or nothing when it stays as it is. Free: no model, no app."""
    root = _root(targets, name)
    kept = in_use(targets, name)
    if root is None or kept is None or kept.status != "approved":
        return []
    files = committed(moved(kept.evidence_hashes, root), root)
    if not files:
        return []
    kept.status = "stale"
    kept.stale_files = files
    note = STALE_NOTE.format(files=", ".join(files))
    kept.report = f"{kept.report}\n\n{note}".strip()
    store(targets).save(kept)
    _logger.info("page map: %s's is out of date (%s)", name, ", ".join(files))
    return files


async def mark_stale_quietly(targets: Targets, name: str) -> None:
    try:
        await asyncio.to_thread(mark_stale, targets, name)
    except Exception:
        _logger.warning("page map: %s's not checked", name, exc_info=True)


def to_check(targets: Targets, name: str) -> KeptMap | None:
    """The map in use when a run should check it on the copy before it plays: marked stale, found
    broken by the last check (the page may have been put right since), or a file its evidence
    cites changed since (committed or not). ``None`` when it needn't be."""
    kept = in_use(targets, name)
    if kept is None or kept.status not in ("approved", "stale", "broken"):
        return None
    if kept.status in ("stale", "broken"):
        return kept
    root = _root(targets, name)
    return kept if root is not None and moved(kept.evidence_hashes, root) else None


def checked(targets: Targets, name: str, kept: KeptMap, missing: list[str]) -> KeptMap:
    """Save what the check before a run found: *kept* approved again, its evidence's hashes taken
    afresh, when nothing was *missing*; else ``broken``, saying what wasn't there."""
    if missing:
        kept.status = "broken"
        kept.missing = list(missing)
        kept.report = (
            f"Checked before a run on evalution's copy: the page has no {', '.join(missing)}. "
            "Re-learn the page to propose a new map."
        )
    else:
        kept.status = "approved"
        kept.missing, kept.stale_files = [], []
        if (root := _root(targets, name)) is not None:
            kept.evidence_hashes = hashes_of(kept.page, root)
        kept.report = (
            "Checked again before a run on evalution's copy: everything it names is there."
        )
    store(targets).save(kept)
    return kept


def brief(targets: Targets, name: str) -> dict[str, Any] | None:
    """What Home says of the page map: only one a commit left out of date, or one the check before
    a run found broken, with the files or what wasn't there."""
    kept = in_use(targets, name)
    if kept is None or kept.status not in ("stale", "broken"):
        return None
    return {
        "status": kept.status,
        "stale_files": kept.stale_files,
        "missing": kept.missing,
    }
