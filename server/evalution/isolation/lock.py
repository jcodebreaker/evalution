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


class TargetLock:
    """The lock on *target*'s copies in *data_dir* (see the module's docstring)."""

    def __init__(self, data_dir: Path, target: str) -> None:
        if not _TARGET.match(target):
            raise ValueError(f"not a target name: {target!r}")
        self.target = target
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
                f"{self.target}'s copy of the app is in use by another run, rehearsal or search "
                "(here or from the command line): try again when it ends"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd

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
