"""One holder at a time of a target's copies: a run, a rehearsal or finding isolation, from the
server or the CLI alike.

Each starts copies named ``eval-<target>-<slot>``; two at once would collide on those names, or
take down the other's copy. :class:`TargetLock` is an advisory lock on a file in evalution's data
folder (``isolation/<target>/copy.lock``), so it holds across processes that share the folder,
and the system lets it go when the holder exits, however it ends. It never waits: a second holder
is told at once, with :class:`Busy`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import TracebackType

from evalution.isolation.launch import LaunchError

try:
    import fcntl
except ImportError:  # pragma: no cover - not on this system: no lock, as before
    fcntl = None  # type: ignore[assignment]

_TARGET = re.compile(r"^[A-Za-z0-9_-]+$")


class Busy(LaunchError):
    """Another run, rehearsal or search holds the target's copies."""


#: What may hold the lock, written in its file beside the pid so a second holder is told which.
HOLDERS = ("run", "rehearsal", "search")


class TargetLock:
    """The lock on *target*'s copies in *data_dir* (see the module's docstring), held by a
    *holder* of :data:`HOLDERS`."""

    def __init__(self, data_dir: Path, target: str, holder: str = "run") -> None:
        if not _TARGET.match(target):
            raise ValueError(f"not a target name: {target!r}")
        if holder not in HOLDERS:
            raise ValueError(f"not a holder: {holder!r}")
        self.target = target
        self.holder = holder
        self.path = data_dir / "isolation" / target / "copy.lock"
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        """Take the lock, or say who can't wait.

        :raises Busy: Another holder has it, in this process or another.
        """
        if self._fd is not None or fcntl is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise Busy(
                f"{self.target}'s copy is in use by a {self._holder()}: wait for it or stop it"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {self.holder}\n".encode())
        self._fd = fd

    def _holder(self) -> str:
        """Who holds the lock, as its file says; all three when it doesn't say (yet)."""
        try:
            said = self.path.read_text().split()
        except OSError:
            said = []
        return said[1] if len(said) > 1 and said[1] in HOLDERS else "run, rehearsal or search"

    def release(self) -> None:
        """Let it go; nothing when not held."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def __enter__(self) -> TargetLock:
        self.acquire()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
