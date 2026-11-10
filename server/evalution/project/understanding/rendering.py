"""The understanding as text: ``profile.md``, for people and the agent's prompt, and the card."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.project.understanding.document import AREA_PREFIX, DETAILS
from evalution.project.understanding.steering import overlay

_TAG = {"traces": "[trace]", "admin": "[admin]"}


def _tags(evidence: Sequence[Mapping[str, str]]) -> str:
    """Evidence as profile tags: ``[trace]``, ``[admin]``, ``[docs: ref]`` (what docs say, which
    the code may contradict) or ``[code: ref]``."""
    tags = []
    for item in evidence:
        tag = _TAG.get(item["kind"]) or (
            f"[docs: {item['ref']}]" if item["kind"] == "docs" else f"[code: {item['ref']}]"
        )
        if tag not in tags:
            tags.append(tag)
    return " ".join(tags)


def _line(text: str, item: Mapping[str, Any]) -> str:
    """A claim's line: as the agent wrote it, or the admin's fix or verdict."""
    if item.get("fix"):
        return f"- {item['fix']} [admin]"
    if item.get("verdict") == "wrong":
        return f"- Not true, says the admin: {text} [admin]"
    return f"- {text} {_tags(item['evidence'])}"


def render_profile(name: str, doc: Mapping[str, Any], log: Sequence[Mapping[str, Any]]) -> str:
    """``profile.md`` from the understanding *doc* with the steers in *log*: every line but
    headings ends with its evidence, and what the admin fixed or marked wrong is an
    ``[admin]`` line."""
    doc = overlay(doc, log)
    lines = [f"# {name}", "", doc["one_line"], "", "## How it works", ""]
    for node in doc["nodes"]:
        tools = f" Tools: {', '.join(f'`{t}`' for t in node['tools'])}." if node["tools"] else ""
        text = f"**{node['title']}** ({node['kind']}): {node['sub']}".rstrip(": ") + "." + tools
        lines.append(_line(text, node))
    for section in doc["sections"]:
        lines += ["", f"## {section['title'] or section['id']}", ""]
        if section.get("note"):
            lines.append(f"{section['note']} [trace]")
        lines += [_line(c["text"], c) for c in section["claims"]]
    tools = [(t, n) for n in doc["nodes"] for t in n["tools"]]
    if tools:
        lines += ["", "## Tools", ""]
        lines += [f"- `{t}`: {n['title']} {_tags(n['evidence'])}" for t, n in tools]
    traced = doc.get("traced") or {}
    if traced.get("asks"):
        lines += ["", "## Typical ask", ""]
        if traced.get("ask_seconds") is not None:
            lines.append(f"- about {traced['ask_seconds']:.0f} s per ask [trace]")
    if doc.get("paths"):
        lines += ["", "## Typical paths", ""]
        for path in doc["paths"]:
            steps = " > ".join(path["steps"])
            lines.append(
                f"- {path['share']:.0%}: {steps} [trace]"
                if path["seen"]
                else f"- expected, never seen: {steps}"
            )
    picked = [p["title"] for p in doc.get("priorities") or [] if p["picked"]]
    if picked:
        lines += ["", "## What matters most to test (picked)", ""]
        lines += [f"- {title} [admin]" for title in picked]
    if doc.get("starters"):
        lines += ["", "## Proposed starter tests", ""]
        for s in doc["starters"]:
            source = f" From the library: {s['library']}." if s.get("library") else ""
            what = s["what"].rstrip(".")
            lines.append(f"- {s['title']}: {what}. Why: {s['why']}{source}")
    return "\n".join(lines) + "\n"


#: What the closing list of areas left out says.
LEFT_OUT = "Left out of this profile to fit"


def _size(section: Mapping[str, Any]) -> int:
    """About what *section* renders to: its heading and its claims' lines."""
    return len(str(section.get("title") or "")) + sum(
        len(str(c.get("text") or "")) + 40 for c in section.get("claims") or []
    )


