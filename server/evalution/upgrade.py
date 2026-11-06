"""``evalution backup | upgrade | restore``: moving to new code without losing what is kept.

A backup is one timestamped folder, under ``<config folder>/backups`` by default: ``data/``,
``sets/``, ``targets/`` and ``settings/`` (the config, ``targets.yaml``, ``model.yaml``), with a
``manifest.json`` written last and holding every file's hash and first mode. ``data/`` holds the
database, ``evalution.db``, copied through SQLite's own backup so a server writing to it can't tear
the copy. A database ``$DATABASE_URL`` names that isn't SQLite is not in it: that one is backed up
with its own tools. A backup holds secrets, so every folder in it is 0700 and every file 0600.
``run.lock`` isn't kept: it means nothing later, and a restore must not swap the one it is holding.

All three hold the run lock throughout, so they refuse while a run is going. ``restore``, which
pulls files from under a ``serve``, refuses one on this data folder even when it's idle, and holds
it off until it's done (:class:`~evalution.results.store.FolderUse`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import secrets
import shlex
import shutil
import sqlite3
import stat
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from evalution.config import Config
from evalution.results.record import SCHEMA
from evalution.results.store import FolderUse, RunLock, RunStore
from evalution.results.views import detail, summary
from evalution.storage import Database
from evalution.storage.database import URL_ENV
from evalution.suite.cases import CaseError, list_sets, load_cases

_logger = logging.getLogger(__name__)
MANIFEST = "manifest.json"
BACKUPS = "backups"
#: In a backup, the files that live next to the config.
SETTINGS = "settings"
_SKIPPED_NAMES = frozenset({RunLock.FILE, "__pycache__"})
_SQLITE = (".db", ".sqlite", ".sqlite3")
#: What SQLite keeps beside a database; its online backup already holds what they do.
_SQLITE_SIDE = ("-wal", "-shm", "-journal")
_CHUNK = 1024 * 1024
_ELSEWHERE = (
    f"evalution: the database ${URL_ENV} names is not in it: back that one up with its own tools"
)


class UpgradeError(RuntimeError):
    """Why it refused or stopped; says so in words, and never holds a secret."""


@dataclass
class Backup:
    path: Path
    files: int
    size: int

    def said(self) -> str:
        return f"{self.path} ({self.files} files, {self.size / 1_000_000:.1f} MB)"


@dataclass
class Outcome:
    """What to print, and whether everything checked out."""

    lines: list[str] = field(default_factory=list)
    ok: bool = True


def _sections(config: Config) -> dict[str, Path]:
    return {"data": config.data_dir, "sets": config.sets_dir, "targets": config.targets_dir}


def _settings(config: Config) -> dict[str, Path]:
    """The files beside the config, unless ``data/`` already holds them."""
    found = (config.file, config.targets_file, config.model_file)
    return {p.name: p for p in found if not p.is_relative_to(config.data_dir)}


def _walk(folder: Path) -> Iterator[tuple[str, os.stat_result]]:
    """``(relative path, stat)`` of each file and link under *folder*, sorted, not following links.
    What a server removes while this reads is simply not there."""
    for current, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in _SKIPPED_NAMES)
        links = [d for d in dirs if (Path(current) / d).is_symlink()]
        for name in sorted({*names, *links} - _SKIPPED_NAMES):
            path = Path(current) / name
            if name.removesuffix("-wal").removesuffix("-shm").removesuffix("-journal").endswith(
                _SQLITE
            ) and name.endswith(_SQLITE_SIDE):
                continue
            with contextlib.suppress(FileNotFoundError):
                yield path.relative_to(folder).as_posix(), path.lstat()


def _is_sqlite(path: Path) -> bool:
    if path.suffix not in _SQLITE:
        return False
    with path.open("rb") as handle:
        return handle.read(16) == b"SQLite format 3\x00"


def _private_folders(path: Path) -> None:
    """*path* and what is missing above it, each 0700 (``mkdir(parents=True)`` makes the ones above
    with the default mode)."""
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for folder in reversed(missing):
        folder.mkdir(mode=0o700, exist_ok=True)


def _private_file(path: Path) -> int:
    _private_folders(path.parent)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def _copy_private(source: Path, dest: Path) -> None:
    """*source* to a new owner-only *dest*: a database through SQLite, so a server still writing to
    it can't leave a torn copy."""
    database = _is_sqlite(source)
    descriptor = _private_file(dest)
    if database:
        os.close(descriptor)
        reader = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=30)
        writer = sqlite3.connect(dest)
        try:
            reader.backup(writer)
        finally:
            writer.close()
            reader.close()
        return
    with os.fdopen(descriptor, "wb") as out, source.open("rb") as handle:
        shutil.copyfileobj(handle, out, _CHUNK)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _keep(source: Path, dest: Path, rel: str, found: os.stat_result, manifest: dict) -> None:
    if stat.S_ISLNK(found.st_mode):
        _private_folders(dest.parent)
        manifest["links"][rel] = os.readlink(source)
        os.symlink(manifest["links"][rel], dest)
    elif stat.S_ISREG(found.st_mode):
        _copy_private(source, dest)
        manifest["files"][rel] = {
            "mode": stat.S_IMODE(found.st_mode),
            "size": dest.stat().st_size,
            "sha256": _hash(dest),
        }


