"""The one agent a learn starts from, on a platform that hosts many (next-level C7, C11).

A repository can hold over a hundred agent definitions; learning them all drowns the learn and the
diagram. When the skeleton (:mod:`evalution.project.skeleton`) names more than :data:`ASK_OVER`
agents, connecting asks "Which agent do you want to test?" with :func:`choices`, and the answer is
kept as the target's ``code.focus``. :func:`agent_files` are the files that are that agent: its
definition, the prompts at it, and the tools it names (a tool listed in its settings is folded
into one entry whose other places name them). The learn's scope starts from those
(:func:`evalution.project.areas.agent_scope`); :func:`limit` keeps what a page shows to the same.

No model anywhere: the skeleton, the imports, and what real use touched.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evalution.code import Code, CodeError
from evalution.config import CodeConfig
from evalution.project import imports
from evalution.project.codemap.facts import static_facts
from evalution.project.codemap.graph import CodeMap
from evalution.project.codemap.parse import parse
from evalution.project.skeleton import Entry, Skeleton, skeleton

#: More agent definitions than this, and connecting asks which one is tested.
ASK_OVER = 3
#: The answer that keeps every agent: no ``code.focus``.
ALL = "*"
ALL_LABEL = "All of them"
QUESTION = "Which agent do you want to test?"
#: Steps through imports from an agent that still count as what it reaches.
HOPS = 2


def _paths(entry: Entry) -> list[str]:
    """The files *entry* is at: its own and its other places (``path:line``)."""
    return [entry.path, *(p.rsplit(":", 1)[0] for p in entry.places)]


def _own(name: str, entries: Iterable[Entry]) -> set[str]:
    """The files agent *name* is defined at."""
    return {p for e in entries if e.kind == "agent" and e.name == name for p in _paths(e)}


def agents(entries: Iterable[Entry]) -> list[Entry]:
    """The agent definitions, one per name (the first of a name, in the skeleton's order)."""
    seen: dict[str, Entry] = {}
    for e in entries:
        if e.kind == "agent" and e.name not in seen:
            seen[e.name] = e
    return list(seen.values())


def asks(entries: Iterable[Entry]) -> bool:
    """Whether connecting asks which agent is tested: more than :data:`ASK_OVER` of them."""
    return len(agents(entries)) > ASK_OVER


def agent_files(name: str, entries: Iterable[Entry]) -> set[str]:
    """The files that are agent *name*: where it is defined, and where the prompts and tools at
    those files are (a tool its settings list is defined elsewhere: that file too). Empty when
    no agent has that name."""
    found = list(entries)
    own = _own(name, found)
    if not own:
        return set()
    named = {
        p
        for e in found
        if e.kind in ("prompt", "tool") and own & set(_paths(e))
        for p in _paths(e)
        if e.kind == "prompt" or p == e.path
    }
    return own | named


def _reach(edges: Mapping[str, Iterable[str]], start: Iterable[str]) -> set[str]:
    begin = set(start)
    seen, front = set(begin), begin
    for _ in range(HOPS):
        front = {hit for rel in front for hit in edges.get(rel, ())} - seen
        seen |= front
    return seen


def agent_uses(name: str, entries: Sequence[Entry], uses: Mapping[str, int]) -> int:
    """How often real use touched agent *name*: the sends of the prompts at its files."""
    own = _own(name, entries)
    return sum(
        int(uses.get(e.id) or 0) for e in entries if e.kind == "prompt" and own & set(_paths(e))
    )


def choices(
    entries: Sequence[Entry],
    edges: Mapping[str, Iterable[str]] | None = None,
    uses: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """The agents to pick from, as a question's ``choices``: ``{value, label, hint, where, uses,
    tools}``, most used by real traces first (*uses*: entry id -> count; ``None``: no traces, so
    ``uses`` is ``None``), then by how many tools each reaches (its own, and those in the files it
    imports within :data:`HOPS`), then by name; "All of them" last."""
    edges = edges or {}
    tools = [e for e in entries if e.kind == "tool"]
    rows = []
    for agent in agents(entries):
        own = _own(agent.name, entries)
        reached = _reach(edges, own)
        count = sum(1 for t in tools if own & set(_paths(t)) or t.path in reached)
        used = agent_uses(agent.name, entries, uses) if uses is not None else None
        where = f"{agent.path}:{agent.line}"
        said = [where]
        if used is not None:
            said.append(f"{used} use" if used == 1 else f"{used} uses")
        said.append(f"{count} tool" if count == 1 else f"{count} tools")
        rows.append(
            {
                "value": agent.name,
                "label": agent.name,
                "hint": " · ".join(said),
                "where": where,
                "uses": used,
                "tools": count,
            }
        )
    rows.sort(key=lambda r: (-(r["uses"] or 0), -r["tools"], r["label"].lower()))
    every = {
        "value": ALL,
        "label": ALL_LABEL,
        "hint": f"{len(rows)} agents",
        "where": "",
        "uses": None,
        "tools": len(tools),
    }
    return [*rows, every]


def chosen(answer: Any, entries: Iterable[Entry]) -> str | None:
    """The focus an *answer* names: an agent of the skeleton, else ``None`` (all of them)."""
    if not isinstance(answer, str) or not answer.strip() or answer.strip() == ALL:
        return None
    name = answer.strip()
    return name if any(a.name == name for a in agents(entries)) else None


def limit(
    entries: Sequence[Entry], focus: str | None, scope: Collection[str] | None
) -> tuple[list[Entry], int]:
    """*entries* as a page shows them for *focus*: only the focused agent's definition among the
    agents, and the other entries at a file of *scope* (the learn's, from
    :func:`evalution.project.areas.agent_scope`; ``None``: every file); with how many agents were
    left out. No focus, or one no agent has: all of them, none left out."""
    if not focus or not any(a.name == focus for a in agents(entries)):
        return list(entries), 0
    within = set(scope) if scope is not None else None
    kept = [
        e
        for e in entries
        if (e.name == focus if e.kind == "agent" else within is None or within & set(_paths(e)))
    ]
    left = {a.name for a in agents(entries)} - {focus}
    return kept, len(left)


def limit_nodes(
    nodes: Sequence[Mapping[str, Any]], focus: str | None
) -> tuple[list[Mapping[str, Any]], int]:
    """The understanding's diagram *nodes* for *focus*: of the ``agent`` nodes, only the one
    named (by ``label`` or ``id``) and the first, the app's main one; with how many others were
    left out. No focus, or no node of that name: all of them."""
    agent_nodes = [n for n in nodes if n.get("kind") == "agent"]
    if not focus or not agent_nodes:
        return list(nodes), 0
    named = {focus.lower()}

    def is_focus(node: Mapping[str, Any]) -> bool:
        return str(node.get("label") or "").lower() in named or str(node.get("id")) in named

    if not any(is_focus(n) for n in agent_nodes):
        return list(nodes), 0
    kept = [n for n in nodes if n.get("kind") != "agent" or is_focus(n)]
    return kept, len(agent_nodes) - sum(1 for n in agent_nodes if is_focus(n))


def of_folder(folder: Path) -> tuple[Skeleton, dict[str, list[str]]]:
    """The skeleton and the import graph of the source in *folder*, with nothing kept: what
    connecting reads before there is a target to keep them under."""
    code = Code(CodeConfig(path=folder))
    files = code.hashes()
    parsed: dict[str, dict[str, Any]] = {}
    for path, print_ in files.items():
        try:
            text = code.read(path)
        except (CodeError, OSError):
            continue
        found = parse(path, text)
        parsed[path] = {"fingerprint": print_, **found, "facts": static_facts(path, text, found)}
    edges, _ = imports.graph(folder, files, {})
    return skeleton(CodeMap(parsed, edges), code.read), edges
