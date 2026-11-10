"""Versions of a target's code that evalution checks out on its own (ADR 0011, plan "Phase 2:
versions and worktrees"): the refs of the target's repository, a ref resolved to its commit, a
remote branch fetched into evalution's own refs, and worktrees of commits in evalution's data
folder, with their sizes and a limit.

Evalution never touches the person's checkout:

- A remote branch is fetched into ``refs/evalution/<target>/<remote>/<branch>`` with an explicit
  refspec and an empty ``--refmap``, so neither ``refs/heads/*`` nor ``refs/remotes/*`` is written
  or moved; no tags follow, ``FETCH_HEAD`` isn't written and no maintenance runs after.
- A pull request (``pull/412`` or ``#412``) is fetched the same way, from the code host's
  ``refs/pull/<n>/head`` on ``upstream`` (else ``origin``) into
  ``refs/evalution/<target>/pull/<n>``.
- A version is checked out with ``git worktree add --detach`` into
  ``<data>/versions/<target>/<commit12>``: its own folder, index and ``HEAD``. Removing one removes
  only that folder, and prunes only the worktree entries that pointed into evalution's folder.
- Every git call goes through :func:`git`: the repo's hooks and fsmonitor are off, no shell, a
  timeout, no prompt for a password (a fetch uses the person's own git settings and credential
  helper, as their own ``git fetch`` would; evalution stores none).

The repository is the top level of the target's code folder, which may itself be a worktree of
another repository: a worktree added from it belongs to that repository, as theirs do.

Limits (the plan's decision 6): a size per target, :data:`LIMIT` by default, the versions used
longest ago removed first; and no version is checked out with less than :data:`FREE_MIN` free on
the disk the data folder is on, with what to remove said. What a version keeps beside its worktree
(its built images, phase 3) adds to its size and goes with it, through :class:`Part`.

More than one evalution at a time: a run holds the version it plays (:meth:`Versions.take`, a
shared ``flock`` the operating system drops when the process ends), and no version held is trimmed
or removed; checking out and removing hold the versions folder's lock (:meth:`Versions.changing`),
across processes.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Protocol

from evalution.core.case import SLUG

GB = 1_000_000_000
#: What a target's versions may take, in all, by default: about two of relay-lite's.
LIMIT = 25 * GB
#: No version is checked out with less than this free on the computer's own disk.
FREE_MIN = 5 * GB
#: Where evalution keeps its versions, under the data folder: ``<this>/<target>/<commit12>``.
FOLDER = "versions"
#: Where a fetched remote branch is kept: ``<this>/<target>/<remote>/<branch>``.
REFS = "refs/evalution"
#: How much of a commit names its folder.
FOLDER_COMMIT = 12
#: Seconds a git call may take: a read, a network fetch, a checkout of a large tree.
READ_TIMEOUT = 20
FETCH_TIMEOUT = 300
CHECKOUT_TIMEOUT = 300
#: Seconds listing a remote's branches may take: a network call, so the picker isn't held long.
LIST_TIMEOUT = 30
#: Seconds a change to the versions folder waits for another evalution's to end.
LOCK_WAIT = CHECKOUT_TIMEOUT + 60
#: The versions folder's lock, across processes: ``<data>/versions/<this>``.
LOCK_FILE = ".lock"
#: Where a run says it plays a version: ``<data>/versions/<target>/<this>/<commit12>``.
USING = ".using"
#: Refs listed at most, newest first.
REFS_CAP = 500
#: Settings no repo gets to change for evalution's git calls: nothing of theirs runs.
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
#: Environment that would point git at another repo, index or work tree than the one asked.
_REDIRECTS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)
_HEX = re.compile(r"^[0-9a-f]{4,64}$")
#: A pull request, as a person says it: ``pull/412`` or ``#412``.
_PULL = re.compile(r"^(?:pull/|#)([1-9][0-9]{0,9})$")
#: Where a code host keeps a pull request's head on its repository, and where evalution keeps it.
PULL_HEAD = "refs/pull/{n}/head"
PULL_OURS = "pull/{n}"
#: The remote a pull request is fetched from, first one there: a fork's are on what it was forked
#: from.
PULL_REMOTES = ("upstream", "origin")
_KINDS = {"refs/heads/": "local", "refs/remotes/": "remote", "refs/tags/": "tag"}

NOT_A_CHECKOUT = "The app's code folder isn't a git checkout, so there are no versions to pick"
NO_CODE = "Say where the app's code is first: versions are checked out from its repository"


class VersionError(ValueError):
    """Why a version can't be listed, fetched, checked out or removed, in plain words."""


