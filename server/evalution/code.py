"""A target's source, read-only and scoped (docs/architecture.md, "Knowing a target" and
"Drift"): every source file under ``code.path``, whatever the project's layout, from git when
the folder is a checkout (tracked, plus untracked and not ignored), else from a walk. Tests,
lockfiles, build output, binaries, large files and whatever ``code.ignore`` names are left out,
and so is a secrets file, always, and anything through a link out of the folder. A
``code.include`` keeps only the files it names. Used by the
agent (``read_code``, ``list_code``, ``diff_code``), the drift fingerprint and the change card.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

from evalution.config import CodeConfig

READ_CAP = 64_000
LIST_CAP = 500
HASH_CAP = 2_000
HASH_SIZE = 1_000_000
DIFF_CAP = 60_000
#: Lines of context around each change: with no whole files sent beside it, a reader of the diff
#: needs to see what the change sits in.
DIFF_CONTEXT = 10
#: Commit ranges whose diffs are kept (:data:`Diffs`).
DIFFS_KEPT = 8
#: Files one diff names, and how much of a new file it shows.
DIFF_FILES = 500
#: What :meth:`Code.diff` says when no watched file changed: not a diff to read.
NOTHING_CHANGED = "(no watched file changed)"
NEW_FILE_CAP = 4_000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
#: Folders that hold no source: output and copies of what others wrote.
_OUTPUT_DIRS = {
    "out",
    "target",
    "coverage",
    "htmlcov",
    "vendor",
    "site-packages",
    "__snapshots__",
}
#: Hidden folders with no agent behaviour in them, or with credentials. Others stay: agent
#: instructions and prompts live in folders like these. A walk skips every hidden folder.
_HIDDEN_DIRS = {
    ".git",
    ".github",
    ".gitlab",
    ".vscode",
    ".idea",
    ".cache",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".nuxt",
    ".turbo",
    ".terraform",
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".docker",
    ".config",
}
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testdata", "fixtures"}
_TEST_NAMES = re.compile(
    r"^(test_.*|.*_test\.\w+|.*_spec\.\w+|.*\.(test|spec)\.\w+|conftest\.py)$", re.IGNORECASE
)
_LOCK_NAMES = re.compile(
    r"^(.*\.lock|.*\.lockb|package-lock\.json|npm-shrinkwrap\.json|pnpm-lock\.ya?ml|go\.sum|"
    r"packages\.lock\.json)$",
    re.IGNORECASE,
)
_NOT_SOURCE = re.compile(
    r"\.(min\.js|min\.css|map|pyc|pyo|class|o|a|so|dylib|dll|exe|wasm|jar|whl|"
    r"zip|gz|tgz|bz2|xz|7z|rar|tar|pdf|doc|docx|xls|xlsx|ppt|pptx|"
    r"png|jpe?g|gif|webp|avif|ico|icns|bmp|tiff?|heic|mp3|mp4|m4a|mov|wav|ogg|webm|"
    r"ttf|otf|woff2?|eot|sqlite3?|db|parquet|pkl|npy|npz|onnx|bin)$",
    re.IGNORECASE,
)
#: Bumped when the rules above change, so an old hash map is not read as every file changing.
_WATCH_RULES = "3"
_SECRET_NAMES = re.compile(
    r"^(\.env.*|.*\.(pem|key|p12|pfx|jks|keystore|kdbx)|id_(rsa|dsa|ecdsa|ed25519).*|"
    r"\.npmrc|\.pypirc|\.netrc|\.git-credentials|.*credentials.*|.*secrets?\..*)$",
    re.IGNORECASE,
)
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^-]{0,79}$")
#: Digests already taken: ``folder -> {file: (size, mtime, digest)}``, so a check that finds
#: nothing changed reads no file. ``None``: not text.
Digests = dict[Path, dict[str, tuple[int, int, str | None]]]
#: Diffs already made: ``(folder, since, until) -> {file: its diff}``. Commits never change, so a
#: range's diff is the same whoever asks: the knowledge refresh and the change card share it.
Diffs = dict[tuple[Path, str, str], dict[str, str]]


class CodeError(ValueError):
    """A path outside what the target lets evalution read."""


@cache
def glob_re(pattern: str) -> re.Pattern[str]:
    """A ``**``-aware glob as a regex over ``/``-separated relative paths."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(f"^{out}$")


