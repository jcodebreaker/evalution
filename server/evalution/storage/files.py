"""Files a run keeps beside its evidence (ADR 0010): a page try's screenshots and video, too big
for the database. :class:`Files` is what the rest reads and writes through; :class:`LocalFiles`
keeps them in a folder, ``<data_dir>/files/<run>/<case>.<try>/<name>``.

Only a server with a data folder of its own has them: a page is only played locally or in CI, so a
hosted workspace never holds any, and an uploaded run keeps their names only. Deleting a run
deletes its files.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from evalution.core.case import SLUG

FOLDER = "files"
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: A file's name: one path segment, no hidden file and no way out of its folder.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class FileNameError(ValueError):
    """A run, try or file name that can't be a file of a run."""


def _checked(run: str, case: str, n: int, name: str | None = None) -> str:
    """The try's folder name, ``<case>.<n>``, once every part is safe."""
    if not _RUN_ID.match(run):
        raise FileNameError(f"not a run id: {run!r}")
    if not SLUG.match(case) or isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise FileNameError(f"not a try: {case!r} {n!r}")
    if name is not None and (not _NAME.match(name) or ".." in name):
        raise FileNameError(f"not a file name: {name!r}")
    return f"{case}.{n}"


class Files(Protocol):
    """A run's files, by try. Names are one segment (letters, digits, ``.``, ``_``, ``-``); any
    other is refused with :class:`FileNameError`."""

    def put(self, run: str, case: str, n: int, name: str, data: bytes) -> None:
        """Keep *data* as *name* of try *n* of *case*, replacing one of that name."""

    def path(self, run: str, case: str, n: int, name: str) -> Path:
        """Where *name* of the try is, or is to be written by something that writes its own file
        (a browser's video), its folder made."""

    def open(self, run: str, case: str, n: int, name: str) -> BinaryIO:
        """*name* of the try, to read.

        :raises FileNotFoundError: The try has no such file.
        """

    def list(self, run: str, case: str, n: int) -> list[str]:
        """The try's file names, sorted; none for a try that has none."""

    def delete_run(self, run: str) -> int:
        """Forget every file of *run*; how many went."""


class LocalFiles:
    """:class:`Files` in a folder of this computer: ``<data_dir>/files``."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / FOLDER

    def _folder(self, run: str, case: str, n: int, name: str | None = None) -> Path:
        return self.root / run / _checked(run, case, n, name)

    def put(self, run: str, case: str, n: int, name: str, data: bytes) -> None:
        folder = self._folder(run, case, n, name)
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / f".{name}.part"
        part.write_bytes(data)
        part.chmod(0o600)
        part.replace(folder / name)

    def path(self, run: str, case: str, n: int, name: str) -> Path:
        folder = self._folder(run, case, n, name)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / name

    def open(self, run: str, case: str, n: int, name: str) -> BinaryIO:
        return (self._folder(run, case, n, name) / name).open("rb")

    def list(self, run: str, case: str, n: int) -> list[str]:
        folder = self._folder(run, case, n)
        if not folder.is_dir():
            return []
        return sorted(p.name for p in folder.iterdir() if p.is_file() and _NAME.match(p.name))

    def delete_run(self, run: str) -> int:
        if not _RUN_ID.match(run):
            raise FileNameError(f"not a run id: {run!r}")
        folder = self.root / run
        if not folder.is_dir():
            return 0
        gone = sum(1 for p in folder.rglob("*") if p.is_file())
        shutil.rmtree(folder)
        return gone
