"""The understanding's Details (docs/architecture.md, "Facts and Details"): exact facts of the
code, assembled by the engine and never written by a model.

Per file, in the order asked: the facts read off it with no model (:mod:`.codemap.facts`), the
facts the light model gave of a slice file (:mod:`.filememory`), and those the writer noted of a
file it read. A model's fact counts only while its file is as it was read. Each becomes a claim
citing ``path:line``, so knowledge links, steering and the page treat it like any other claim,
with an id that stays while its file's facts do.

**The code wins.** A fact a model drew from a prose file (docs, an ADR) that names something the
code itself sets (a constant, a variable read, a setting) is left out: the static fact says what
holds. Any other fact from prose is marked as what the docs say.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from evalution.project import filememory
from evalution.project.codemap import CodeMap

_PROSE = re.compile(r"(^|/)(docs?|adr)/|\.(md|mdx|rst|txt|adoc)$", re.IGNORECASE)
#: Static fact kinds that set a value a prose fact could contradict.
_SETS = {"constant", "env", "config"}


def _claim(path: str, tag: str, fact: Mapping[str, Any], text: str) -> dict[str, Any]:
    line = fact.get("line")
    kind = "prompt" if fact.get("kind") in ("prompt", "prompt_rule") else "code"
    if _PROSE.search(path):
        kind = "docs"
    return {
        "id": f"fact-{tag}",
        "text": text,
        "evidence": [{"kind": kind, "ref": f"{path}:{line}" if line else path}],
    }


def assemble(
    cmap: CodeMap, memory: Mapping[str, Any], paths: Iterable[str]
) -> list[dict[str, Any]]:
    """The Details claims of the files *paths*, in that order."""
    set_by_code = {
        s for p in cmap.files for f in cmap.facts(p) if f["kind"] in _SETS for s in f["symbols"]
    }
    summaries = memory.get("summaries") or {}
    notes = memory.get("notes") or {}
    claims = []
    for path in dict.fromkeys(paths):
        tag = hashlib.sha256(path.encode()).hexdigest()[:8]
        fingerprint = (cmap.files.get(path) or {}).get("fingerprint")
        for n, fact in enumerate(cmap.facts(path)):
            claims.append(_claim(path, f"{tag}-s{n}", fact, fact["text"]))
        drawn = [
            (mark, fact)
            for mark, entry in (("m", summaries.get(path)), ("n", notes.get(path)))
            if filememory.current(entry, fingerprint)
            for fact in entry.get("facts") or []
        ]
        for n, (mark, fact) in enumerate(drawn):
            text = fact["fact"]
            if _PROSE.search(path):
                if set(fact.get("symbols") or ()) & set_by_code:
                    continue
                text = f"The docs say: {text}"
            claims.append(_claim(path, f"{tag}-{mark}{n}", fact, text))
    return claims