def is_secret(path: str) -> bool:
    return any(_SECRET_NAMES.match(part) for part in Path(path).parts)


def _source(rel: str) -> bool:
    """Whether a relative path can be source at all, by its name and folders alone."""
    *folders, name = Path(rel).parts or ("",)
    return not (
        is_secret(rel)
        or any(
            f in _HIDDEN_DIRS or f.lower() in _TEST_DIRS or f in SKIP_DIRS or f in _OUTPUT_DIRS
            for f in folders
        )
        or name.startswith(".")
        or _TEST_NAMES.match(name)
        or _LOCK_NAMES.match(name)
        or _NOT_SOURCE.search(name)
    )


def _digest(path: Path) -> str | None:
    """Short hash of a text file; ``None`` for a binary or one that does not read."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data[:8_192] else hashlib.sha256(data).hexdigest()[:12]


class Code:
    def __init__(
        self, conf: CodeConfig, digests: Digests | None = None, diffs: Diffs | None = None
    ) -> None:
        self.root = conf.path
        self.ignore = conf.ignore
        self.include = conf.include
        self._digests: Digests = {} if digests is None else digests
        self._diffs: Diffs = {} if diffs is None else diffs

    def allowed(self, rel: str) -> bool:
        return (
            _source(rel)
            and (not self.include or any(glob_re(g).match(rel) for g in self.include))
            and not any(glob_re(g).match(rel) for g in self.ignore)
        )

    def resolve(self, path: str) -> Path:
        """The file *path* names, relative to the code folder.

        :raises CodeError: Outside the folder, not source, ignored by git, or a secrets file.
        """
        rel = path.strip().lstrip("/")
        parts = Path(rel).parts
        if not parts or ".." in parts:
            raise CodeError(f"{path}: not a path inside the code folder")
        if is_secret(rel):
            raise CodeError(f"{path}: secrets files are never read")
        if not self.allowed(rel) or self._git("check-ignore", "-q", "--", rel) is not None:
            raise CodeError(f"{path}: not part of the target's source")
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise CodeError(f"{path}: not found")
        return target

    def read(self, path: str) -> str:
        target = self.resolve(path)
        with target.open("rb") as handle:
            data = handle.read(READ_CAP + 1)
        text = data[:READ_CAP].decode("utf-8", errors="replace")
        return text + ("\n… (cut)" if len(data) > READ_CAP else "")

    def _listed(self) -> list[str]:
        """Every file of the folder, relative and sorted: from git, else a walk."""
        listed = self._git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if listed is not None:
            return sorted(set(filter(None, listed.split("\0"))))
        found: list[str] = []
        for folder, dirs, names in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            found += [(Path(folder) / n).relative_to(self.root).as_posix() for n in sorted(names)]
        return found

    def files(self, cap: int) -> list[str]:
        """Readable files, relative and sorted, at most *cap*."""
        found: list[str] = []
        for rel in self._listed():
            if not self.allowed(rel):
                continue
            path = self.root / rel
            try:
                small = path.is_file() and path.stat().st_size <= HASH_SIZE
            except OSError:
                continue
            if small and path.resolve().is_relative_to(self.root):
                found.append(rel)
                if len(found) >= cap:
                    return found
        return found

    def list(self, pattern: str = "**") -> tuple[list[str], int]:
        """Readable files matching glob *pattern*, at most :data:`LIST_CAP`, and how many more
        matched, so a reader knows to narrow the glob."""
        found = [f for f in self.files(HASH_CAP) if glob_re(pattern or "**").match(f)]
        return found[:LIST_CAP], max(0, len(found) - LIST_CAP)

    def hashes(self) -> dict[str, str]:
        """``file -> short hash``; a file whose size and time did not move is not read again."""
        seen = self._digests.get(self.root, {})
        kept: dict[str, tuple[int, int, str | None]] = {}
        for rel in self.files(HASH_CAP):
            path = self.root / rel
            try:
                stat = path.stat()
            except OSError:
                continue
            known = seen.get(rel)
            same = known and known[:2] == (stat.st_size, stat.st_mtime_ns)
            kept[rel] = (stat.st_size, stat.st_mtime_ns, known[2] if same else _digest(path))
        self._digests[self.root] = kept
        return {rel: found[2] for rel, found in kept.items() if found[2]}

    def state(self) -> dict[str, Any]:
        """The code as it stands: its last commit, its files' hashes, and what was watched
        (``watch``), since hash maps taken under other rules do not compare."""
        return {"commit": self.commit(), "files": self.hashes(), "watch": self.watch()}

    def watch(self) -> str:
        """Short hash of the rules that say which files are watched: the same files under the same
        rules, or not comparable."""
        rules = "\n".join([_WATCH_RULES, *sorted(self.ignore)])
        if self.include:
            rules += "\ninclude\n" + "\n".join(sorted(self.include))
        return hashlib.sha256(rules.encode()).hexdigest()[:8]

    def _git(self, *args: str) -> str | None:
        try:
            done = subprocess.run(  # noqa: S603
                # A repo's own settings never get to run anything from here.
                [  # noqa: S607
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "-C",
                    str(self.root),
                    *args,
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    def commit(self) -> str | None:
        """The repo's last commit, ``None`` when the folder is not a git checkout."""
        found = self._git("rev-parse", "HEAD")
        return found.strip() if found else None

    def clean(self) -> bool:
        """Whether no watched file differs from the last commit, untracked ones included; ``False``
        when this is not a checkout."""
        changed = self._git("diff", "--relative", "HEAD", "--name-only", "-z", "--")
        new = self._git("ls-files", "-z", "--others", "--exclude-standard")
        if changed is None or new is None:
            return False
        return not any(self.allowed(n) for n in (changed + new).split("\0") if n)

    def rewriting(self) -> bool:
        """Whether a rebase is under way: HEAD is passing through states nobody asked to keep."""
        for name in ("rebase-merge", "rebase-apply"):
            found = self._git("rev-parse", "--git-path", name)
            if found and (self.root / found.strip()).exists():
                return True
        return False

    def changed_between(self, since: str, until: str) -> list[str] | None:
        """The watched files that differ between two commits, the working tree left out (a rename
        names both sides); ``None`` when a commit is gone or this is not a checkout."""
        if not _REF.match(since) or not _REF.match(until):
            return None
        names = self._git("diff", "--relative", "--no-renames", "--name-only", "-z", since, until)
        if names is None:
            return None
        return [n for n in names.split("\0") if n and self.allowed(n)]

    def diff_between(self, since: str, until: str, files: list[str]) -> str:
        """The diff of *files* between two commits, each named so nothing else is shown, capped;
        nothing when it cannot be read. Each file's part is made once per range
        (:data:`Diffs`)."""
        named = [n for n in files if self.allowed(n)][:DIFF_FILES]
        if not named or not _REF.match(since) or not _REF.match(until):
            return ""
        key = (self.root, since, until)
        made = self._diffs.setdefault(key, {})
        if missing := [n for n in named if n not in made]:
            split = self._split(since, until, missing)
            if split is None:
                return ""
            made |= {n: "" for n in missing} | split
        # The newest ranges stay: one now asked for again moves to the end.
        self._diffs[key] = self._diffs.pop(key)
        for old in list(self._diffs)[:-DIFFS_KEPT]:
            del self._diffs[old]
        text = "".join(made[n] for n in named)
        return text[:DIFF_CAP] + ("\n… (cut)" if len(text) > DIFF_CAP else "")

    def _split(self, since: str, until: str, files: list[str]) -> dict[str, str] | None:
        """One ``git diff`` of *files*, cut into each file's part by its header; ``None`` when it
        does not read, so nothing is kept of it."""
        text = self._git(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--relative",
            f"-U{DIFF_CONTEXT}",
            since,
            until,
            "--",
            *(f":(literal){n}" for n in files),
        )
        if text is None:
            return None
        headers = {f"diff --git a/{n} b/{n}": n for n in files}
        out: dict[str, str] = {}
        current = None
        for line in text.splitlines(keepends=True):
            if line.startswith("diff --git "):
                # A name git had to quote matches no header, and is left out.
                current = headers.get(line.rstrip("\n"))
            if current is not None:
                out[current] = out.get(current, "") + line
        return out

    def hooks_folder(self) -> Path:
        """The repo's real hooks folder, ``core.hooksPath`` and worktrees honoured.

        :raises CodeError: Not a checkout; a folder in the working tree that git would see, since
            evalution never changes what a repo tracks; or a hooks path set outside this repo,
            which other repos share.
        """
        found = self._git("rev-parse", "--git-path", "hooks")
        common = self._git("rev-parse", "--git-common-dir")
        if found is None or common is None:
            raise CodeError("not a git checkout")
        folder = (self.root / found.strip()).resolve()
        if folder.is_relative_to((self.root / common.strip()).resolve()):
            return folder
        top = self._git("rev-parse", "--show-toplevel")
        if top and folder.is_relative_to(Path(top.strip()).resolve()):
            hook = str(folder / "post-commit")
            ignored = self._git("check-ignore", "-q", "--", hook) is not None
            if not ignored or self._git("ls-files", "--", str(folder)):
                raise CodeError("the hooks folder is part of the repo")
            return folder
        if self._git("config", "--local", "--get", "core.hooksPath") is None:
            raise CodeError("the hooks folder is shared with other repos")
        return folder

    def diff(self, since: str, only: set[str] | None = None) -> str:
        """The diff of the readable files from commit *since* to now (the working tree), new
        untracked files shown from their start; of the files in *only*, when given.

        :raises CodeError: *since* is not a commit ref, or the folder is not a git checkout.
        """
        if not _REF.match(since):
            raise CodeError(f"{since}: not a commit")
        names = self._git("diff", "--relative", "--name-only", "-z", since, "--")
        if names is None:
            raise CodeError(f"{since}: no such commit, or the code folder is not a git checkout")
        new = self._git("ls-files", "-z", "--others", "--exclude-standard") or ""

        def watched(listed: str) -> list[str]:
            found = [n for n in listed.split("\0") if n and self.allowed(n)]
            return [n for n in found if only is None or n in only][:DIFF_FILES]

        tracked, untracked = watched(names), watched(new)
        if not tracked and not untracked:
            return NOTHING_CHANGED
        head = self.commit()
        if head and self.clean():
            # Nothing uncommitted: this is the diff between two commits, made once for both.
            return self.diff_between(since, head, tracked)
        # Named one by one: without paths, git would show every file, secrets too.
        text = ""
        if tracked:
            text = (
                self._git(
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--relative",
                    since,
                    "--",
                    *(f":(literal){n}" for n in tracked),
                )
                or ""
            )
        for rel in untracked:
            if len(text) > DIFF_CAP:
                break
            try:
                text += f"\n--- new file {rel}\n{self.read(rel)[:NEW_FILE_CAP]}\n"
            except (CodeError, OSError):
                continue
        return text[:DIFF_CAP] + ("\n… (cut)" if len(text) > DIFF_CAP else "")
