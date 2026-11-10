"""The running app watched while one probe goes through it: what it really touched.

:func:`watch` takes a snapshot of the folders the app uses, lets *act* send the probe (a message
carrying a canary), then takes another. The folders are the project's own plus every folder the
app's process (and its children) holds a file open in before the probe, outside system and
package folders, so a store at a path the code hardcodes elsewhere is still found. Open TCP
connections to the usual database ports name the stores it reaches over a socket.

A broad folder (the system temp folder, the home folder, ``/usr``...: :func:`broad`) is never
watched whole: an app holding a file there (its inherited output, a lock, a socket) has only that
file watched, with its sidecars. The file budget (:data:`MAX_FILES`) is shared out per folder
watched, so one crowded folder can't use it all up, and a folder cut short is said
(:attr:`Observation.unread`), so a plan resting on a search that didn't read everything says so.

Nothing here writes, deletes or sends anything but the probe *act* sends. Files are read only to
hash them and to look for the canary.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalution.code import SKIP_DIRS
from evalution.isolation.discover import Store

#: Files hashed at most per snapshot, and the largest hashed; larger ones compare size and time.
#: The budget is shared out per folder watched (:func:`shares`).
MAX_FILES, MAX_HASH = 20_000, 50 * 1024 * 1024
#: Files counted in a folder past its share, as many times the whole budget: what ``seen`` says.
COUNTED = 5
#: Ports and the store each usually is.
PORTS = {5432: "postgres", 3306: "mysql", 6379: "redis", 27017: "mongo", 6333: "vector"}
#: Folders whose files are never the app's state.
_SYSTEM = (
    "/System/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/Library/",
    "/Applications/",
    "/opt/homebrew/",
    "/nix/",
    "/dev/",
    "/proc/",
    "/private/var/db/",
)
_PACKAGE_DIRS = re.compile(
    r"/(site-packages|dist-packages|node_modules|\.venv|venv|\.pyenv|\.nvm|"
    r"\.cache/uv|\.local/share/uv|\.rustup|\.cargo|Frameworks)/"
)
#: Folders never watched whole, as they are or under the home folder: the system's, and the
#: home folder's own big ones. The temp folders are added when asked (:func:`broad`). Names to
#: stay out of, never a file made there.
_TMP = ("/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp")  # noqa: S108
_BROAD = ("/", *_TMP, "/usr", "/usr/local",
          "/etc", "/private/etc", "/dev", "/proc", "/sys", "/var", "/private/var", "/private",
          "/opt", "/Library", "/System", "/Applications", "/Users", "/home", "/Volumes", "/run",
          "/var/log", "/private/var/log", "/var/run", "/private/var/run", "/var/folders",
          "/private/var/folders", "/var/lib", "/srv", "/mnt", "/media")  # fmt: skip
_BROAD_HOME = ("", "Library", "Library/Caches", "Library/Application Support", "Library/Logs",
               "Library/Preferences", "Library/Containers", "Library/Group Containers",
               ".cache", ".local", ".local/share", ".local/state", ".config", "Documents",
               "Desktop", "Downloads")  # fmt: skip
#: macOS's per-user temp and cache folders: ``/private/var/folders/<a>/<b>/<T|C|0|X>``.
_VAR_FOLDERS = Path("/private/var/folders")


@dataclass(frozen=True)
class Entry:
    size: int
    mtime: int
    digest: str


Unread = dict[str, Any]


class Snapshot(dict[str, Entry]):
    """Every file read, by absolute path, and the folders not read whole.

    :ivar unread: ``[{root, seen, limit}]``: a root with more files than its share of the budget:
        ``seen`` files found there (counted up to :data:`COUNTED` times the budget), ``limit`` of
        them read.
    """

    def __init__(self) -> None:
        super().__init__()
        self.unread: list[Unread] = []


@dataclass
class Observation:
    #: Folders watched.
    roots: list[str]
    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    #: Files written during the probe that now hold the canary.
    canary: list[str] = field(default_factory=list)
    #: ``host:port`` the process held a TCP connection to, and the kind the port suggests.
    sockets: dict[str, str] = field(default_factory=dict)
    #: Regular files the process held open, outside system and package folders.
    open_files: list[str] = field(default_factory=list)
    #: Roots watched but not read whole, the file budget reached: ``[{root, seen, limit}]``
    #: (:class:`Snapshot`). A store kept there may have been missed.
    unread: list[Unread] = field(default_factory=list)

    @property
    def written(self) -> list[str]:
        return sorted({*self.created, *self.changed})


def _hash(path: Path, size: int) -> str:
    if size > MAX_HASH:
        return ""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return "unreadable"
    return h.hexdigest()


def _temp_folders() -> list[Path]:
    """The system temp folders as this process sees them now."""
    found = [tempfile.gettempdir(), os.environ.get("TMPDIR") or "", *_TMP]
    return [Path(f).resolve() for f in found if f]


def broad(path: Path) -> bool:
    """Whether *path* is a folder never watched whole: ``/``, a system folder, the home folder or
    one of its big ones (``~/Library``, ``~/.cache``, ``~/Downloads``...), a temp folder
    (``$TMPDIR``, ``/tmp``, macOS's ``/var/folders/*/*/T``), or a folder above any of those."""
    path = path.expanduser().resolve()
    home = Path.home().resolve()
    named = {Path(p).resolve() for p in _BROAD} | {(home / p).resolve() for p in _BROAD_HOME}
    if path in named:
        return True
    if any(t.is_relative_to(path) for t in [*_temp_folders(), home]):
        return True
    return path.is_relative_to(_VAR_FOLDERS) and len(path.relative_to(_VAR_FOLDERS).parts) <= 3


def _files(root: Path, cap: int, keep: int) -> tuple[list[Path], int]:
    """The first *keep* files under *root* (sorted, so each walk reads the same ones), and how
    many there are, counted up to *cap*. A file root is the file and its sidecars
    (``memory.db-wal``), whatever else its folder holds."""
    if not root.is_dir():
        try:
            names = sorted(
                e.name
                for e in os.scandir(root.parent)
                if e.name == root.name or e.name.startswith(root.name + "-")
            )
        except OSError:
            return [], 0
        found = [root.parent / n for n in names if (root.parent / n).is_file()]
        return found[:keep], len(found)
    kept: list[Path] = []
    count = 0
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and d != ".git")
        for name in sorted(names):
            path = Path(dirpath) / name
            if not path.is_file():
                continue
            count += 1
            if len(kept) < keep:
                kept.append(path)
            if count >= cap:
                return kept, count
    return kept, count


