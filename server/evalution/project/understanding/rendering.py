"""The understanding as text: ``profile.md``, for people and the agent's prompt, and the card."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
