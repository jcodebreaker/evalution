"""What the agent writes in an understanding, and the checks it must pass."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from typing import Any

NODE_KINDS = ("person", "agent", "tools", "worker", "state")
SECTION_IDS = ("what_it_is", "can_do", "good_reply", "in_use")
EVIDENCE_KINDS = ("prompt", "code", "traces", "docs", "admin")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def clean_text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def read_evidence(value: Any, where: str, problems: list[str]) -> list[dict[str, str]]:
    items = value if isinstance(value, list) else []
    out = []
    for item in items:
        kind = item.get("kind") if isinstance(item, dict) else None
        ref = clean_text(item.get("ref")) if isinstance(item, dict) else ""
        if kind not in EVIDENCE_KINDS or not ref:
            problems.append(f"{where}: evidence is {{kind: {'|'.join(EVIDENCE_KINDS)}, ref}}")
            continue
        out.append({"kind": kind, "ref": ref.replace("]", ")")})
    if not out:
        problems.append(f"{where}: needs evidence (where you saw it)")
    return out


def _path_of(ref: str) -> str:
    """The file an evidence ref names: ``AGENTS.md "Delegation"`` -> ``AGENTS.md``."""
    return ref.split()[0].strip("\"'`") if ref.split() else ""


def _id(value: Any, where: str, seen: set[str], problems: list[str]) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        problems.append(f"{where}: id must be a short slug")
        return ""
    if value in seen:
        problems.append(f"{where}: id {value} is used twice")
    seen.add(value)
    return value


def check(
    args: Mapping[str, Any],
    offered: AbstractSet[str],
    in_code: Callable[[str, str], bool],
    library_ids: AbstractSet[str] = frozenset(),
    priority_ids: AbstractSet[str] = frozenset(),
) -> tuple[dict[str, Any], list[str]]:
    """``(what the agent wrote, read strictly; the problems)``. A node or claim without
    evidence is a problem, and so is a node tool that is neither in *offered* (the traces'
    tools) nor in a file the node cites (*in_code(path, tool)*). *priority_ids* are the
    admin's own priorities, which a starter may name too."""
    problems: list[str] = []
    one_line = clean_text(args.get("one_line"))
    if not one_line:
        problems.append("one_line: one sentence on what the app is")
    nodes, ids = [], set()
    for n, node in enumerate(args.get("nodes") if isinstance(args.get("nodes"), list) else []):
        node = node if isinstance(node, dict) else {}
        where = f"nodes[{n}]"
        found = {
            "id": _id(node.get("id"), where, ids, problems),
            "kind": node.get("kind"),
            "title": clean_text(node.get("title")),
            "sub": clean_text(node.get("sub")),
            "tools": [],
            "evidence": read_evidence(node.get("evidence"), where, problems),
        }
        if found["kind"] not in NODE_KINDS:
            problems.append(f"{where}: kind is one of {', '.join(NODE_KINDS)}")
        if not found["title"]:
            problems.append(f"{where}: needs a title")
        cited = [_path_of(e["ref"]) for e in found["evidence"] if e["kind"] != "traces"]
        for tool in node.get("tools") if isinstance(node.get("tools"), list) else []:
            if not isinstance(tool, str) or not tool.strip():
                continue
            tool = tool.strip()
            if tool not in offered and not any(in_code(path, tool) for path in cited if path):
                problems.append(
                    f"{where}: {tool} is not in the traces' tools"
                    + (f" nor in {', '.join(cited)}" if cited else "")
                )
            found["tools"].append(tool)
        nodes.append(found)
    if not nodes:
        problems.append("nodes: at least the agent itself")
    sections, claim_ids = [], set()
    for n, section in enumerate(
        args.get("sections") if isinstance(args.get("sections"), list) else []
    ):
        section = section if isinstance(section, dict) else {}
        where = f"sections[{n}]"
        if section.get("id") not in SECTION_IDS:
            problems.append(f"{where}: id is one of {', '.join(SECTION_IDS)}")
        claims = []
        for c, claim in enumerate(
            section.get("claims") if isinstance(section.get("claims"), list) else []
        ):
            claim = claim if isinstance(claim, dict) else {}
            at = f"{where}.claims[{c}]"
            read = {
                "id": _id(claim.get("id"), at, claim_ids, problems),
                "text": clean_text(claim.get("text")),
                "evidence": read_evidence(claim.get("evidence"), at, problems),
            }
            if not read["text"]:
                problems.append(f"{at}: needs text")
            claims.append(read)
        read_section = {
            "id": section.get("id"),
            "title": clean_text(section.get("title")),
            "claims": claims,
        }
        if note := clean_text(section.get("note")):
            read_section["note"] = note
        sections.append(read_section)
    priorities, seen_priorities = [], set()
    for n, item in enumerate(
        args.get("priorities") if isinstance(args.get("priorities"), list) else []
    ):
        item = item if isinstance(item, dict) else {}
        where = f"priorities[{n}]"
        read = {
            "id": _id(item.get("id"), where, seen_priorities, problems),
            "title": clean_text(item.get("title")),
            "picked": item.get("picked") is True,
            "source": "agent",
        }
        if not read["title"]:
            problems.append(f"{where}: needs a title")
        priorities.append(read)
    starters, seen_starters = [], set()
    for n, item in enumerate(
        args.get("starters") if isinstance(args.get("starters"), list) else []
    ):
        item = item if isinstance(item, dict) else {}
        where = f"starters[{n}]"
        read = {
            "id": _id(item.get("id"), where, seen_starters, problems),
            "title": clean_text(item.get("title")),
            "what": clean_text(item.get("what")),
            "why": clean_text(item.get("why")),
            "selected": item.get("selected") is True,
        }
        if not read["title"] or not read["what"]:
            problems.append(f"{where}: needs a title and what it does")
        if priority := item.get("priority"):
            if priority not in seen_priorities | set(priority_ids):
                problems.append(f"{where}: no priority {priority}")
            read["priority"] = priority
        if template := item.get("library"):
            if template not in library_ids:
                problems.append(f"{where}: no library test {template}")
            read["library"] = template
        starters.append(read)
    labels = args.get("step_labels") if isinstance(args.get("step_labels"), dict) else {}
    step_labels = {str(k): clean_text(v) for k, v in labels.items() if clean_text(v)}
    expected = []
    for steps in args.get("expected_paths") if isinstance(args.get("expected_paths"), list) else []:
        clean = [clean_text(s) for s in steps if clean_text(s)] if isinstance(steps, list) else []
        if clean:
            expected.append(clean)
    return {
        "one_line": one_line,
        "nodes": nodes,
        "sections": sections,
        "priorities": priorities,
        "starters": starters,
        "step_labels": step_labels,
        "expected_paths": expected,
    }, problems