def _version() -> str:
    try:
        return metadata.version("evalution")
    except metadata.PackageNotFoundError:
        return "unknown"


def _new_folder(parent: Path) -> Path:
    _private_folders(parent)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for n in range(1000):
        path = parent / (stamp if not n else f"{stamp}-{n}")
        with contextlib.suppress(FileExistsError):
            path.mkdir(mode=0o700)
            return path
    raise UpgradeError(f"no free folder name under {parent}")


def _make_backup(config: Config, parent: Path, why: str) -> Backup:
    sources = _sections(config)
    if any(parent.resolve().is_relative_to(s) for s in sources.values()):
        raise UpgradeError(f"a backup can't go inside what it backs up: {parent}")
    path = _new_folder(parent)
    manifest: dict[str, Any] = {
        "evalution": _version(),
        "schema": SCHEMA,
        "why": why,
        "created": datetime.now().isoformat(timespec="seconds"),
        "files": {},
        "links": {},
    }
    try:
        for name, folder in sources.items():
            for rel, found in _walk(folder):
                with contextlib.suppress(FileNotFoundError):
                    _keep(folder / rel, path / name / rel, f"{name}/{rel}", found, manifest)
        for name, file in _settings(config).items():
            if file.is_file():
                rel = f"{SETTINGS}/{name}"
                _keep(file, path / rel, rel, file.lstat(), manifest)
        descriptor = _private_file(path / MANIFEST)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            json.dump(manifest, out, indent=1)
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(path, ignore_errors=True)
        raise UpgradeError(f"the backup failed, and nothing was changed: {exc}") from exc
    files = manifest["files"]
    return Backup(path, len(files), sum(int(f["size"]) for f in files.values()))


def _held(config: Config) -> RunLock:
    """The run lock, taken.

    :raises UpgradeError: A run is going.
    """
    lock = RunLock(config.data_dir)
    if not lock.acquire():
        raise UpgradeError("a run is going on this data folder: let it end, or stop it, first")
    return lock


def _elsewhere() -> bool:
    """Whether the database is one no backup of the folders holds: not SQLite."""
    url = os.environ.get(URL_ENV, "")
    return bool(url) and not url.startswith("sqlite")


def _runs(config: Config) -> RunStore:
    """The stored runs, through the database alone: nothing else in the folders a restore swaps
    is opened (a target's view would open its traces too)."""
    return RunStore(Database.open(config.data_dir).docs())


def _restore_command(config: Config, backup: Path) -> str:
    """The command that puts *backup* back, whatever way this one was started."""
    script = Path(sys.executable).with_name("evalution")
    start = [str(script)] if script.exists() else [sys.executable, "-m", "evalution.cli"]
    return shlex.join([*start, "--config", str(config.file), "restore", str(backup)])


def backup(config: Config, to: Path | None = None) -> Outcome:
    """A backup of everything now.

    :raises UpgradeError: It refused, or failed.
    """
    lock = _held(config)
    try:
        made = _make_backup(config, to or config.root / BACKUPS, "backup")
    finally:
        lock.release()
    return Outcome(
        [
            f"evalution: backed up to {made.said()}. It holds your secrets, owner-only",
            *([_ELSEWHERE] if _elsewhere() else []),
            f"evalution: to put it back: {_restore_command(config, made.path)}",
        ]
    )


def _as_text(view: Any) -> str:
    return json.dumps(view, sort_keys=True, ensure_ascii=False, default=str)


