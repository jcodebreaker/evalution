"""One record of what the code was (docs/architecture.md, "Drift"; ADR 0005).

"Has the code moved?" is asked from three marks, each the code as it was at one moment:

* **learned**: when the understanding (and the profile) were last written from it. Drift's code
  part and the learn's "nothing changed" compare with this. A successful knowledge refresh moves it
  for the files it covered.
* **refreshed**: the commit what it knows was last checked at (:mod:`evalution.project.knowledge`).
  Only commits move it.
* **tested**: the code when the run the change card compares with started
  (:mod:`evalution.project.impact`), with the last few run start states.

They live in one document per target, :data:`DOC`, beside **seen**: the code as the last check
took it. Every signal the page gets is worked out from these, so two of them can never name
different files for the same change (:func:`since`).

A mark with hashes is ``{commit, hashes, rules, clean?, at}``: ``hashes`` is ``file -> short
hash`` as :meth:`evalution.code.Code.hashes` gives it, ``rules`` the short hash of what is watched
(:meth:`evalution.code.Code.watch`). The refreshed mark is commits only: ``{commit, detected,
backlog_since?, at}``.

A target with no :data:`DOC` yet has its marks read once from the documents that kept them before
(``fingerprint.json``, ``files.json``, ``knowledge.json``, ``tested.json``) and folded in; those
documents are left where they are, and nothing writes these fields to them again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from evalution.code import Code
from evalution.project.profile import FINGERPRINT_DOC, read_doc, write_doc
from evalution.targets import Targets

DOC = "state.json"
VERSION = 1
MARKS = ("learned", "refreshed", "tested")
#: The documents the marks were kept in before :data:`DOC`.
FILES_DOC = "files.json"
KNOWLEDGE_DOC = "knowledge.json"
TESTED_DOC = "tested.json"
#: :data:`FILES_DOC` written under another version kept nothing a learn reads.
_FILES_VERSION = 2

# Every read and the write that follows it, for any target: short, and always taken last.
_lock = threading.RLock()


@dataclass(frozen=True)
class Changes:
    """The watched files since a mark. ``first``: there was none to compare with."""

    changed: list[str]
    added: list[str]
    removed: list[str]
    first: bool = False

    def __bool__(self) -> bool:
        return self.first or bool(self.changed or self.added or self.removed)

    @property
    def files(self) -> list[str]:
        """Every file named, in path order."""
        return sorted({*self.changed, *self.added, *self.removed})


NOTHING = Changes([], [], [])


def snapshot(code: Code, *, clean: bool = False) -> dict[str, Any]:
    """The code as it stands, as a mark: its commit, its files' hashes and the watch rules.
    *clean*: also say whether no watched file differs from the commit (one more git call)."""
    state = code.state()
    taken = {
        "commit": state["commit"],
        "hashes": state["files"],
        "rules": state["watch"],
        "at": time.time(),
    }
    if clean:
        taken["clean"] = code.clean()
    return taken


def between(mark: Mapping[str, Any] | None, now: Mapping[str, Any] | None) -> Changes | None:
    """What changed from *mark* to *now*, both marks with hashes; no file read. ``None`` when there
    is nothing to compare: no *now*, or a mark without hashes. No *mark* is ``first``.

    The same commit on a tree clean then and now is nothing changed, known without the hashes.
    Taken under other watch rules, only the files both watched compare: a file newly watched or
    newly left out is a change of the rules, not of the code."""
    if now is None or now.get("hashes") is None:
        return None
    if mark is None:
        return Changes([], sorted(now["hashes"]), [], first=True)
    before: Mapping[str, str] | None = mark.get("hashes")
    if before is None:
        return None
    after: Mapping[str, str] = now["hashes"]
    same_rules = mark.get("rules") == now.get("rules")
    commit = mark.get("commit")
    unmoved = same_rules and commit and commit == now.get("commit")
    if unmoved and mark.get("clean") and now.get("clean"):
        return NOTHING
    changed = sorted(p for p in after if p in before and before[p] != after[p])
    if not same_rules:
        return Changes(changed, [], [])
    return Changes(
        changed=changed,
        added=sorted(p for p in after if p not in before),
        removed=sorted(p for p in before if p not in after),
    )


# The document


def _hashed(old: Mapping[str, Any] | None, **extra: Any) -> dict[str, Any] | None:
    """A mark from an old code state (``{commit, files, watch}``)."""
    if not isinstance(old, Mapping) or not isinstance(old.get("files"), Mapping):
        return None
    mark = {
        "commit": old.get("commit"),
        "hashes": dict(old["files"]),
        "rules": old.get("watch"),
        **{k: old[k] for k in ("clean", "code", "at") if k in old},
    }
    return {**mark, **extra}


def _fold(targets: Targets, name: str) -> dict[str, Any]:
    """The marks as the documents before :data:`DOC` kept them."""
    doc: dict[str, Any] = {"version": VERSION, "learned": None, "refreshed": None, "tested": None}
    memory = read_doc(targets, name, FILES_DOC)
    if isinstance(memory, dict) and memory.get("version") == _FILES_VERSION:
        doc["learned"] = _hashed({**memory, "at": memory.get("learned_at")}, by="learn")
    if doc["learned"] is None:
        taken = read_doc(targets, name, FINGERPRINT_DOC)
        if isinstance(taken, dict):
            doc["learned"] = _hashed(
                {**(taken.get("code") or {}), "at": taken.get("taken_at")}, by="check"
            )
    known = read_doc(targets, name, KNOWLEDGE_DOC)
    if isinstance(known, dict) and "checked" in known:
        doc["refreshed"] = {
            "commit": known.get("checked"),
            "detected": known.get("detected"),
            **({"backlog_since": known["backlog_since"]} if known.get("backlog_since") else {}),
            "at": None,
        }
    tested = read_doc(targets, name, TESTED_DOC)
    if isinstance(tested, dict) and (tested.get("tested") or tested.get("runs")):
        runs = {k: _hashed(v) for k, v in (tested.get("runs") or {}).items()}
        mark = _hashed(tested.get("tested")) or {}
        doc["tested"] = {**mark, "runs": {k: v for k, v in runs.items() if v}}
    return doc


def read(targets: Targets, name: str) -> dict[str, Any]:
    """The marks of *name*: ``{learned, refreshed, tested, seen}``, each ``None`` until set. The
    first read of a target from before folds its old documents in."""
    with _lock:
        found = read_doc(targets, name, DOC)
        if isinstance(found, dict) and found.get("version") == VERSION:
            return found
        folded = _fold(targets, name)
        write_doc(targets, name, DOC, folded)
        return folded


def update(targets: Targets, name: str, change: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Read the marks, let *change* change them in place, and keep them, as one step."""
    with _lock:
        doc = read(targets, name)
        change(doc)
        write_doc(targets, name, DOC, doc)
        return doc


