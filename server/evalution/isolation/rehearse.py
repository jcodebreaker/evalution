"""A plan for resetting an app's stores, tried once before any test relies on it (prototype).

The plan names the stores to reset. The rehearsal:

1. stops the app and keeps each planned store as it is now: the **starting state**, and its hash;
2. starts the app and sends a probe carrying a canary, watching every folder the app uses;
3. stops it, and fails if the canary reached nothing planned, or if the app wrote to a file no
   planned store covers;
4. puts every planned store back to the starting state and fails if its hash doesn't match;
5. starts the app again and fails if the canary is still anywhere it can reach;
6. always ends with the starting state put back and the app started, so a rehearsal leaves the
   app exactly as it found it, whatever happened.

Only what the plan names is ever replaced, and only by its own copy from step 1. The copies live
in a folder of evalution's own.
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from evalution.code import SKIP_DIRS
from evalution.isolation import observe

#: Files beside a SQLite database that are part of it.
SIDECARS = ("-wal", "-shm", "-journal")


class FileStore:
    """A store kept in files: one file (with its SQLite sidecars) or one folder."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @property
    def name(self) -> str:
        return str(self.path)

    def _parts(self) -> list[Path]:
        return [self.path, *(self.path.with_name(self.path.name + s) for s in SIDECARS)]

    def covers(self, path: str) -> bool:
        p = Path(path)
        return any(p == part or p.is_relative_to(part) for part in self._parts())

    def digest(self) -> str:
        h = hashlib.sha256()
        for part in self._parts():
            if part.is_dir():
                for sub in sorted(part.rglob("*")):
                    if sub.is_file():
                        h.update(str(sub.relative_to(part)).encode())
                        h.update(sub.read_bytes())
            elif part.is_file():
                h.update(part.name.encode())
                h.update(part.read_bytes())
        return h.hexdigest()

    def keep(self, into: Path) -> None:
        """Copy it into *into*; what doesn't exist is remembered as absent."""
        into.mkdir(parents=True)
        for part in self._parts():
            if part.is_dir():
                shutil.copytree(part, into / part.name, symlinks=True)
            elif part.is_file():
                shutil.copy2(part, into / part.name)

    def put_back(self, kept: Path) -> None:
        """Make it exactly what :meth:`keep` copied: replaced, or removed where it was absent."""
        for part in self._parts():
            if part.is_dir():
                shutil.rmtree(part)
            elif part.exists():
                part.unlink()
            copy = kept / part.name
            if copy.is_dir():
                shutil.copytree(copy, part, symlinks=True)
            elif copy.is_file():
                shutil.copy2(copy, part)

    def holding(self, canary: str) -> list[str]:
        out = []
        for part in self._parts():
            files = [p for p in part.rglob("*") if p.is_file()] if part.is_dir() else [part]
            out += [str(f) for f in files if f.is_file() and observe.holds(str(f), canary)]
        return out


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Rehearsal:
    ok: bool
    steps: list[Step] = field(default_factory=list)
    #: Files outside the plan the canary reached, or the app wrote to.
    leaks: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"{'ok ' if s.ok else 'NO '} {s.name}{': ' + s.detail if s.detail else ''}"
            for s in self.steps
        ]
        lines.append(
            "Proven: the plan resets everything the probe reached."
            if self.ok
            else "Not proven: " + (", ".join(self.leaks) or self.steps[-1].detail)
        )
        return "\n".join(lines)


def _scan(roots: list[Path], canary: str) -> list[str]:
    """Every file under *roots* that holds *canary*."""
    found = []
    for root in roots:
        files = [root] if root.is_file() else root.rglob("*")
        for f in files:
            if any(part in SKIP_DIRS for part in f.parts) or not f.is_file():
                continue
            if observe.holds(str(f), canary):
                found.append(str(f))
    return sorted(set(found))


async def rehearse(
    stores: list[FileStore],
    folder: Path,
    start: Callable[[], Awaitable[int | None]],
    stop: Callable[[], Awaitable[None]],
    probe: Callable[[str], Awaitable[object]],
    workdir: Path,
) -> Rehearsal:
    """Try the plan once on a running app (see the module's docstring).

    :param start: Starts the app and returns its process id, when it has one on this computer.
    :param stop: Stops it, so its stores can be copied whole.
    :param probe: Sends one message carrying the canary it's given.
    :param workdir: An empty folder of evalution's own, for the starting state.
    """
    out = Rehearsal(ok=False)
    canary = f"eval-rehearsal-{secrets.token_hex(6)}"
    kept: list[tuple[FileStore, Path]] = []
    before: dict[str, str] = {}
    started = False

    def step(name: str, ok: bool, detail: str = "") -> bool:
        out.steps.append(Step(name, ok, detail))
        return ok

    def unplanned(paths: list[str]) -> list[str]:
        return [p for p in paths if not any(s.covers(p) for s in stores)]

    try:
        await stop()
        before = {s.name: s.digest() for s in stores}
        for i, store in enumerate(stores):
            store.keep(workdir / str(i))
            kept.append((store, workdir / str(i)))
        step("starting state kept", True, f"{len(kept)} stores")

        pid = await start()
        started = True
        open_files = observe.open_by(pid)[0] if pid else []
        roots = observe.roots_for(folder, open_files, [])
        roots += [s.path.parent for s in stores if not any(s.path.is_relative_to(r) for r in roots)]
        snap = observe.snapshot(roots)
        await probe(canary)
        await stop()
        started = False
        created, changed, _ = observe.diff(snap, observe.snapshot(roots))
        reached = [p for s in stores for p in s.holding(canary)]
        if not step("the probe reached a planned store", bool(reached), ", ".join(reached)):
            out.leaks = unplanned(_scan(roots, canary))
            return out
        stray = unplanned(sorted({*created, *changed}))
        if not step("the app wrote only to planned stores", not stray, ", ".join(stray)):
            out.leaks = stray
            return out

        for store, copy in kept:
            store.put_back(copy)
        moved = [s.name for s in stores if s.digest() != before[s.name]]
        if not step("put back to the starting state", not moved, ", ".join(moved)):
            return out

        pid = await start()
        started = True
        left = _scan(roots, canary)
        if not step("the canary is gone after the reset", not left, ", ".join(left)):
            out.leaks = left
            return out
        out.ok = True
        return out
    finally:
        if started:
            await stop()
        for store, copy in kept:
            store.put_back(copy)
        if kept:
            back = all(s.digest() == before[s.name] for s, _ in kept)
            step("left as it was found", back)
            out.ok = out.ok and back
        await start()