class InUse(VersionError):
    """A version a run is playing, in this process or another: not removed."""


def pull_number(ref: str | None) -> int | None:
    """The pull request *ref* names (``pull/412``, ``#412``), else ``None``."""
    found = _PULL.match((ref or "").strip())
    return int(found[1]) if found else None


class Part(Protocol):
    """Something a version keeps beside its worktree, by commit: phase 3's built images. Its size
    counts toward the target's limit, and it is removed with the version."""

    def size(self, commit: str) -> int: ...

    def remove(self, commit: str) -> None: ...


@dataclass(frozen=True)
class Ref:
    """A ref of the repository and the commit it points at.

    :param name: As a person says it: ``feature-x``, ``origin/feature-x``, ``v1.2``.
    :param kind: ``local``, ``remote`` (the person's remote-tracking branches), ``fetched``
        (evalution's own, see :meth:`Versions.fetch`), ``pull`` (a pull request evalution
        fetched, see :meth:`Versions.pull`) or ``tag``.
    :param date: The commit's date, seconds since the epoch.
    """

    name: str
    ref: str
    kind: str
    commit: str
    subject: str
    date: float | None
    checked_out: bool = False
    worktree: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ref": self.ref,
            "kind": self.kind,
            "commit": self.commit,
            "subject": self.subject,
            "date": self.date,
            "checked_out": self.checked_out,
            "worktree": self.worktree,
        }


@dataclass(frozen=True)
class RemoteBranch:
    """A branch on a remote, as ``git ls-remote`` lists it: maybe not fetched here at all.

    :param fetched: Evalution fetched it into its own refs; *fetched_commit* is where that is,
        which may be behind *commit*.
    """

    remote: str
    name: str
    commit: str
    fetched: bool = False
    fetched_commit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "remote": self.remote,
            "name": self.name,
            "ref": f"{self.remote}/{self.name}",
            "commit": self.commit,
            "fetched": self.fetched,
            "fetched_commit": self.fetched_commit,
        }


@dataclass(frozen=True)
class Kept:
    """A version evalution keeps: its worktree, what it takes, and when a run last used it."""

    commit: str
    path: Path
    size: int
    used: float
    parts: dict[str, int] = field(default_factory=dict)
    #: A run is playing it, here or in another evalution: it isn't trimmed or removed.
    in_use: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "path": str(self.path),
            "size": self.size,
            "used": self.used,
            "in_use": self.in_use,
        }


