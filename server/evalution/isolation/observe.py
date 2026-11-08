"""The running app watched while one probe goes through it: what it really touched.

:func:`watch` takes a snapshot of the folders the app uses, lets *act* send the probe (a message
carrying a canary), then takes another. The folders are the project's own plus every folder the
app's process (and its children) holds a file open in before the probe, outside system and
package folders, so a store at a path the code hardcodes elsewhere is still found. Open TCP
connections to the usual database ports name the stores it reaches over a socket.

Nothing here writes, deletes or sends anything but the probe *act* sends. Files are read only to
hash them and to look for the canary.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from evalution.code import SKIP_DIRS
from evalution.isolation.discover import Store

#: Files hashed at most per snapshot, and the largest hashed; larger ones compare size and time.
MAX_FILES, MAX_HASH = 20_000, 50 * 1024 * 1024
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
    r"\.cache/uv|\.rustup|\.cargo|Frameworks)/"
)


@dataclass(frozen=True)
class Entry:
    size: int
    mtime: int
    digest: str


Snapshot = dict[str, Entry]


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


def snapshot(roots: list[Path]) -> Snapshot:
    """Every file under *roots*, by absolute path."""
    out: Snapshot = {}
    for root in roots:
        walk = [(str(root.parent), [], [root.name])] if root.is_file() else os.walk(root)
        for dirpath, dirs, names in walk:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != ".git"]
            for name in names:
                path = Path(dirpath) / name
                try:
                    st = path.stat()
                except OSError:
                    continue
                if not path.is_file():
                    continue
                out[str(path)] = Entry(st.st_size, st.st_mtime_ns, _hash(path, st.st_size))
                if len(out) >= MAX_FILES:
                    return out
    return out


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


def roots_for(folder: Path, open_files: list[str], stores: list[Store]) -> list[Path]:
    """The folders to watch: the project, the folders of files the app holds open, and any
    absolute path a store names. Nested ones are dropped."""
    candidates = [folder.resolve()]
    candidates += [Path(f).parent for f in open_files]
    for store in stores:
        loc = store.locator
        if loc.startswith(("/", "~")):
            candidates.append(Path(loc).expanduser().parent)
        elif loc.startswith("sqlite:///"):
            candidates.append(Path(loc.removeprefix("sqlite:///")).expanduser().parent)
    out: list[Path] = []
    for path in sorted({p.resolve() for p in candidates if p.exists()}, key=lambda p: len(p.parts)):
        if path == Path(path.anchor) or path == Path.home():
            continue
        if not any(path.is_relative_to(kept) for kept in out):
            out.append(path)
    return out


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
    before = snapshot(roots)
    await act()
    open_after, more = open_by(pid) if pid else ([], {})
    after = snapshot(
        roots + [Path(f) for f in open_after if not any(Path(f).is_relative_to(r) for r in roots)]
    )
    created, changed, deleted = diff(before, after)
    obs = Observation([str(r) for r in roots], created, changed, deleted)
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