def shares(sizes: Sequence[int], total: int) -> list[int]:
    """How many files each root may read of *total*: an equal share each, and what a root
    doesn't need passed on to those that need more (the first roots first for what doesn't
    divide evenly). A root never gets more than it has."""
    out = [0] * len(sizes)
    hungry = [i for i, size in enumerate(sizes) if size > 0]
    left = total
    while hungry and left > 0:
        each = left // len(hungry)
        if each == 0:
            for i in hungry[:left]:
                out[i] += 1
            break
        for i in hungry:
            take = min(each, sizes[i] - out[i])
            out[i] += take
            left -= take
        hungry = [i for i in hungry if out[i] < sizes[i]]
    return out


def _shared_out(roots: Sequence[Path], out: Snapshot, like: Snapshot | None) -> list[list[Path]]:
    """The files each root reads, its share of :data:`MAX_FILES`, with the roots cut short set on
    *out*. A root *like* cut short reads no further than it did there, so files
    it didn't read then don't turn up as new; the others share what it leaves."""
    total = MAX_FILES
    listed = [_files(Path(r), total * COUNTED, total) for r in roots]
    cut = {u["root"]: u["limit"] for u in like.unread} if like is not None else {}
    wanted = [
        min(count, cut.get(str(r), count)) for r, (_, count) in zip(roots, listed, strict=True)
    ]
    given = shares(wanted, total)
    reads = []
    for root, (found, count), limit in zip(roots, listed, given, strict=True):
        key = str(root)
        if count > limit:
            out.unread.append({"root": key, "seen": count, "limit": limit})
        reads.append(found[:limit])
    return reads


