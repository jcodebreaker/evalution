"""Which files the light model reads (docs/architecture.md, "Knowing a target"), chosen with no
model by what a file does, not by a share: every file that defines or registers a tool, a prompt or
prompt builder, an agent, a route, state or memory, a policy, or holds settings that shape
behaviour, and the files the rest of the code uses most. Everything else is read by the code map
alone (its card and static facts) and, when it matters, by the writer's own lookups.

``code.summarize`` in the target's code block caps the slice at that share of the watched files,
best first: what does the most, then rank, then how often it changed lately (git churn).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import PurePosixPath

from evalution.project.codemap import CodeMap

#: Files the slice always holds of the most used, whatever they define.
TOP_RANKED = 10
#: The most files a learn reads, best first: a big app's slice was 760, longer than a learn
#: could read. What's left out is still in the code map, and read when the writer asks for it.
MAX_FILES = 300
#: Months of history the churn counts.
CHURN_MONTHS = 6
_ENTRY = re.compile(r"^(main|app|server|index|agent|agents|graph|cli|__main__)\.", re.I)
#: Kinds of definition that make a file shape behaviour.
_DOES = {"tool", "prompt", "prompt-builder", "agent", "route", "state"}
#: Settings that shape behaviour, by the facts a settings file gives.
_SETTINGS = {"config", "env"}
_POLICY = re.compile(r"polic|guard|permission|safety|moderat|allow|deny|approv", re.I)


def does(cmap: CodeMap, path: str) -> bool:
    """Whether *path* shapes the app's behaviour by what it defines or holds."""
    kinds = {d.kind for d in cmap.defs.values() if d.path == path}
    facts = {f["kind"] for f in cmap.facts(path)}
    named = [d.name for d in cmap.defs.values() if d.path == path]
    policy = _POLICY.search(path) or any(_POLICY.search(n) for n in named)
    return bool(kinds & _DOES or facts & _SETTINGS or policy)


def choose(cmap: CodeMap, churn: Mapping[str, int], cap: float | None = None) -> list[str]:
    """The files of the slice, best first: those that shape behaviour and the
    :data:`TOP_RANKED` most used; at most ``ceil(cap × files)`` when *cap* is given, and never
    more than :data:`MAX_FILES`."""
    files = sorted(cmap.files)
    ranked = cmap.ranked_files()
    position = {p: n for n, p in enumerate(ranked)}
    most = max(churn.values(), default=0) or 1
    top = set(ranked[:TOP_RANKED])
    chosen = [p for p in files if p in top or does(cmap, p)]

    def score(path: str) -> float:
        rank = 1 - position.get(path, len(ranked)) / max(len(ranked), 1)
        entry = 0.3 * bool(_ENTRY.match(PurePosixPath(path).name))
        return 1.0 * does(cmap, path) + rank + entry + churn.get(path, 0) / most

    chosen.sort(key=lambda p: (-score(p), p))
    if cap is not None:
        chosen = chosen[: max(1, math.ceil(cap * len(files)))]
    return chosen[:MAX_FILES]
