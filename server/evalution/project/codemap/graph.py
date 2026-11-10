"""The code map read: definitions joined by the names they use, ranked, and walked.

A reference resolves to the definitions of that name at read time: in its own file first, else in
the files its file imports (:mod:`evalution.project.imports`, a cheap hint), else anywhere, unless
the name is defined in so many places that it says nothing (``get``, ``run``).

**Rank** is a personalized PageRank over "uses" edges, plainly: what many definitions use, and what
the app's behaviour rests on (prompts, tools, agents, routes, state, and what a *focus* names),
comes first, then the constants that set its limits. The kinds weigh twice: in where a walk
restarts, and on the final score.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evalution.project.codemap.parse import BEHAVIOUR

#: How much a kind weighs in the rank: what an understanding must cover first.
WEIGHTS = {
    "prompt": 6.0,
    "prompt-builder": 6.0,
    "tool": 6.0,
    "agent": 6.0,
    "route": 4.0,
    "state": 4.0,
    "constant": 3.0,
    "class": 1.5,
}
#: What a *focus* adds to a definition it names, or one in a file it names.
FOCUS = 20.0
#: A name defined in more places than this resolves to none: it says nothing.
AMBIGUOUS = 6
DAMPING = 0.85
ROUNDS = 40
#: A file's role on its card, by the first kind it holds of each.
_ROLES = (
    ("routes", {"route"}),
    ("tools", {"tool"}),
    ("agent", {"agent"}),
    ("prompts", {"prompt", "prompt-builder"}),
    ("state", {"state"}),
    ("settings", {"config", "env"}),
    ("docs", {"section"}),
)
#: Names a card shows, and of its static facts how many and how much of each.
CARD_NAMES = 8
CARD_FACTS = 3
CARD_FACT_CHARS = 70
#: The most of one definition's code :meth:`CodeMap.walk` shows, and of the whole walk.
CODE_CHARS = 4_000
WALK_CHARS = 16_000
WALK_DEPTH = 2


@dataclass(frozen=True)
class Definition:
    path: str
    name: str
    kind: str
    line: int
    end: int
    sig: str

    @property
    def key(self) -> str:
        return f"{self.path}:{self.line}:{self.name}"

    def label(self) -> str:
        return f"{self.path}:{self.line} {self.sig}"


@dataclass
class CodeMap:
    """The definitions of a target's code and who uses whom. *files*: ``path -> parse``;
    *imports*: ``path -> the paths it imports``."""

    files: Mapping[str, Mapping[str, Any]]
    imports: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.defs: dict[str, Definition] = {}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        #: Each file's definitions' keys: what a file holds, without a scan of them all.
        self._at: dict[str, list[str]] = defaultdict(list)
        for path, parsed in sorted(self.files.items()):
            for d in parsed.get("defs") or []:
                found = Definition(path, d["name"], d["kind"], d["line"], d["end"], d["sig"])
                if found.key not in self.defs:
                    self._at[path].append(found.key)
                self.defs[found.key] = found
                self.by_name[found.name].append(found.key)
        self.uses: dict[str, set[str]] = defaultdict(set)
        self.used_by: dict[str, set[str]] = defaultdict(set)
        for path, parsed in self.files.items():
            for name, inside in parsed.get("refs") or []:
                source = self._in(path, inside)
                for target in self.resolve(name, path):
                    if source and target != source:
                        self.uses[source].add(target)
                        self.used_by[target].add(source)
        self.refs = sum(len(p.get("refs") or ()) for p in self.files.values())

    def _in(self, path: str, name: str | None) -> str | None:
        return next((k for k in self.by_name.get(name or "", ()) if k.startswith(f"{path}:")), None)

    def resolve(self, name: str, path: str) -> list[str]:
        """The definitions *name*, used in *path*, may mean."""
        found = self.by_name.get(name) or []
        here = [k for k in found if self.defs[k].path == path]
        if here:
            return here
        imported = set(self.imports.get(path) or ())
        there = [k for k in found if self.defs[k].path in imported]
        if there:
            return there
        return found if len(found) <= AMBIGUOUS else []

    # Rank

    def rank(self, focus: Iterable[str] = ()) -> dict[str, float]:
        """``definition -> score``, highest first."""
        keys = list(self.defs)
        if not keys:
            return {}
        named = set(focus)
        weight = {}
        for key, d in self.defs.items():
            w = WEIGHTS.get(d.kind, 1.0)
            if d.name in named or d.path in named:
                w += FOCUS
            weight[key] = w
        total = sum(weight.values())
        restart = {k: w / total for k, w in weight.items()}
        score = dict(restart)
        for _ in range(ROUNDS):
            nxt = {k: (1 - DAMPING) * restart[k] for k in keys}
            lost = 0.0
            for key in keys:
                out = self.uses.get(key)
                if out:
                    share = DAMPING * score[key] / len(out)
                    for target in out:
                        nxt[target] += share
                else:
                    lost += DAMPING * score[key]
            for key in keys:
                nxt[key] += lost * restart[key]
            score = nxt
        final = {k: score[k] * WEIGHTS.get(self.defs[k].kind, 1.0) for k in keys}
        return dict(sorted(final.items(), key=lambda kv: (-kv[1], kv[0])))

    def ranked_files(self, focus: Iterable[str] = ()) -> list[str]:
        """The files by the sum of their definitions' scores."""
        totals: dict[str, float] = defaultdict(float)
        for key, value in self.rank(focus).items():
            totals[self.defs[key].path] += value
        return sorted(totals, key=lambda p: (-totals[p], p))

    def render(self, keys: Iterable[str]) -> str:
        """Definitions grouped by file, in file and line order: a signature line each."""
        files: dict[str, list[Definition]] = defaultdict(list)
        for key in keys:
            files[self.defs[key].path].append(self.defs[key])
        return "\n".join(
            f"{path}:\n"
            + "\n".join(
                f"  {d.line}| {d.sig}" + (f"  [{d.kind}]" if d.kind in BEHAVIOUR else "")
                for d in sorted(found, key=lambda d: d.line)
            )
            for path, found in sorted(files.items())
        )

    def repo_map(self, budget: int, focus: Iterable[str] = ()) -> str:
        """The highest-ranked definitions that fit in *budget* characters, grouped by file: as
        many as fit, found by halving."""
        ranked = list(self.rank(focus))
        low, high, best = 0, len(ranked), ""
        while low <= high:
            middle = (low + high) // 2
            text = self.render(ranked[:middle])
            if len(text) <= budget:
                best, low = text, middle + 1
            else:
                high = middle - 1
        return best

    # Walking

    def defs_in(self, path: str) -> list[Definition]:
        """The definitions of the file at *path*: read from an index, so asking it of every file
        of a large repository stays linear."""
        return [self.defs[k] for k in self._at.get(path, ())]

    def find(self, name_or_file: str) -> list[str]:
        """The definitions a name means, or those of a file."""
        target = name_or_file.strip().lstrip("/")
        if target in self.files:
            return list(self._at.get(target, ()))
        return list(self.by_name.get(target) or [])

    def callees(self, key: str) -> list[str]:
        return sorted(self.uses.get(key) or ())

    def callers(self, key: str) -> list[str]:
        return sorted(self.used_by.get(key) or ())

    def reach_back(self, keys: Iterable[str], hops: int) -> set[str]:
        """What uses *keys*, up to *hops* steps back, *keys* included."""
        seen = set(keys)
        frontier = set(seen)
        for _ in range(hops):
            frontier = {c for k in frontier for c in self.used_by.get(k) or ()} - seen
            seen |= frontier
        return seen

    def overlapping(self, path: str, spans: Iterable[tuple[int, int]] | None) -> list[str]:
        """The definitions of *path* that the line *spans* touch; all of them for ``None``."""
        found = list(self._at.get(path, ()))
        if spans is None:
            return found
        spans = list(spans)
        return [
            k
            for k in found
            if any(a <= self.defs[k].end and b >= self.defs[k].line for a, b in spans)
        ]

    def code(self, key: str, read: Callable[[str], str]) -> str:
        """A definition's lines, capped."""
        d = self.defs[key]
        try:
            lines = read(d.path).splitlines()
        except (OSError, ValueError):
            return d.sig
        text = "\n".join(lines[d.line - 1 : d.end])
        return text[:CODE_CHARS] + ("\n… (cut)" if len(text) > CODE_CHARS else "")

    def neighbours(self, key: str) -> str:
        """What a definition uses and what uses it, a line each."""
        uses = [self.defs[k].label() for k in self.callees(key)]
        used = [self.defs[k].label() for k in self.callers(key)]
        return "\n".join(
            [
                "uses: " + ("\n  ".join(["", *uses]) if uses else "(nothing it defines)"),
                "used by: " + ("\n  ".join(["", *used]) if used else "(nothing in the map)"),
            ]
        )

    def walk(self, name_or_file: str, depth: int, read: Callable[[str], str]) -> str:
        """A definition's code, with what it uses and what uses it; at *depth* 2, those one step
        further by signature. Capped at :data:`WALK_CHARS`."""
        start = self.find(name_or_file)
        if not start:
            return f"(no definition or file {name_or_file} in the map)"
        parts = []
        for key in start:
            parts.append(
                f"{self.defs[key].label()}\n{self.code(key, read)}\n{self.neighbours(key)}"
            )
        if min(max(depth, 1), WALK_DEPTH) > 1:
            near = {n for k in start for n in (*self.callees(k), *self.callers(k))} - set(start)
            further = {n for k in near for n in (*self.callees(k), *self.callers(k))}
            further -= near | set(start)
            if further:
                parts.append("two steps away:\n" + self.render(sorted(further)))
        text = "\n\n".join(parts)
        return text[:WALK_CHARS] + ("\n… (cut)" if len(text) > WALK_CHARS else "")

    def card(self, path: str) -> str:
        """A file in one line, with no model: its role, guessed from what it defines and holds,
        the names that matter most there (behaviour first, then constants, in line order), and how
        many facts it gives. Stable while the file is: no rank in it."""
        defs = sorted(
            self.defs_in(path),
            key=lambda d: (d.kind not in BEHAVIOUR, d.kind != "constant", d.line),
        )
        kinds = {d.kind for d in defs} | {f["kind"] for f in self.facts(path)}
        role = next((r for r, wanted in _ROLES if kinds & wanted), "code" if defs else "other")
        names = ", ".join(d.name for d in defs[:CARD_NAMES])
        facts = "; ".join(f["text"][:CARD_FACT_CHARS] for f in self.facts(path)[:CARD_FACTS])
        return f"{path} ({role}): {names or '(nothing defined)'}" + (f" | {facts}" if facts else "")

    def facts(self, path: str) -> list[dict[str, Any]]:
        """The facts read off *path* with no model (:mod:`.facts`)."""
        return list((self.files.get(path) or {}).get("facts") or [])

    def behaviour(self, keys: Iterable[str] | None = None) -> list[Definition]:
        """The prompts, tools, agents, routes and state the map holds (of *keys*, when given)."""
        wanted = None if keys is None else set(keys)
        return [
            d
            for k, d in self.defs.items()
            if d.kind in BEHAVIOUR and (wanted is None or k in wanted)
        ]

    def innermost(self, keys: Iterable[str]) -> list[str]:
        """*keys* without those that hold another of them (a class whose method changed)."""
        keys = list(keys)

        def holds(outer: Definition, inner: Definition) -> bool:
            same = outer.path == inner.path and outer is not inner
            return same and outer.line <= inner.line and inner.end <= outer.end

        return [
            k for k in keys if not any(holds(self.defs[k], self.defs[o]) for o in keys if o != k)
        ]
