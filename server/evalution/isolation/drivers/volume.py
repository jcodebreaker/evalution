"""A Docker volume of evalution's own, reached through a throwaway container.

Every action runs in ``docker run --rm -i -v <volume>:/v <image>``, with no network, and moves
data only through that container's stdin and stdout. Nothing on this computer is mounted into it:
the Docker VM may not see this computer's temporary folders.

* **keep** saves a tar of the volume as ``volume.tar`` in the folder kept.
* **put_back** empties the volume, dotfiles included, then unpacks that tar into it.
* **digest** is computed inside the container: each entry's type, mode and name (and a link's
  target), then each file's ``sha256``, all in path order, so it's the same for the same content
  whenever it's taken. Modification times aren't counted.
* **holding** is a ``grep`` inside the container, named ``volume:<name>/<path>``.

Containers that use the volume should be stopped (or idle) while it's kept or put back.
It refuses any volume not named ``eval-...`` or ``eval_...`` (evalution's own compose copy).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from evalution.isolation.drivers.base import DockerError, Refused, docker

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_EMPTY = "find /v -mindepth 1 -maxdepth 1 -exec rm -rf {} +"
_LIST = (
    "cd /v && find . -exec stat -c '%F %a %N' {} + | sort"
    " && find . -type f -exec sha256sum {} + | sort -k2"
)


class VolumeStore:
    """One named Docker volume.

    :param volume: Its name; must start with ``eval-`` or ``eval_``.
    :param image: The small image the throwaway container runs (needs ``sh``, ``tar``,
        ``find``, ``stat``, ``sha256sum`` and ``grep``).
    :raises Refused: For a volume not evalution's own, before any command runs.
    """

    def __init__(self, volume: str, image: str = "alpine:3.20") -> None:
        if not volume.startswith(("eval-", "eval_")) or not _NAME.match(volume):
            raise Refused(f"volume {volume!r} isn't one of evalution's own (eval-... or eval_...)")
        self.volume = volume
        self.image = image
        self.name = f"volume:{volume}"

    def _run(self, *command: str, **kwargs) -> str:
        """Run *command* in a throwaway container with the volume at ``/v``."""
        return docker(
            "run", "--rm", "-i", "--network", "none", "--label", "evalution.driver=volume",
            "-v", f"{self.volume}:/v", self.image, *command,
            **kwargs,
        )  # fmt: skip

    def _must_exist(self) -> None:
        """``docker run -v`` would make a missing volume; reading one is a mistake instead."""
        try:
            docker("volume", "inspect", self.volume)
        except DockerError as e:
            raise DockerError(f"volume {self.volume!r} doesn't exist") from e

    def covers(self, path: str) -> bool:
        """Whether *path*, a place the copy's canary search names as
        ``container:volume:NAME/path``, is in this volume."""
        where = path.partition(":")[2]
        return where == self.name or where.startswith(f"{self.name}/")

    def digest(self) -> str:
        self._must_exist()
        return hashlib.sha256(self._run("sh", "-c", _LIST).encode()).hexdigest()

    def keep(self, into: Path) -> None:
        self._must_exist()
        into.mkdir(parents=True, exist_ok=True)
        with (into / "volume.tar").open("wb") as out:
            self._run("tar", "-C", "/v", "-cf", "-", ".", stdout=out)

    def put_back(self, kept: Path) -> None:
        with (kept / "volume.tar").open("rb") as source:
            self._run("sh", "-c", f"{_EMPTY} && tar -C /v -xpf -", stdin=source)

    def holding(self, canary: str) -> list[str]:
        self._must_exist()
        out = self._run("grep", "-rlsa", "--", canary, "/v", ok=(0, 1))
        return [
            f"volume:{self.volume}/{line.removeprefix('/v/')}" for line in out.splitlines() if line
        ]
