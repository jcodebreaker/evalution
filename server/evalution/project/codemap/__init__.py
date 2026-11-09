"""The code map of a target (docs/architecture.md, "The code map"): what each watched file
defines and uses, from a parse and no model (:mod:`.parse`), read as one graph that ranks and walks
(:mod:`.graph`).

Each file's parse is kept as a document of kind :data:`KIND`, key ``<target>/<path>``, with the
fingerprint it was made from: one per file, so a learn writes only the files that moved, and all
of a target's are read at once by their key's prefix. No query needs more than that, so they are
documents, not a table of their own.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from evalution.code import Code, CodeError
from evalution.project import imports
from evalution.project.codemap.facts import static_facts
from evalution.project.codemap.graph import CodeMap, Definition
from evalution.project.codemap.parse import BEHAVIOUR, VERSION, parse, parses
from evalution.targets import Targets

KIND = "codemap"

__all__ = ["BEHAVIOUR", "CodeMap", "Definition", "Made", "build", "parses", "spans"]


@dataclass(frozen=True)
class Made:
    """A map as :func:`build` made it, and what that took."""

    map: CodeMap
    seconds: float
    parsed: int
    #: The files parsed again whose facts or definitions' signatures moved: what a summary of
    #: them could have to say differently.
    reshaped: frozenset[str] = frozenset()


def build(targets: Targets, name: str, code: Code) -> Made:
    """The map of *name*'s code now: a file parsed again only when its fingerprint moved."""
    begun = time.perf_counter()
    docs = targets.docs
    prefix = f"{name}/"
    files = code.hashes()
    kept = {key.removeprefix(prefix): body for key, body, _ in docs.items(KIND, prefix)}
    parsed = 0
    reshaped: set[str] = set()
    out = {}
    for path, fingerprint in files.items():
        entry = kept.get(path)
        if not entry or entry.get("fingerprint") != fingerprint or entry.get("version") != VERSION:
            try:
                text = code.read(path)
            except (CodeError, OSError):
                continue
            parsed_now = parse(path, text)
            fresh = {
                "fingerprint": fingerprint,
                "version": VERSION,
                **parsed_now,
                "facts": static_facts(path, text, parsed_now),
            }
            if _shape(fresh) != _shape(entry):
                reshaped.add(path)
            entry = fresh
            docs.put(KIND, prefix + path, entry)
            parsed += 1
        out[path] = entry
    for gone in set(kept) - set(files):
        docs.delete(KIND, prefix + gone)
    edges = imports.kept_graph(targets, name, code.root, files)
    return Made(
        CodeMap(out, edges), round(time.perf_counter() - begun, 3), parsed, frozenset(reshaped)
    )


def _shape(entry: dict | None) -> tuple:
    """What a file offers to read without its bodies: its facts and its definitions' lines."""
    if not entry:
        return ()
    facts = tuple(f["text"] for f in entry.get("facts") or [])
    return facts, tuple(sorted((d["name"], d["sig"]) for d in entry.get("defs") or []))


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
_FILE = re.compile(r"^(?:diff --git a/\S+ b/(\S+)|--- new file (\S+))$", re.M)


def spans(diff: str) -> dict[str, list[tuple[int, int]] | None]:
    """``path -> the new-side line spans`` a diff's hunks cover; ``None`` for a file it shows
    whole (new). A file the diff does not name is not in it."""
    out: dict[str, list[tuple[int, int]] | None] = {}
    heads = list(_FILE.finditer(diff))
    for i, head in enumerate(heads):
        body = diff[head.end() : heads[i + 1].start() if i + 1 < len(heads) else len(diff)]
        if head[2]:
            out[head[2]] = None
            continue
        out[head[1]] = [
            (int(m[1]), int(m[1]) + max(int(m[2] or 1), 1) - 1) for m in _HUNK.finditer(body)
        ]
    return out
