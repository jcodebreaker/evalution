"""What every store driver does, and the one way they run Docker.

A driver is the deterministic hand behind a plan's store: it keeps the store's starting state,
puts it back, says whether it changed, and says where a canary landed. The model never acts on a
store; it only names which driver to use and on what.

Every driver refuses, with :class:`Refused`, to act on something that isn't evalution's own. A
refusal happens before any command runs against the thing refused.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import IO, Protocol

#: Seconds one Docker command may take: a dump, a copy or a tar of a large store included.
DOCKER_SECONDS = 600


class Refused(Exception):
    """A driver refusing to act on something that isn't evalution's own."""


class DockerError(RuntimeError):
    """A Docker command failed; the message holds what it said."""


class Driver(Protocol):
    """One store of a plan, kept and put back by deterministic code.

    The methods are plain (not async): rehearsal calls them with ``asyncio.to_thread``.
    """

    #: The plan's id for this store.
    name: str

    def covers(self, path: str) -> bool:
        """Whether a path on this computer is part of this store (always False when it isn't
        kept in files here)."""
        ...

    def digest(self) -> str:
        """A hash that changes if and only if the store's content does, parts left out excepted."""
        ...

    def keep(self, into: Path) -> None:
        """Save the store's current state under *into*, an empty folder of evalution's own."""
        ...

    def put_back(self, kept: Path) -> None:
        """Make the store exactly what :meth:`keep` saved in *kept*."""
        ...

    def holding(self, canary: str) -> list[str]:
        """Where *canary* is in the store, in words a person can check."""
        ...


def docker(
    *args: str,
    stdin: IO[bytes] | None = None,
    stdout: IO[bytes] | None = None,
    timeout: float = DOCKER_SECONDS,
    ok: tuple[int, ...] = (0,),
) -> str:
    """Run ``docker *args`` and return what it printed (empty when *stdout* takes it).

    :param ok: Exit codes that aren't a failure (``grep`` finding nothing exits 1).
    :raises DockerError: When Docker isn't installed, or the command exits with another code.
    """
    exe = shutil.which("docker")
    if exe is None:
        raise DockerError("docker isn't installed")
    proc = subprocess.run(  # noqa: S603 - a fixed program, arguments passed as a list
        [exe, *args],
        stdin=stdin if stdin is not None else subprocess.DEVNULL,
        stdout=stdout if stdout is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if proc.returncode not in ok:
        said = proc.stderr.decode(errors="replace").strip()
        raise DockerError(f"docker {args[0]} failed ({proc.returncode}): {said}")
    return proc.stdout.decode(errors="replace") if stdout is None else ""
