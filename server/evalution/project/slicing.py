"""Which files the light model reads (docs/architecture.md, "Knowing a target"): a slice, by
default a fifth of the watched files, chosen with no model. The rest is read by the code map alone
and, when it matters, by the writer's own lookups.

A file's claim to the slice adds up what says it matters: its rank in the code map (what the rest
uses), whether it holds prompts or prompt builders, whether it registers tools, routes or agents,
whether its name says it is where the app starts, and how often it changed lately (git churn).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import PurePosixPath

from evalution.project.codemap import CodeMap

#: The share of the watched files the light model reads, unless the target's code block says
#: (``code.summarize``).
SHARE = 0.20
#: Months of history the churn counts.
CHURN_MONTHS = 6
_ENTRY = re.compile(r"^(main|app|server|index|agent|agents|graph|cli|__main__)\.", re.I)
_PROMPTS = {"prompt", "prompt-builder"}
_REGISTERS = {"tool", "route", "agent"}


def choose(cmap: CodeMap, churn: Mapping[str, int], share: float) -> list[str]:
    """The files of the slice, best first: ``ceil(share × files)``, at least one."""
    files = sorted(cmap.files)
    if not files:
        return []
    ranked = cmap.ranked_files()
    position = {p: n for n, p in enumerate(ranked)}
    most = max(churn.values(), default=0) or 1
    kinds: dict[str, set[str]] = {p: set() for p in files}
    for d in cmap.defs.values():
        kinds[d.path].add(d.kind)

    def score(path: str) -> float:
        rank = 1 - position.get(path, len(ranked)) / max(len(ranked), 1)
        return (
            rank
            + 0.6 * bool(kinds[path] & _PROMPTS)
            + 0.5 * bool(kinds[path] & _REGISTERS)
            + 0.3 * bool(_ENTRY.match(PurePosixPath(path).name))
            + 1.0 * churn.get(path, 0) / most
        )

    wanted = max(1, math.ceil(share * len(files)))
    return sorted(files, key=lambda p: (-score(p), p))[:wanted]