def _reports(store: RunStore) -> dict[str, str | None]:
    """What each stored run says on the page, in words to compare; ``None`` for one that doesn't
    read."""
    docs = {run: store.load(run) for run in store.ids()}
    by_set: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for doc in docs.values():
        if doc is not None and isinstance(doc.get("started_at"), (int, float)):
            by_set.setdefault((doc.get("target"), doc.get("set")), []).append(doc)
    for group in by_set.values():
        group.sort(key=lambda d: float(d["started_at"]), reverse=True)
    found: dict[str, str | None] = {}
    for stem, doc in docs.items():
        found[stem] = None
        if doc is not None:
            others = by_set.get((doc.get("target"), doc.get("set")), [])
            # A stored run can be off in any way at all; that is what this is here to report.
            try:
                found[stem] = _as_text([summary(doc), detail(doc, others, None)])
            except Exception as exc:
                _logger.warning("evals: run %s unreadable: %s", stem, type(exc).__name__)
    return found


def _tests(config: Config) -> tuple[int, list[str]]:
    """How many tests read, and what doesn't."""
    count, problems = 0, []
    folders = sorted(config.sets_dir.iterdir()) if config.sets_dir.is_dir() else []
    for folder in (f for f in folders if f.is_dir()):
        for name in list_sets(folder):
            try:
                count += len(load_cases(name, folder))
            except CaseError as exc:
                problems.append(f"{folder.name}/{name}: {exc}")
    return count, problems


def _check_reads(
    config: Config,
    store: RunStore,
    before: Mapping[str, str | None],
    tests_before: int,
    out: Outcome,
) -> None:
    after = _reports(store)
    gone = sorted(s for s, said in before.items() if said is not None and after.get(s) is None)
    moved = sorted(
        s for s, said in before.items() if said and after.get(s) and said != after.get(s)
    )
    bad = sorted(s for s, said in before.items() if said is None)
    good = sum(1 for said in after.values() if said is not None)
    out.lines.append(f"evalution: {good} of {len(after)} runs read after it")
    if gone:
        out.lines.append(f"evalution: PROBLEM: these no longer read: {', '.join(gone)}")
    if moved:
        out.lines.append(f"evalution: PROBLEM: these now read differently: {', '.join(moved)}")
    if bad:
        out.lines.append(f"evalution: these didn't read before it either: {', '.join(bad)}")
    count, problems = _tests(config)
    out.lines.append(f"evalution: {count} tests read after it")
    if count < tests_before:
        out.lines.append(f"evalution: PROBLEM: {tests_before} tests read before it")
    out.lines.extend(f"evalution: a test set doesn't read: {problem}" for problem in problems)
    out.ok = not (gone or moved or bad or problems or count < tests_before)


def upgrade(config: Config, to: Path | None = None) -> Outcome:
    """Back up, rewrite stored runs as today's, and show every run still says what it said.

    :raises UpgradeError: It refused, or the backup failed; nothing was changed.
    """
    lock = _held(config)
    try:
        made = _make_backup(config, to or config.root / BACKUPS, "upgrade")
        out = Outcome([f"evalution: backed up to {made.said()}. It holds your secrets, owner-only"])
        if _elsewhere():
            out.lines.append(_ELSEWHERE)
        store = _runs(config)
        before = _reports(store)
        tests_before = _tests(config)[0]
        try:
            changed, _ = store.upcast_all()
        except Exception as exc:  # the database's own errors, whatever the database
            raise UpgradeError(
                f"rewriting the runs stopped partway ({type(exc).__name__}). To get back what was "
                f"here: {_restore_command(config, made.path)}"
            ) from exc
        if not before:
            out.lines.append("evalution: no stored runs to rewrite")
        elif changed:
            out.lines.append(
                f"evalution: {len(changed)} of {len(before)} runs rewritten as today's shape: "
                f"{', '.join(changed)}"
            )
        else:
            out.lines.append(f"evalution: all {len(before)} runs already have today's shape")
        _check_reads(config, store, before, tests_before, out)
    finally:
        lock.release()
    out.lines.append(f"evalution: to undo all of it: {_restore_command(config, made.path)}")
    return out


def _safe_entry(rel: str, names: frozenset[str]) -> None:
    parts = Path(rel).parts
    if Path(rel).is_absolute() or ".." in parts or len(parts) < 2 or parts[0] not in names:
        raise UpgradeError(f"that backup names a file outside itself: {rel}")


