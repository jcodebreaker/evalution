"""A Docker volume of evalution's own, reached through a throwaway container.

Every action runs in ``docker run --rm -i -v <volume>:/v <image>``, with no network, and moves
data only through that container's stdin and stdout. Nothing on this computer is mounted into it:
the Docker VM may not see this computer's temporary folders.

* **keep** copies the volume into a second volume of evalution's own, ``<volume>_golden``, in one
  throwaway container (``cp -a``): nothing passes through this computer. Its name goes in
  ``volume.json`` in the folder kept.
* **put_back** empties the volume, dotfiles included, then copies the golden volume back into it
  the same way. A folder kept before golden volumes holds ``volume.tar``: that is unpacked.
* **drop_golden** removes the golden volume (compose's ``down`` of the copy would too: it is
  named after the copy's project).
* **digest** is computed inside the container: each entry's type, mode and name (and a link's
  target), then each file's ``sha256``, all in path order, so it's the same for the same content
  whenever it's taken. Modification times aren't counted.
* **holding** is a ``grep`` inside the container, named ``volume:<name>/<path>``.
* **shape** is the cheap check between full hashes: the volume's top-level entries, each with
  its type, and a file's size.

Containers that use the volume should be stopped (or idle) while it's kept or put back
(``needs_stop`` is ``services``: those that mount it, not the whole copy).
It refuses any volume not named ``eval-...`` or ``eval_...`` (evalution's own compose copy).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path

from evalution.isolation.drivers.base import DockerError, Refused, docker

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_EMPTY = "find /v -mindepth 1 -maxdepth 1 -exec rm -rf {} +"
_LIST = (
    "cd /v && find . -exec stat -c '%F %a %N' {} + | sort"
    " && find . -type f -exec sha256sum {} + | sort -k2"
)
_TOP = (
    "cd /v && find . -mindepth 1 -maxdepth 1 -exec stat -c '%F %n' {} + | sort"
    " && find . -mindepth 1 -maxdepth 1 -type f -exec stat -c '%s %n' {} + | sort -k2"
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

    #: Put back with the services that mount it stopped, the rest of the copy left up.
    needs_stop = "services"

    def _helper(self) -> str:
        """A name for one throwaway container: the volume's, so it is evalution's own (``eval-*``)
        like everything else it starts, and found with the copy if it were ever left."""
        return f"{self.volume}-helper-{secrets.token_hex(4)}"

    def _run(self, *command: str, **kwargs) -> str:
        """Run *command* in a throwaway container with the volume at ``/v``."""
        return docker(
            "run", "--rm", "-i", "--name", self._helper(), "--network", "none",
            "--label", "evalution.driver=volume",
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

    def shape(self) -> str:
        """The volume's top-level listing: what a put back volume must show again."""
        self._must_exist()
        return self._run("sh", "-c", _TOP)

    @property
    def golden(self) -> str:
        """The second volume its starting state is kept in."""
        return f"{self.volume}_golden"

    def _copy(self, source: str, into: str) -> None:
        """Make volume *into* exactly volume *source*, in one throwaway container."""
        for name in (source, into):
            if not name.startswith(("eval-", "eval_")) or not _NAME.match(name):
                raise Refused(f"volume {name!r} isn't one of evalution's own")
        docker(
            "run", "--rm", "--name", self._helper(), "--network", "none",
            "--label", "evalution.driver=volume",
            "-v", f"{source}:/from:ro", "-v", f"{into}:/v", self.image,
            "sh", "-c", f"{_EMPTY} && cp -a /from/. /v/",
        )  # fmt: skip

    def keep(self, into: Path) -> None:
        self._must_exist()
        into.mkdir(parents=True, exist_ok=True)
        docker("volume", "create", "--label", "evalution.driver=volume", self.golden)
        self._copy(self.volume, self.golden)
        (into / "volume.json").write_text(
            json.dumps({"volume": self.volume, "golden": self.golden})
        )

    def _kept(self, kept: Path) -> str | None:
        """The golden volume *kept* names; ``None`` for a folder kept as a tar.

        :raises Refused: It names another volume's, or one not evalution's own.
        """
        path = kept / "volume.json"
        if not path.is_file():
            return None
        saved = json.loads(path.read_text())
        if saved.get("volume") != self.volume or saved.get("golden") != self.golden:
            raise Refused(f"{kept} was kept from another volume")
        return self.golden

    def put_back(self, kept: Path) -> None:
        golden = self._kept(kept)
        if golden is not None:
            self._copy(golden, self.volume)
            return
        with (kept / "volume.tar").open("rb") as source:
            self._run("sh", "-c", f"{_EMPTY} && tar -C /v -xpf -", stdin=source)

    def drop_golden(self, kept: Path) -> None:
        """Remove the golden volume :meth:`keep` made for *kept*."""
        golden = self._kept(kept)
        if golden is not None:
            docker("volume", "rm", "-f", golden)

    def holding(self, canary: str) -> list[str]:
        self._must_exist()
        out = self._run("grep", "-rlsa", "--", canary, "/v", ok=(0, 1))
        return [
            f"volume:{self.volume}/{line.removeprefix('/v/')}" for line in out.splitlines() if line
        ]
