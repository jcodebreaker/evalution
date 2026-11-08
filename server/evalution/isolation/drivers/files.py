"""A store kept in files on this computer: one file (with its SQLite sidecars) or one folder.

Copies are clones on macOS (``cp -c``: APFS shares the blocks until one side changes, so keeping a
large store is instant and takes no room), and plain copies elsewhere or when cloning fails.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from evalution.isolation import observe
from evalution.isolation.drivers.base import Refused

#: Files beside a SQLite database that are part of it.
SIDECARS = ("-wal", "-shm", "-journal")


def _cp() -> str | None:
    """``cp`` when it can clone (macOS), else None."""
    return shutil.which("cp") if sys.platform == "darwin" else None


def copy(src: Path, dst: Path) -> None:
    """Copy a file or folder to *dst* (which doesn't exist yet), symlinks kept as symlinks.

    A clone where the system can make one, a plain copy otherwise.
    """
    cp = _cp()
    if cp is not None:
        done = subprocess.run(  # noqa: S603 - the system's cp, arguments passed as a list
            [cp, "-c", "-R", "-p", str(src), str(dst)], capture_output=True, check=False
        )
        if done.returncode == 0:
            return
        if dst.is_dir():  # a half-made clone of a folder
            shutil.rmtree(dst)
        elif dst.exists() or dst.is_symlink():
            dst.unlink()
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


class FileStore:
    """A store kept in files: one file (with its SQLite sidecars) or one folder.

    :param allowed_roots: Folders :meth:`put_back` may write in. The launcher passes the roots of
        evalution's own copies, so a plan can never replace the person's own files. None (the
        default) puts back anywhere, as the prototype's rehearsal on a toy app does.
    """

    def __init__(self, path: Path, allowed_roots: Sequence[Path] | None = None) -> None:
        self.path = path.resolve()
        self.allowed_roots = None if allowed_roots is None else [r.resolve() for r in allowed_roots]

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
        into.mkdir(parents=True, exist_ok=True)
        for part in self._parts():
            if part.exists():
                copy(part, into / part.name)

    def put_back(self, kept: Path) -> None:
        """Make it exactly what :meth:`keep` copied: replaced, or removed where it was absent.

        :raises Refused: When the store lies outside every allowed root.
        """
        if self.allowed_roots is not None and not any(
            self.path.is_relative_to(root) for root in self.allowed_roots
        ):
            raise Refused(f"{self.path} isn't inside a copy of evalution's own")
        for part in self._parts():
            if part.is_dir() and not part.is_symlink():
                shutil.rmtree(part)
            elif part.exists() or part.is_symlink():
                part.unlink()
            saved = kept / part.name
            if saved.exists() or saved.is_symlink():
                copy(saved, part)

    def holding(self, canary: str) -> list[str]:
        out = []
        for part in self._parts():
            files = [p for p in part.rglob("*") if p.is_file()] if part.is_dir() else [part]
            out += [str(f) for f in files if f.is_file() and observe.holds(str(f), canary)]
        return out