def _read_manifest(path: Path, config: Config) -> dict[str, Any]:
    """The manifest of the backup at *path*, once every file in it is what the manifest says.

    :raises UpgradeError: It isn't a whole backup.
    """
    file = path / MANIFEST
    try:
        manifest = json.loads(file.read_text(encoding="utf-8"))
        files, links = manifest["files"], manifest["links"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UpgradeError(f"{path} isn't a whole backup: no readable {MANIFEST}") from exc
    names = frozenset({*_sections(config), SETTINGS})
    for rel in (*files, *links):
        _safe_entry(rel, names)
    for rel, entry in files.items():
        kept = path / rel
        if not kept.is_file() or kept.is_symlink() or _hash(kept) != entry["sha256"]:
            raise UpgradeError(f"that backup is damaged, so nothing was changed: {rel}")
    if any(not (path / rel).is_symlink() for rel in links):
        raise UpgradeError("that backup is damaged, so nothing was changed: a link is missing")
    return manifest


def _clear_except(folder: Path, kept: frozenset[str]) -> None:
    for item in folder.iterdir():
        if item.name in kept:
            continue
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()


def _restore_section(path: Path, name: str, folder: Path, manifest: Mapping[str, Any]) -> None:
    """Put one section back. It stays inside *folder* until the swap, which works on a folder that
    is itself a mount, and leaves the run lock alone."""
    folder.mkdir(parents=True, exist_ok=True)
    staging = folder / f".restoring-{secrets.token_hex(4)}"
    staging.mkdir(mode=0o700)
    for rel, entry in manifest["files"].items():
        section, _, inner = rel.partition("/")
        if section == name:
            dest = staging / inner
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path / rel, dest)
            dest.chmod(int(entry["mode"]))
    for rel, link in manifest["links"].items():
        section, _, inner = rel.partition("/")
        if section == name:
            dest = staging / inner
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(link, dest)
    _clear_except(folder, frozenset({staging.name, RunLock.FILE}))
    for item in staging.iterdir():
        item.replace(folder / item.name)
    staging.rmdir()


def _restore_settings(
    path: Path, config: Config, manifest: Mapping[str, Any], out: Outcome
) -> None:
    for name, file in _settings(config).items():
        rel = f"{SETTINGS}/{name}"
        entry = manifest["files"].get(rel)
        try:
            if entry is None:
                # The config itself is never removed: without it nothing says where anything is.
                if file != config.file:
                    file.unlink(missing_ok=True)
            elif not file.is_file() or _hash(file) != entry["sha256"]:
                tmp = file.with_name(f".{file.name}.{secrets.token_hex(4)}.tmp")
                shutil.copyfile(path / rel, tmp)
                tmp.chmod(int(entry["mode"]))
                tmp.replace(file)
        except OSError as exc:
            out.ok = False
            out.lines.append(
                f"evalution: PROBLEM: couldn't put {file} back ({exc.strerror}). "
                f"The backup's copy is {path / rel}"
            )


def restore(config: Config, source: Path, to: Path | None = None) -> Outcome:
    """Put the backup at *source* back, whole, after a backup of what is here now.

    :raises UpgradeError: It refused, and nothing was changed.
    """
    path = source.resolve()
    if any(path.is_relative_to(s) for s in _sections(config).values()):
        raise UpgradeError(f"a backup can't be restored from inside what it replaces: {path}")
    use = FolderUse(config.data_dir)
    if not use.alone():
        raise UpgradeError("evalution serve is running on this data folder: stop it first")
    try:
        return _restore(config, path, to)
    finally:
        use.release()


def _restore(config: Config, path: Path, to: Path | None) -> Outcome:
    lock = _held(config)
    try:
        manifest = _read_manifest(path, config)
        safety = _make_backup(config, to or config.root / BACKUPS, "before a restore")
        out = Outcome([f"evalution: what was here is backed up to {safety.said()}"])
        if _elsewhere():
            out.lines.append(f"evalution: the database ${URL_ENV} names is left as it is")
        else:
            # No connection may go on using the file the swap replaces.
            Database.open(config.data_dir).close()
        try:
            for name, folder in _sections(config).items():
                _restore_section(path, name, folder, manifest)
        except OSError as exc:
            raise UpgradeError(
                f"the restore stopped partway ({exc.strerror}). To get back what was here: "
                f"{_restore_command(config, safety.path)}"
            ) from exc
        _restore_settings(path, config, manifest, out)
        reports = _reports(_runs(config))
        good = sum(1 for said in reports.values() if said is not None)
        out.lines.append(
            f"evalution: restored {len(manifest['files'])} files from {path}, made "
            f"{manifest.get('created')} by evalution {manifest.get('evalution')}"
        )
        out.lines.append(f"evalution: {good} of {len(reports)} runs read")
        return out
    finally:
        lock.release()
