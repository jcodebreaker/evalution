"""Git hooks in a target's repo that tell evalution about a change (docs/architecture.md, "Keeping
what it knows current"). Only ``evalution hooks install`` or the Settings toggle write them.

Each hook gets a marked block near its top, after the shebang: a later ``exit`` in the owner's own
script cannot skip it, and nothing else in the file is touched. The block only starts a backgrounded
``curl`` with short timeouts and its output thrown away, so a git command is neither slowed nor
failed. Uninstall removes that block, and the file only when evalution made it and nothing else is
left. A hook that is a link, not a shell script, or switched off (not executable) is left alone.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

from evalution.code import Code, CodeError
from evalution.core.case import SLUG

HOOKS = ("post-commit", "post-merge", "post-rewrite", "post-checkout")
#: Marks a file evalution created, so uninstall knows it may remove it.
CREATED = "# Created by evalution; remove with: evalution hooks uninstall"
_SHEBANG = "#!/bin/sh"
_SHELLS = re.compile(r"^#!\s*(?:/\S*/)?(?:env\s+(?:-S\s+)?)?(?:sh|bash|dash|ash|ksh|zsh)(?:\s.*)?$")
_URL = re.compile(
    r"^https?://(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\])"
    r"(?::[0-9]{1,5})?$"
)
_ENCODING: dict[str, Any] = {"encoding": "utf-8", "errors": "surrogateescape"}


def _begin(name: str) -> str:
    return f"# >>> evalution {name} >>>"


def _end(name: str) -> str:
    return f"# <<< evalution {name} <<<"


def block(name: str, url: str) -> str:
    """The lines a hook gets.

    :raises ValueError: A target name or URL that is not plainly one.
    """
    if not SLUG.match(name):
        raise ValueError("target: lowercase letters, digits, - and _")
    if not _URL.match(url):
        raise ValueError("url: http(s)://host[:port], nothing else")
    target = shlex.quote(f"{url}/v1/targets/{name}/changed")
    # -q first: a person's ~/.curlrc has no say here.
    call = (
        f"curl -q -sS -m 3 --connect-timeout 1 --noproxy '*' -X POST {target} "
        "</dev/null >/dev/null 2>&1"
    )
    return f"{_begin(name)}\n( {call} & ) >/dev/null 2>&1 || true\n{_end(name)}\n"


def _without(text: str, name: str) -> str:
    """*text* without *name*'s block."""
    kept, inside = [], False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == _begin(name):
            inside = True
        elif inside:
            inside = stripped != _end(name)
        else:
            kept.append(line)
    return "".join(kept)


def _has(text: str, name: str) -> bool:
    return any(line.strip() == _begin(name) for line in text.splitlines())


def _with(text: str, name: str, url: str) -> str:
    """*text* with *name*'s block right after the shebang, the rest as it was."""
    text = _without(text, name)
    head, newline, rest = text.partition("\n")
    if head.startswith("#!"):
        return f"{head}\n{block(name, url)}{rest}" if newline else f"{head}\n{block(name, url)}"
    return block(name, url) + text


def _refused(path: Path) -> str | None:
    """Why the hook at *path* cannot carry the block; ``None`` when it can."""
    if path.is_symlink():
        return "is a link"
    if not path.exists():
        return None
    if not path.is_file():
        return "is not a file"
    if not os.access(path, os.X_OK):
        return "is switched off"
    first = path.read_text(**_ENCODING).partition("\n")[0]
    return "is not a shell script" if first.startswith("#!") and not _SHELLS.match(first) else None


def _write(path: Path, text: str, mode: int) -> None:
    """Replace *path* whole, so a commit running now reads the old file or the new one."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", **_ENCODING) as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def status(code: Code | None, name: str) -> dict[str, Any]:
    """``{available, installed, hooks: {hook: "on"|"off"|why it cannot}, reason?}``."""
    if code is None:
        return {"available": False, "installed": False, "hooks": {}, "reason": "no code folder"}
    try:
        folder = code.hooks_folder()
    except CodeError as exc:
        return {"available": False, "installed": False, "hooks": {}, "reason": str(exc)}
    states = {}
    for hook in HOOKS:
        path = folder / hook
        why = _refused(path)
        if why:
            states[hook] = why
        elif path.exists() and _has(path.read_text(**_ENCODING), name):
            states[hook] = "on"
        else:
            states[hook] = "off"
    return {
        "available": True,
        "installed": all(s == "on" for s in states.values()),
        "hooks": states,
    }


def install(code: Code | None, name: str, url: str) -> dict[str, Any]:
    """Add the block to each hook that can carry it; the same block again replaces it.

    :raises ValueError: A bad target name or URL.
    :raises CodeError: Not a place hooks can go.
    """
    block(name, url)
    if code is None:
        raise CodeError("no code folder")
    folder = code.hooks_folder()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for hook in HOOKS:
        path = folder / hook
        if _refused(path):
            continue
        if path.exists():
            _write(path, _with(path.read_text(**_ENCODING), name, url), path.stat().st_mode & 0o777)
        else:
            _write(path, f"{_SHEBANG}\n{CREATED}\n{block(name, url)}", 0o700)
    return status(code, name)


def uninstall(code: Code | None, name: str) -> dict[str, Any]:
    """Take the block out of each hook; a file evalution made goes when nothing else is left."""
    if code is None:
        return status(code, name)
    try:
        folder = code.hooks_folder()
    except CodeError:
        return status(code, name)
    for hook in HOOKS:
        path = folder / hook
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_text(**_ENCODING)
        if not _has(text, name):
            continue
        rest = _without(text, name)
        lines = [line.strip() for line in rest.splitlines() if line.strip()]
        if lines == [_SHEBANG, CREATED]:
            path.unlink()
        else:
            _write(path, rest, path.stat().st_mode & 0o777)
    return status(code, name)