def patched(doc: Mapping[str, Any], edit: Mapping[str, Any]) -> dict[str, Any]:
    """*doc* with *edit* laid over it, the rest exactly as it was: ``nodes`` and ``claims``
    (each with its ``section``) replace the one with their id where it stands, or are added;
    ``remove`` (``claim:<id>``, ``node:<id>``) leaves those out; ``one_line`` replaces it. What
    comes out goes through :func:`check` like any write.

    :raises ValueError: A claim for a section the understanding does not have.
    """
    out = copy.deepcopy(dict(doc))
    gone = {str(r) for r in edit.get("remove") or []}
    if clean_text(edit.get("one_line")):
        out["one_line"] = edit["one_line"]
    nodes = [n for n in out.get("nodes") or [] if f"node:{n.get('id')}" not in gone]
    for node in edit.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        at = next((i for i, n in enumerate(nodes) if n.get("id") == node.get("id")), None)
        if at is None:
            nodes.append(node)
        else:
            nodes[at] = node
    out["nodes"] = nodes
    sections = out.get("sections") or []
    for section in sections:
        section["claims"] = [c for c in section["claims"] if f"claim:{c.get('id')}" not in gone]
    for n, claim in enumerate(edit.get("claims") or []):
        if not isinstance(claim, dict):
            continue
        body = {k: v for k, v in claim.items() if k != "section"}
        placed = False
        for section in sections:
            for i, old in enumerate(section["claims"]):
                if old.get("id") == body.get("id"):
                    section["claims"][i], placed = body, True
        if placed:
            continue
        home = next((s for s in sections if s.get("id") == claim.get("section")), None)
        if home is None:
            raise ValueError(f"claims[{n}].section: one of {', '.join(s['id'] for s in sections)}")
        home["claims"].append(body)
    return out
