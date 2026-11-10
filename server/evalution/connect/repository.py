"""Connecting an app by its repository's address on the code host (docs/versions.md, "Connect by
address"): ``github.com/owner/repo``, ``owner/repo``, ``owner/repo#412`` (a pull request) or
``owner/repo@feature-x`` (a branch, tag or commit).

The repository is cloned into evalution's own folder, ``<data>/code/<target>``, with the
person's own git settings and credentials, as their own ``git clone`` would (evalution stores
none, and never asks for a password: a clone that needs one fails, and says how to check it).
Every git call goes through :func:`evalution.project.versions.git`: no hooks, no prompt, a
timeout. Then the clone is connected as a folder is. A pull request is fetched into the clone's
``refs/evalution/<target>/pull/<n>`` and checked out detached; so is a ``@ref``. The clone is
evalution's, not the person's checkout, so checking out there touches nothing of theirs.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evalution.project import versions

#: The code host an address names, and where its repositories are cloned from. Tests point
#: :data:`BASE` at a folder of bare repositories: nothing here reaches the network on its own.
HOST = "github.com"
BASE = f"https://{HOST}"
#: Where evalution keeps the clones it connects: ``<data>/<this>/<target>``.
FOLDER = "code"
#: Seconds a clone may take: a large repository over a slow link.
CLONE_TIMEOUT = 900
_NAME = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}"
_ADDRESS = re.compile(
    rf"^(?:(?:https?://)?(?:www\.)?{re.escape(HOST)}/)?(?P<owner>{_NAME})/(?P<repo>{_NAME})"
    r"(?:\.git)?/?"
    r"(?:(?:#|/pull/)(?P<pull>[1-9][0-9]{0,9})|@(?P<ref>[^\s@#]{1,200}))?$",
    re.IGNORECASE,
)


class CloneError(ValueError):
    """Why a repository couldn't be cloned or checked out, in plain words."""


@dataclass(frozen=True)
class Address:
    """A repository on the code host, and what of it to connect.

    :param pull: A pull request's number: its head is checked out.
    :param ref: A branch, tag or commit to check out; neither: the default branch.
    """

    owner: str
    repo: str
    pull: int | None = None
    ref: str | None = None

    @property
    def said(self) -> str:
        """``owner/repo``, ``owner/repo#412`` or ``owner/repo@feature-x``."""
        name = f"{self.owner}/{self.repo}"
        if self.pull is not None:
            return f"{name}#{self.pull}"
        return f"{name}@{self.ref}" if self.ref else name

    @property
    def url(self) -> str:
        """Where it is cloned from."""
        return f"{BASE}/{self.owner}/{self.repo}.git"

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": HOST,
            "owner": self.owner,
            "repo": self.repo,
            "pull": self.pull,
            "ref": self.ref,
            "said": self.said,
        }


def parse(text: str) -> Address | None:
    """The repository *text* names on the code host, else ``None``: ``github.com/owner/repo``
    (with ``https://``, ``.git`` or ``/pull/412`` too), ``owner/repo``, ``owner/repo#412``,
    ``owner/repo@ref``. Whether a bare ``owner/repo`` is a folder instead is the caller's to say.
    """
    found = _ADDRESS.match((text or "").strip())
    if not found:
        return None
    owner, repo, ref = found["owner"], found["repo"], found["ref"]
    if not repo or repo.endswith(".") or ".." in owner + repo:
        return None
    if ref is not None and (ref.startswith(("-", "/")) or ".." in ref or ref.endswith("/")):
        return None
    pull = int(found["pull"]) if found["pull"] else None
    return Address(owner, repo.removesuffix(".git"), pull, ref)


def folder(data_dir: Path, target: str) -> Path:
    """Where *target*'s clone is kept."""
    return (Path(data_dir) / FOLDER / target).resolve()


def _origin(where: Path) -> str | None:
    done = versions.git(where, "remote", "get-url", "origin")
    return done.stdout.strip() if done.returncode == 0 else None


def _failed(address: Address, stderr: str) -> CloneError:
    why = versions._last_line(stderr)  # noqa: SLF001 - one module's plain last line
    if any(word in why.lower() for word in ("username", "authentication", "not found", "403")):
        return CloneError(
            f"{address.said} couldn't be cloned: {why}. Evalution clones with your own git "
            f"credentials; check that `git clone {address.url}` works in a terminal."
        )
    return CloneError(f"{address.said} couldn't be cloned: {why}")


def clone(address: Address, data_dir: Path, target: str) -> tuple[Path, str]:
    """Clone *address* into ``<data>/code/<target>`` (a clone of it there already is fetched
    again instead), then check out its pull request or ref, detached: the folder, and the commit
    checked out.

    :raises CloneError: The clone, fetch or checkout failed, git took too long, or another
        repository is there.
    """
    try:
        return _clone(address, data_dir, target)
    except CloneError:
        raise
    except versions.VersionError as exc:
        raise CloneError(f"{address.said} couldn't be cloned: {exc}") from exc


def _clone(address: Address, data_dir: Path, target: str) -> tuple[Path, str]:
    where = folder(data_dir, target)
    if where.exists():
        if _origin(where) != address.url:
            raise CloneError(
                f"{where} is there already and isn't a clone of {address.said}; move it, or "
                "connect under another name"
            )
        done = versions.git(
            where,
            "fetch",
            "--no-recurse-submodules",
            "--no-auto-maintenance",
            "origin",
            timeout=versions.FETCH_TIMEOUT,
        )
        if done.returncode != 0:
            raise _failed(address, done.stderr)
    else:
        where.parent.mkdir(parents=True, exist_ok=True)
        done = versions.git(
            where.parent,
            "clone",
            "--quiet",
            "--no-recurse-submodules",
            "--end-of-options",
            address.url,
            str(where),
            timeout=CLONE_TIMEOUT,
        )
        if done.returncode != 0:
            shutil.rmtree(where, ignore_errors=True)
            raise _failed(address, done.stderr)
    return where, _check_out(address, where, data_dir, target)


def _check_out(address: Address, where: Path, data_dir: Path, target: str) -> str:
    """Check out what *address* asks for in the clone, detached; the commit."""
    found = versions.Versions(target, where, data_dir)
    try:
        if address.pull is not None:
            commit = found.pull(address.pull, "origin")
        elif address.ref:
            try:
                commit = found.resolve(f"origin/{address.ref}")
            except versions.VersionError:
                commit = found.resolve(address.ref)
        else:
            commit = found.resolve("origin/HEAD")
    except versions.VersionError as exc:
        raise CloneError(f"{address.said}: {exc}") from exc
    done = versions.git(
        where, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", commit
    )
    if done.returncode != 0:
        raise CloneError(
            f"Checking out {commit[:7]} of {address.said} failed: "
            f"{versions._last_line(done.stderr)}"  # noqa: SLF001
        )
    return commit