def fit_profile(
    name: str, doc: Mapping[str, Any], log: Sequence[Mapping[str, Any]], cap: int
) -> tuple[str, dict[str, Any]]:
    """``profile.md`` of *doc* (:func:`render_profile`) in at most *cap* characters, and what is
    in it: ``{areas, of, left_out}``, the areas' sections kept, of how many, and the titles of
    those left out. Details is never part of it. Past *cap*, the areas' sections go, the heaviest
    first, and a closing list names them; past it still (an overview alone too long), the text is
    cut. It never refuses."""
    sections = [s for s in doc.get("sections") or [] if s.get("id") != DETAILS]
    areas = [s for s in sections if str(s.get("id") or "").startswith(AREA_PREFIX)]
    gone: list[Mapping[str, Any]] = []
    heaviest = sorted(areas, key=lambda s: (-_size(s), str(s.get("id"))))
    while True:
        dropped = {id(s) for s in gone}
        kept = {**doc, "sections": [s for s in sections if id(s) not in dropped]}
        text = render_profile(name, kept, log)
        if gone:
            titles = [str(s.get("title") or s.get("id")) for s in gone]
            said = f"{len(areas) - len(gone)} of {len(areas)} areas are in this profile."
            text += f"\n## {LEFT_OUT}\n\n{said}\n" + "".join(f"- {t}\n" for t in titles)
        if len(text) <= cap or len(gone) == len(areas):
            break
        # Drop as many of the heaviest as the excess needs at once, then measure again.
        excess, more = len(text) - cap, 0
        for section in heaviest[len(gone) :]:
            gone.append(section)
            more += _size(section)
            if more >= excess:
                break
    if len(text) > cap:
        mark = "\n\n(cut to fit)\n"
        text = text[: cap - len(mark)].rstrip() + mark
    left_out = [str(s.get("title") or s.get("id")) for s in gone]
    return text, {"areas": len(areas) - len(gone), "of": len(areas), "left_out": left_out}


#: A name worth matching in a conversation: one with an underscore, or a capital past its first
#: letter (``greet_people``, ``MemoryStore``), at least four characters.
_NAME = re.compile(r"\b[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9_]+|[a-z0-9][A-Z][A-Za-z0-9_]*)\b")
#: What an area section not named says in its place.
ASK_FOR_IT = "(not named in this conversation: read_understanding gives its claims)"


def _names_of(section: Mapping[str, Any], nodes: Sequence[Mapping[str, Any]]) -> set[str]:
    """What names *section*: its area id and title, its files' paths, the names its
    claims use, and the tools of the nodes that rest on its files."""
    refs = {
        str(e.get("ref") or "").split(":")[0]
        for c in section.get("claims") or []
        for e in c.get("evidence") or []
        if e.get("kind") in ("code", "prompt", "docs")
    } - {""}
    names = {str(section.get("id") or "").removeprefix(AREA_PREFIX), str(section.get("title"))}
    names |= refs
    names |= {m for c in section.get("claims") or [] for m in _NAME.findall(str(c.get("text")))}
    for node in nodes:
        if {str(e.get("ref") or "") for e in node.get("evidence") or []} & refs:
            names |= {str(t) for t in node.get("tools") or []}
    return {n for n in names if len(n) >= 4}


def only_named(doc: Mapping[str, Any], said: str) -> dict[str, Any]:
    """*doc* with the claims of only those area sections *said* (a conversation's text) names: by
    a tool, file or name of theirs, or the area itself. The rest keep their title, marked
    :data:`ASK_FOR_IT`. The overview's own sections, the nodes and their tools always stay."""
    text = said.lower()
    nodes = doc.get("nodes") or []
    out = []
    for section in doc.get("sections") or []:
        area = str(section.get("id") or "").startswith(AREA_PREFIX)
        if not area or any(n.lower() in text for n in _names_of(section, nodes)):
            out.append(section)
        else:
            out.append({**section, "title": f"{section['title']} {ASK_FOR_IT}", "claims": []})
    return {**doc, "sections": out}


def card_md(found: Mapping[str, Any]) -> str:
    """The card of a :func:`view` as Markdown."""
    card, n = found["card"], found["card"]["numbers"]
    lines = [f"# {card['name']}", ""]
    if card["one_line"]:
        lines += [card["one_line"], ""]
    if card["stack"]:
        lines += [" · ".join(card["stack"]), ""]
    lines.append(f"- {n['tools']} tools, {n['tools_seen']} seen in use")
    if n["state_kinds"]:
        lines.append(f"- State: {', '.join(n['state_kinds'])}")
    if n["ask_seconds"] is not None:
        lines.append(f"- Typical ask: {n['ask_seconds']:.0f} s")
    passing = f", {n['tests_passing']} pass" if n["tests_passing"] is not None else ""
    lines.append(f"- {n['tests']} tests{passing}")
    lines.append(
        f"- {n['library_covered']} of {n['library_total']} stateful checks covered from the library"
    )
    status = {"learning": "Learning", "up_to_date": "Up to date", "out_of_date": "Out of date"}
    foot = [status[card["status"]]]
    if card["changes"]:
        foot.append(f"{card['changes']} changes")
    if card["watched"]:
        foot.insert(0, f"Watched: {', '.join(card['watched'])}")
    lines += ["", " · ".join(foot)]
    return "\n".join(lines) + "\n"