def unread_of(roots: Sequence[Path]) -> list[Unread]:
    """The roots a :func:`snapshot` of *roots* wouldn't read whole, counted only (nothing read)."""
    out = Snapshot()
    _shared_out(roots, out, None)
    return out.unread


def snapshot(roots: Sequence[Path], like: Snapshot | None = None) -> Snapshot:
    """Every file under *roots*, by absolute path, each root read up to its share of
    :data:`MAX_FILES` (:func:`shares`; the first roots first), a root cut short in ``unread``.
    With *like* (the snapshot before), a root it cut short reads no further than it did there,
    so the two compare file for file."""
    out = Snapshot()
    for found in _shared_out(roots, out, like):
        for path in found:
            try:
                st = path.stat()
            except OSError:
                continue
            out[str(path)] = Entry(st.st_size, st.st_mtime_ns, _hash(path, st.st_size))
    return out


def merge_unread(*lists: Sequence[Unread]) -> list[Unread]:
    """``unread`` entries, one per root in the order first met: the most seen there, the least
    read."""
    out: dict[str, Unread] = {}
    for entry in (e for items in lists for e in items):
        had = out.get(entry["root"])
        if had is None:
            out[entry["root"]] = dict(entry)
        else:
            had["seen"] = max(had["seen"], entry["seen"])
            had["limit"] = min(had["limit"], entry["limit"])
    return list(out.values())


def unread_words(unread: Sequence[Unread]) -> str:
    """What the search didn't read whole, in a person's words; ``""`` when it read everything."""
    if not unread:
        return ""
    said = "; ".join(f"{u['root']} (read {u['limit']:,} of {u['seen']:,} files)" for u in unread)
    return (
        f"The search for stores was incomplete: the file limit was reached in {said}. "
        "A store kept there may have been missed; the plan is proven only for what was read."
    )


def diff(before: Snapshot, after: Snapshot) -> tuple[list[str], list[str], list[str]]:
    """Created, changed and deleted paths."""
    created = sorted(p for p in after if p not in before)
    deleted = sorted(p for p in before if p not in after)
    changed = sorted(p for p in after if p in before and after[p] != before[p])
    return created, changed, deleted


def holds(path: str, canary: str) -> bool:
    """Whether the file's bytes hold *canary* (a SQLite page or WAL frame included)."""
    try:
        with open(path, "rb") as f:
            return canary.encode() in f.read(MAX_HASH)
    except OSError:
        return False


def children(pid: int) -> list[int]:
    """*pid* and every process under it."""
    found, todo = [pid], [pid]
    pgrep = shutil.which("pgrep")
    while todo and pgrep:
        out = subprocess.run(  # noqa: S603
            [pgrep, "-P", str(todo.pop())], capture_output=True, text=True, check=False
        ).stdout
        for line in out.split():
            if line.isdigit() and int(line) not in found:
                found.append(int(line))
                todo.append(int(line))
    return found


def parse_lsof(text: str) -> tuple[list[str], dict[str, str]]:
    """Open regular files and TCP peers from ``lsof -F ftn`` output."""
    files: list[str] = []
    sockets: dict[str, str] = {}
    kind = ""
    for line in text.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "f":
            kind = ""
        elif tag == "t":
            kind = value
        elif tag == "n" and kind == "REG":
            if (
                value.startswith("/")
                and not value.startswith(_SYSTEM)
                and not _PACKAGE_DIRS.search(value)
            ):
                files.append(value)
        elif tag == "n" and kind in ("IPv4", "IPv6") and "->" in value:
            peer = value.split("->", 1)[1].split(" ", 1)[0]
            port = peer.rsplit(":", 1)[-1]
            if port.isdigit() and int(port) in PORTS:
                sockets[peer] = PORTS[int(port)]
    return sorted(set(files)), sockets