def mark(targets: Targets, name: str, which: str) -> dict[str, Any] | None:
    return read(targets, name).get(which)


def move(targets: Targets, name: str, which: str, value: Mapping[str, Any] | None) -> None:
    """Set mark *which* to *value* (``None`` forgets it)."""
    if which not in (*MARKS, "seen"):
        raise ValueError(f"no mark {which}")

    def put(doc: dict[str, Any]) -> None:
        doc[which] = dict(value) if value is not None else None

    update(targets, name, put)


def saw(targets: Targets, name: str, now: Mapping[str, Any]) -> None:
    """The code as a check just took it: what every mark is compared with."""
    move(
        targets,
        name,
        "seen",
        {k: v for k, v in now.items() if k in ("commit", "hashes", "rules", "at")},
    )


def cover(targets: Targets, name: str, files: Mapping[str, str | None]) -> None:
    """Move the learned mark for *files* only (``path -> hash now``, ``None``: gone): what they
    say is already current, so they no longer count as changed since learned."""
    if not files:
        return

    def put(doc: dict[str, Any]) -> None:
        learned = doc.get("learned")
        if not learned or learned.get("hashes") is None:
            return
        hashes = dict(learned["hashes"])
        for path, digest in files.items():
            if digest is None:
                hashes.pop(path, None)
            else:
                hashes[path] = digest
        # The commit no longer says what these hashes are: the hashes decide from here.
        doc["learned"] = {**learned, "hashes": hashes, "clean": False}

    update(targets, name, put)


def since(targets: Targets, name: str, which: str, code: Code | None = None) -> Changes | None:
    """What changed from mark *which* to the code as last seen: the one answer every signal uses.
    ``None`` when it cannot be said: the mark or a look at the code is missing, or (for the
    refreshed mark, commits only) there is no checkout or its commit is gone."""
    return since_marks(read(targets, name), which, code)


def since_marks(doc: Mapping[str, Any], which: str, code: Code | None = None) -> Changes | None:
    """:func:`since` over marks already read."""
    if which not in MARKS:
        raise ValueError(f"no mark {which}")
    found, now = doc.get(which), doc.get("seen")
    if found is None or now is None:
        return None
    if found.get("hashes") is not None:
        return between(found, now)
    since_commit, head = found.get("commit"), now.get("commit")
    if code is None or not since_commit or not head:
        return None
    if since_commit == head:
        return NOTHING
    names = code.changed_between(since_commit, head)
    if names is None:
        return None
    present = now.get("hashes") or {}
    return Changes(
        changed=sorted(n for n in names if n in present),
        added=[],
        removed=sorted(n for n in names if n not in present),
    )