class Held:
    """A version a run plays, checked out, held until :meth:`release` (or the process ends): no
    other run, here or in another evalution, trims or removes it meanwhile. Two runs may hold one
    version at once.

    The hold is a shared ``flock`` on ``<data>/versions/<target>/.using/<commit12>``; the
    operating system lets go of it when the process ends, however it ends, so a crashed run holds
    nothing.
    """

    def __init__(self, path: Path, commit: str, file: IO[str]) -> None:
        self.path = path
        self.commit = commit
        self._file: IO[str] | None = file

    def release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> Held:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REDIRECTS}
    # A fetch that needs a password fails instead of waiting for one nobody will type.
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Reads don't take the index's lock or refresh it.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def git(
    folder: Path, *args: str, timeout: float = READ_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """Run git in *folder*: the repo's hooks and fsmonitor off, no shell, no prompt, a timeout.

    :raises VersionError: git isn't there, or it took longer than *timeout*.
    """
    try:
        return subprocess.run(  # noqa: S603
            ["git", *_SAFE, "-C", str(folder), *args],  # noqa: S607
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VersionError(
            f"git {args[0]} took over {int(timeout)} seconds and was stopped"
        ) from exc
    except OSError as exc:
        raise VersionError(f"git didn't start: {exc.strerror or exc}") from exc


def _out(folder: Path, *args: str, timeout: float = READ_TIMEOUT) -> str | None:
    done = git(folder, *args, timeout=timeout)
    return done.stdout if done.returncode == 0 else None


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no reason given"


def _size(path: Path) -> int:
    """Bytes *path* takes on disk, links not followed."""
    total = 0
    for folder, dirs, files in os.walk(path, followlinks=False):
        for name in [*dirs, *files]:
            try:
                stat = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            total += getattr(stat, "st_blocks", 0) * 512 or stat.st_size
    return total


def free(path: Path) -> int:
    """Bytes free on the disk *path* is on (its nearest folder that exists)."""
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


def said(size: float) -> str:
    """A size in words: "1.2 GB", "340 MB"."""
    if size >= GB:
        return f"{size / GB:.1f} GB"
    return f"{max(size, 0) / 1_000_000:.0f} MB"


#: One change to a repository's worktrees or evalution's refs at a time, in this process.
_CHANGING = threading.Lock()


def _flock(file: IO[str], how: int, wait: float) -> bool:
    """``flock`` *file* as *how*, trying for *wait* seconds; whether it was taken."""
    until = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(file, how | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= until:
                return False
            time.sleep(0.05)


class Versions:
    """A target's versions: the refs of its repository and the worktrees evalution keeps.

    :param code_root: The target's code folder; its repository is the folder's top level.
    :param parts: What a version keeps beside its worktree (:class:`Part`).
    :raises VersionError: The folder isn't a git checkout.
    """

    def __init__(
        self,
        target: str,
        code_root: Path,
        data_dir: Path,
        *,
        limit: int = LIMIT,
        parts: Sequence[Part] = (),
    ) -> None:
        if not SLUG.match(target):
            raise VersionError("target: lowercase letters, digits, - and _")
        self.target = target
        self.code_root = Path(code_root)
        if not self.code_root.is_dir():
            raise VersionError(f"{NOT_A_CHECKOUT}: {code_root} isn't there")
        top = _out(self.code_root, "rev-parse", "--show-toplevel")
        if not top or not top.strip():
            raise VersionError(f"{NOT_A_CHECKOUT}: {code_root}")
        #: The repository's top level: the code folder, or a folder above it.
        self.top = Path(top.strip()).resolve()
        common = (_out(self.top, "rev-parse", "--git-common-dir") or "").strip()
        #: The repository's own git folder, shared by all its worktrees.
        self.common = (self.top / common).resolve() if common else self.top / ".git"
        self.folder = (Path(data_dir) / FOLDER / target).resolve()
        self.limit = limit
        self.parts = tuple(parts)

    # More than one evalution at a time

    @contextlib.contextmanager
    def changing(self) -> Iterator[None]:
        """Hold the versions folder while its worktrees change: one change at a time in this
        process (a thread lock) and across processes (an exclusive ``flock`` on
        ``<data>/versions/.lock``), waiting :data:`LOCK_WAIT` seconds at most.

        :raises VersionError: Another evalution held it all that time.
        """
        with _CHANGING:
            self.folder.parent.mkdir(parents=True, exist_ok=True)
            with (self.folder.parent / LOCK_FILE).open("a") as file:
                if not _flock(file, fcntl.LOCK_EX, LOCK_WAIT):
                    raise VersionError(
                        f"Another evalution is changing the versions in {self.folder.parent} "
                        f"and didn't finish within {int(LOCK_WAIT)} seconds; try again when it "
                        "has"
                    )
                try:
                    yield
                finally:
                    fcntl.flock(file, fcntl.LOCK_UN)

    def _using(self, commit: str) -> Path:
        return self.folder / USING / commit[:FOLDER_COMMIT]

    def _hold(self, commit: str) -> IO[str]:
        """A shared hold on *commit*'s version. Taken under :meth:`changing`, so a removal (which
        holds that too) never runs between a check and the hold."""
        path = self._using(commit)
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a")
        if not _flock(file, fcntl.LOCK_SH, 0):  # only a removal holds it, under the same lock
            file.close()
            raise VersionError(f"{commit[:7]} is being removed; try again")
        return file

    def in_use(self, commit: str) -> bool:
        """Whether a run, here or in another evalution, holds *commit*'s version."""
        path = self._using(commit)
        if not path.is_file():
            return False
        with path.open("a") as file:
            if _flock(file, fcntl.LOCK_EX, 0):
                fcntl.flock(file, fcntl.LOCK_UN)
                return False
            return True

    # Refs

    def _git(self, *args: str, timeout: float = READ_TIMEOUT) -> subprocess.CompletedProcess[str]:
        return git(self.top, *args, timeout=timeout)

    def _ours(self) -> str:
        return f"{REFS}/{self.target}/"

    def remotes(self) -> list[str]:
        """The repository's remotes, by name."""
        return sorted(filter(None, (_out(self.top, "remote") or "").split()))

    def head(self) -> tuple[str | None, str | None]:
        """What the person has checked out in the code folder: ``(branch ref, commit)``; the
        branch is ``None`` on a detached ``HEAD``."""
        branch = (_out(self.code_root, "symbolic-ref", "-q", "HEAD") or "").strip() or None
        commit = (_out(self.code_root, "rev-parse", "--verify", "-q", "HEAD") or "").strip()
        return branch, commit or None

    def refs(self) -> list[Ref]:
        """Local branches, remote-tracking branches, evalution's fetched ones and tags, newest
        commit first, at most :data:`REFS_CAP`; each says whether it is the branch checked out
        and whether evalution keeps a worktree of its commit."""
        fields = (
            "%(refname)",
            "%(objectname)",
            "%(*objectname)",
            "%(subject)",
            "%(*subject)",
            "%(committerdate:unix)",
            "%(*committerdate:unix)",
            "%(symref)",
        )
        listed = _out(
            self.top,
            "for-each-ref",
            "--format=" + "%00".join(fields),
            "refs/heads",
            "refs/remotes",
            "refs/tags",
            self._ours(),
        )
        branch, _ = self.head()
        kept = {c for c in self._worktrees().values() if c}
        found: list[Ref] = []
        for line in (listed or "").splitlines():
            parts = line.split("\0")
            if len(parts) != len(fields) or parts[7]:
                continue  # a symbolic ref, like origin/HEAD
            ref, obj, deref, subject, tag_subject, date, tag_date, _ = parts
            commit = deref or obj
            if deref:
                subject, date = tag_subject, tag_date
            kind, name = self._named(ref)
            if kind is None:
                continue
            found.append(
                Ref(
                    name=name,
                    ref=ref,
                    kind=kind,
                    commit=commit,
                    subject=subject,
                    date=float(date) if date.isdigit() else None,
                    checked_out=ref == branch,
                    worktree=commit in kept,
                )
            )
        found.sort(key=lambda r: (-(r.date or 0), r.kind, r.name))
        return found[:REFS_CAP]

    def _named(self, ref: str) -> tuple[str | None, str]:
        if ref.startswith(self._ours()):
            name = ref.removeprefix(self._ours())
            return ("pull" if pull_number(name) is not None else "fetched"), name
        for prefix, kind in _KINDS.items():
            if ref.startswith(prefix):
                return kind, ref.removeprefix(prefix)
        return None, ref

    def resolve(self, ref: str) -> str:
        """The full commit *ref* names: a branch, ``origin/branch`` (the person's, else one
        evalution fetched), a tag, or a short or long commit.

        :raises VersionError: Nothing by that name, in words.
        """
        name = (ref or "").strip()
        if not name or name.startswith("-") or any(c in name for c in "\0\n\r"):
            raise VersionError(f"{ref!r}: not a branch, tag or commit")
        for candidate in (name, f"{self._ours()}{name}"):
            found = _out(
                self.top,
                "rev-parse",
                "--verify",
                "-q",
                "--end-of-options",
                f"{candidate}^{{commit}}",
            )
            if found and found.strip():
                return found.strip()
        raise VersionError(
            f"{name}: no branch, tag or commit by that name in {self.top.name}. A branch only "
            "on the remote is fetched first."
        )

    def remote_of(self, ref: str) -> tuple[str, str] | None:
        """``(remote, branch)`` when *ref* is ``<remote>/<branch>`` for one of the repository's
        remotes; else ``None``."""
        remote, _, branch = (ref or "").strip().partition("/")
        return (remote, branch) if branch and remote in self.remotes() else None

    def reach(self, ref: str, *, fetch: bool = True) -> str:
        """The commit *ref* names. A remote's branch (``origin/feature-x``) is fetched again
        every time, so a run plays the branch as it is on the remote now, never an earlier fetch
        nor the person's remote-tracking branch; a fetch that fails is said, not papered over.

        With *fetch* off (an estimate before Run: nothing on the network), what is known here:
        evalution's fetched ref, else the person's remote-tracking branch.

        :raises VersionError: Nothing by that name, here or on the remote, or the fetch failed.
        """
        name = (ref or "").strip()
        number = pull_number(name)
        if number is not None:
            if fetch:
                return self.pull(number)
            ours = f"{self._ours()}{PULL_OURS.format(n=number)}"
            if git(self.top, "rev-parse", "--verify", "-q", ours).returncode != 0:
                raise VersionError(
                    f"{name}: no pull request {number} fetched here yet; a run fetches it first"
                )
            return self.resolve(ours)
        on_remote = self.remote_of(name)
        if on_remote is not None:
            if fetch:
                return self.fetch(*on_remote)
            ours = f"{self._ours()}{name}"
            if git(self.top, "rev-parse", "--verify", "-q", ours).returncode == 0:
                return self.resolve(ours)
        return self.resolve(name)

    def fetches(self, ref: str) -> bool:
        """Whether a run of *ref* fetches it first: a remote's branch or a pull request."""
        return pull_number(ref) is not None or self.remote_of(ref) is not None

    def _fetch(self, remote: str, source: str, ours: str) -> subprocess.CompletedProcess[str]:
        """Fetch *source* on *remote* into *ours* (one of evalution's refs), and nothing else."""
        with _CHANGING:
            return self._git(
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--no-recurse-submodules",
                "--no-auto-maintenance",
                "--no-auto-gc",
                # Ignore the remote's configured refspecs: only evalution's ref is written.
                "--refmap=",
                "--end-of-options",
                remote,
                f"+{source}:{ours}",
                timeout=FETCH_TIMEOUT,
            )

    def pull_remote(self) -> str:
        """The remote pull requests are fetched from: ``upstream`` when there is one (a fork's
        pull requests are on what it was forked from), else ``origin``, else the only remote.

        :raises VersionError: No remote, or several and none of those.
        """
        remotes = self.remotes()
        for name in PULL_REMOTES:
            if name in remotes:
                return name
        if len(remotes) == 1:
            return remotes[0]
        known = ", ".join(remotes) or "none"
        raise VersionError(
            f"{self.top.name} has no remote named {' or '.join(PULL_REMOTES)} to fetch a pull "
            f"request from ({known})"
        )

    def pull(self, number: int, remote: str | None = None) -> str:
        """Fetch pull request *number*'s head (``refs/pull/N/head`` on the remote) into
        ``refs/evalution/<target>/pull/N``, and return its commit. Nothing else is written.

        :raises VersionError: No such remote or pull request, or the fetch failed.
        """
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise VersionError(f"{number!r}: not a pull request number")
        remote = (remote or "").strip() or self.pull_remote()
        if remote not in self.remotes():
            known = ", ".join(self.remotes()) or "none"
            raise VersionError(f"{remote}: not a remote of {self.top.name} ({known})")
        ours = f"{self._ours()}{PULL_OURS.format(n=number)}"
        done = self._fetch(remote, PULL_HEAD.format(n=number), ours)
        if done.returncode != 0:
            why = _last_line(done.stderr)
            if "couldn't find remote ref" in why:
                raise VersionError(f"{remote} has no pull request {number}")
            raise VersionError(f"Fetching pull request {number} from {remote} failed: {why}")
        return self.resolve(ours)

    def fetch(self, remote: str, branch: str) -> str:
        """Fetch *remote*'s *branch* into ``refs/evalution/<target>/<remote>/<branch>``, and
        return its commit. Nothing else is written: no branch of the person's, no
        remote-tracking branch, no tag, no ``FETCH_HEAD``.

        :raises VersionError: No such remote or branch, an unusable name, or the fetch failed
            (its last line said).
        """
        remote, branch = (remote or "").strip(), (branch or "").strip()
        if remote not in self.remotes():
            known = ", ".join(self.remotes()) or "none"
            raise VersionError(f"{remote or '(none)'}: not a remote of {self.top.name} ({known})")
        branch = branch.removeprefix("refs/heads/")
        ours = f"{self._ours()}{remote}/{branch}"
        well_formed = branch and not branch.startswith("-")
        if not well_formed or git(self.top, "check-ref-format", ours).returncode != 0:
            raise VersionError(f"{branch or '(none)'}: not a branch name")
        done = self._fetch(remote, f"refs/heads/{branch}", ours)
        if done.returncode != 0:
            why = _last_line(done.stderr)
            if "couldn't find remote ref" in why:
                raise VersionError(f"{remote} has no branch {branch}")
            raise VersionError(f"Fetching {branch} from {remote} failed: {why}")
        return self.resolve(ours)

    def remote_branches(self, remote: str) -> list[RemoteBranch]:
        """The branches *remote* has now, as ``git ls-remote --heads`` lists them, with the
        person's own git settings and credentials, nothing fetched and nothing written; each says
        whether evalution has fetched it, and at which commit.

        :raises VersionError: No such remote, or the listing failed (its last line said).
        """
        remote = (remote or "").strip()
        if remote not in self.remotes():
            known = ", ".join(self.remotes()) or "none"
            raise VersionError(f"{remote or '(none)'}: not a remote of {self.top.name} ({known})")
        done = self._git("ls-remote", "--heads", "--end-of-options", remote, timeout=LIST_TIMEOUT)
        if done.returncode != 0:
            raise VersionError(
                f"Listing {remote}'s branches failed: {_last_line(done.stderr)}. Check that "
                f"`git ls-remote {remote}` works in {self.top}."
            )
        fetched = {r.name: r.commit for r in self.refs() if r.kind == "fetched"}
        found = []
        for line in done.stdout.splitlines():
            commit, _, ref = line.strip().partition("\t")
            if not ref.startswith("refs/heads/") or not _HEX.match(commit):
                continue
            name = ref.removeprefix("refs/heads/")
            kept = fetched.get(f"{remote}/{name}")
            found.append(RemoteBranch(remote, name, commit, kept is not None, kept))
        return sorted(found, key=lambda b: b.name)[:REFS_CAP]

    # Worktrees

    def path(self, commit: str) -> Path:
        """Where a version of *commit* is checked out (whether it is or not)."""
        return self.folder / commit[:FOLDER_COMMIT]

    def code_folder(self, commit: str) -> Path:
        """The target's code folder inside the version: the same place relative to the repo."""
        inside = self.code_root.resolve().relative_to(self.top)
        return self.path(commit) / inside

    def _worktrees(self) -> dict[Path, str | None]:
        """Worktrees of the repository inside evalution's folder: path -> commit checked out
        (``None`` when its folder is gone)."""
        listed = _out(self.top, "worktree", "list", "--porcelain") or ""
        found: dict[Path, str | None] = {}
        for block in listed.split("\n\n"):
            fields = {}
            for line in block.splitlines():
                key, _, value = line.partition(" ")
                fields[key] = value
            where = fields.get("worktree")
            if not where:
                continue
            path = Path(where).resolve()
            if path.parent == self.folder:
                found[path] = fields.get("HEAD") if path.is_dir() else None
        return found

    def kept(self) -> list[Kept]:
        """The versions evalution keeps for this target, used longest ago first, with what each
        takes on disk (its worktree and its :class:`Part` s)."""
        found = []
        for path, commit in self._worktrees().items():
            if not commit:
                continue
            parts = {type(p).__name__: p.size(commit) for p in self.parts}
            try:
                used = path.stat().st_mtime
            except OSError:
                continue
            size = _size(path) + sum(parts.values())
            found.append(Kept(commit, path, size, used, parts, self.in_use(commit)))
        return sorted(found, key=lambda k: k.used)

    def size(self) -> int:
        """What this target's versions take, in all."""
        return sum(k.size for k in self.kept())

    def _check_room(self) -> None:
        left = free(self.folder)
        if left >= FREE_MIN:
            return
        kept = self.kept()
        what = (
            "Remove a version evalution keeps: "
            + ", ".join(f"{k.commit[:7]} ({said(k.size)})" for k in kept)
            + f" (evalution versions --target {self.target} --remove COMMIT)"
            if kept
            else "Free some space on it first"
        )
        raise VersionError(
            f"Only {said(left)} free on this computer's disk; checking out a version needs "
            f"{said(FREE_MIN)}. {what}."
        )

    def _inside_repo(self) -> None:
        """:raises VersionError: Evalution's folder is in the repo's working tree, not ignored:
        a version would show in the person's ``git status``."""
        if not self.folder.is_relative_to(self.top):
            return
        rel = self.folder.relative_to(self.top).as_posix()
        if git(self.top, "check-ignore", "-q", "--", rel).returncode != 0:
            raise VersionError(
                f"evalution's data folder is inside {self.top.name} and git doesn't ignore it, "
                "so a version would show in your checkout's status: move the data folder, or "
                "ignore it"
            )

    def checkout(self, ref: str, keep: Iterable[str] = ()) -> Path:
        """A worktree of *ref*'s commit in evalution's folder, detached; the one there already
        when there is one. Then versions used longest ago go until the target is under its limit,
        never this one, those in *keep*, nor one a run holds (:meth:`take`).

        :raises VersionError: No such ref, too little disk, or git refused (its last line said).
        """
        path, held = self._checkout(ref, keep, hold=False)
        assert held is None
        return path

    def take(self, ref: str, keep: Iterable[str] = ()) -> Held:
        """:meth:`checkout`, and hold the version for a run until :meth:`Held.release`: no
        other run, here or in another evalution, trims or removes it meanwhile.

        :raises VersionError: As :meth:`checkout`.
        """
        _, held = self._checkout(ref, keep, hold=True)
        assert held is not None
        return held

    def _checkout(self, ref: str, keep: Iterable[str], *, hold: bool) -> tuple[Path, Held | None]:
        commit = self.resolve(ref)
        path = self.path(commit)
        # Read-only, and first: nothing is written in a folder that would show in their status.
        self._inside_repo()
        held = None
        with self.changing():
            there = self._worktrees()
            if there.get(path) == commit:
                os.utime(path)
                if hold:
                    held = Held(path, commit, self._hold(commit))
                return path, held
            self._check_room()
            if path in there or path.exists():
                self._drop(path)
            self.folder.mkdir(parents=True, exist_ok=True)
            done = self._git(
                "worktree",
                "add",
                "--detach",
                "--end-of-options",
                str(path),
                commit,
                timeout=CHECKOUT_TIMEOUT,
            )
            if done.returncode != 0:
                self._drop(path)
                raise VersionError(f"Checking out {commit[:7]} failed: {_last_line(done.stderr)}")
            if hold:
                held = Held(path, commit, self._hold(commit))
        self.trim(keep=[commit, *keep])
        return path, held

    def _drop(self, path: Path) -> None:
        """Remove evalution's worktree at *path*, its folder, and the entries that pointed into
        evalution's folder. Never a path outside it."""
        path = path.resolve()
        if path.parent != self.folder:
            raise VersionError(f"{path}: not one of evalution's versions")
        if path.is_dir():
            self._git("worktree", "remove", "--force", "--end-of-options", str(path))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        self._prune()

    def _prune(self) -> None:
        """``git worktree prune`` of evalution's entries only: those whose folder was under
        evalution's and is gone. The person's own stale entries are left as they are."""
        admin = self.common / "worktrees"
        if not admin.is_dir():
            return
        for entry in admin.iterdir():
            try:
                pointed = Path((entry / "gitdir").read_text().strip())
            except OSError:
                continue
            folder = pointed.parent
            if folder.resolve().parent == self.folder and not folder.exists():
                shutil.rmtree(entry, ignore_errors=True)

    def remove(self, commit: str) -> Kept:
        """Remove the version of *commit* (a full commit or one evalution keeps starting with it):
        its worktree and whatever its :class:`Part` s keep.

        :raises InUse: A run, here or in another evalution, is playing it.
        :raises VersionError: Not a commit, or it starts more than one kept version.
        :raises LookupError: No version of it is kept.
        """
        wanted = (commit or "").strip().lower()
        if not _HEX.match(wanted):
            raise VersionError(f"{commit!r}: not a commit")
        matches = [k for k in self.kept() if k.commit.startswith(wanted)]
        if not matches:
            raise LookupError(f"no version of {wanted[:FOLDER_COMMIT]} is kept")
        if len(matches) > 1:
            raise VersionError(f"{wanted}: more than one version starts with it, give more of it")
        found = matches[0]
        with self.changing():
            using = self._using(found.commit)
            using.parent.mkdir(parents=True, exist_ok=True)
            with using.open("a") as file:
                if not _flock(file, fcntl.LOCK_EX, 0):
                    raise InUse(
                        f"{found.commit[:7]}: a run is playing it; remove it when the run ends"
                    )
                self._drop(found.path)
                # Under the folder's lock no one is waiting to hold it: the mark can go.
                using.unlink(missing_ok=True)
        for part in self.parts:
            part.remove(found.commit)
        return found

    def trim(self, keep: Iterable[str] = ()) -> list[Kept]:
        """Remove versions used longest ago until the target is under its limit, never those
        whose commit is in *keep* nor one a run holds; the ones removed."""
        keeping = set(keep)
        kept = self.kept()
        total = sum(k.size for k in kept)
        gone = []
        for version in kept:
            if total <= self.limit:
                break
            if version.commit in keeping or version.in_use:
                continue
            try:
                gone.append(self.remove(version.commit))
            except InUse:
                continue  # taken since kept() was read
            total -= version.size
        return gone

    def view(self) -> dict[str, Any]:
        """What the versions card and ``evalution versions`` show."""
        branch, commit = self.head()
        kept = self.kept()
        return {
            "target": self.target,
            "repo": str(self.top),
            "remotes": self.remotes(),
            "checked_out": {
                "branch": branch.removeprefix("refs/heads/") if branch else None,
                "commit": commit,
            },
            "refs": [r.as_dict() for r in self.refs()],
            "kept": [k.as_dict() for k in kept],
            "size": sum(k.size for k in kept),
            "limit": self.limit,
            "free": free(self.folder),
            "free_min": FREE_MIN,
        }


def of(name: str, code_root: Path | None, data_dir: Path, **kwargs: Any) -> Versions:
    """*name*'s versions, from its code folder.

    :raises VersionError: It has no code folder, or that isn't a git checkout.
    """
    if code_root is None:
        raise VersionError(NO_CODE)
    return Versions(name, code_root, data_dir, **kwargs)