def open_by(pid: int) -> tuple[list[str], dict[str, str]]:
    """What *pid* and its children hold open. Empty where ``lsof`` isn't there."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return [], {}
    pids = ",".join(str(p) for p in children(pid))
    out = subprocess.run(  # noqa: S603
        [lsof, "-n", "-P", "-F", "ftn", "-a", "-p", pids],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return parse_lsof(out)


def listening(port: int) -> int | None:
    """The process listening on *port* on this computer."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return None
    out = subprocess.run(  # noqa: S603
        [lsof, "-n", "-P", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return int(out[0]) if out and out[0].isdigit() else None


def _place(path: Path) -> Path:
    """What to watch for a file at *path*: its folder, or the file alone (with its sidecars) when
    that folder is broad (:func:`broad`)."""
    return path if broad(path.parent) else path.parent


def roots_for(folder: Path, open_files: list[str], stores: list[Store]) -> list[Path]:
    """The roots to watch, in the order they share the file budget: the project, the places a
    store names by an absolute path (the code's or a setting's), then the folders of files the
    app holds open. A file in a broad folder (:func:`broad`) is watched alone, never the folder.
    A root under another is dropped, the outer one kept."""
    first = [folder.resolve()]
    for store in stores:
        loc = store.locator.removeprefix("sqlite:///")
        if loc.startswith(("/", "~")):
            first.append(_place(Path(loc).expanduser().resolve()))
    then = [_place(Path(f).resolve()) for f in open_files]
    order: list[Path] = []
    for path in [*first, *then]:
        if path in order or broad(path) or not (path.exists() or path.parent.is_dir()):
            continue
        order.append(path)
    return [p for p in order if not any(p != o and p.is_relative_to(o) for o in order)]


async def watch(
    folder: Path,
    stores: list[Store],
    act: Callable[[], Awaitable[object]],
    canary: str,
    pid: int | None = None,
) -> Observation:
    """Snapshot, run *act* (which should send *canary* through the app), snapshot again."""
    open_before, sockets = open_by(pid) if pid else ([], {})
    roots = roots_for(folder, open_before, stores)
    before = await asyncio.to_thread(snapshot, roots)
    await act()
    open_after, more = open_by(pid) if pid else ([], {})
    late = [Path(f) for f in open_after if not any(Path(f).is_relative_to(r) for r in roots)]
    after = await asyncio.to_thread(snapshot, roots + late, before)
    created, changed, deleted = diff(before, after)
    obs = Observation([str(r) for r in roots], created, changed, deleted)
    obs.unread = merge_unread(before.unread, after.unread)
    obs.canary = [p for p in obs.written if holds(p, canary)]
    obs.sockets = {**sockets, **more}
    obs.open_files = sorted(set(open_before) | set(open_after))
    return obs


def _under(path: str, store: Store, folder: Path) -> bool:
    loc = store.locator.removeprefix("sqlite:///")
    if store.kind in ("sqlite", "dir", "vector") and not loc.startswith(
        ("env:", "package:", "service:")
    ):
        target = Path(loc).expanduser()
        target = target if target.is_absolute() else (folder / target)
        target = target.resolve()
        p = Path(path)
        return (
            p == target
            or p.is_relative_to(target)
            or (
                p.parent == target.parent and p.name.startswith(target.name)  # -wal, -shm, -journal
            )
        )
    return False


def reconcile(folder: Path, stores: list[Store], obs: Observation) -> list[Store]:
    """Stores marked with what the probe showed, plus the ones only the probe found.

    A written file no declared store covers becomes an ``undeclared`` store of its own; one that
    holds the canary is said so. A database socket marks every store of that kind as reached.
    """
    out = list(stores)
    for path in obs.written:
        hit = [s for s in out if _under(path, s, folder)]
        note = f"written: {path}" + (" (holds the canary)" if path in obs.canary else "")
        for s in hit:
            s.seen.append(note)
        if not hit:
            kind = "sqlite" if re.search(r"\.(db|sqlite3?)(-wal|-shm|-journal)?$", path) else "dir"
            out.append(Store(kind, path, ["not named anywhere in the code"], [note]))
    for peer, kind in obs.sockets.items():
        matched = [s for s in out if s.kind == kind]
        for s in matched:
            s.seen.append(f"connected: {peer}")
        if not matched:
            out.append(
                Store(
                    kind,
                    f"socket:{peer}",
                    ["not named anywhere in the code"],
                    [f"connected: {peer}"],
                )
            )
    return out
