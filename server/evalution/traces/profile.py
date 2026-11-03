"""A compact digest of a target's recent sessions (docs/architecture.md, "Knowing a target"),
so the agent reads a page, not raw spans; and the fingerprint drift is checked against
("Drift").

The digest holds: tools (name, argument shapes, how often called, and the tools offered to the
model), trajectory shapes (tool sequences, delegation, depth), the main agent's system prompt
and its sections (from LLM spans; helpers' prompts are counted apart), and typical time, tokens
and cost per ask. An ask is one trace (one message and everything it set off); a session (a
conversation, which may live for days) only counts conversations.

Eval traffic is left out (:func:`is_eval`): sessions evalution opened itself, and traces
stamped with an eval run, as each adapter marks them (its ``own_traffic``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import statistics
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from evalution.adapters import own_traffic
from evalution.core.trace import tool_name
from evalution.traces.base import Span, TraceSource, ancestors, by_trace, get, is_ai, session_of
from evalution.traces.view import (
    Pricer,
    helper_of,
    request_of,
    span_tokens,
    span_usd,
    system_text,
    tool_args,
    tool_of,
)

#: How far back the digest looks for sessions.
LOOKBACK_HOURS = 24 * 14
#: The first window read back from now; each next one is twice as long.
FIRST_WINDOW_HOURS = 1
#: Pages of spans read at most while looking for sessions.
PAGE_CAP = 40
#: Pages read at most per page counted: pages with no AI span don't count, up to this many.
FLOOD_PAGES = 4
_HEADING = re.compile(r"^\s*(#{1,4}\s+.+|<[A-Za-z_][\w-]*>)\s*$")
_SEQ_LEN = 8


def is_eval(key: str, spans: Sequence[Span]) -> bool:
    """Whether session *key* with *spans* is eval traffic, not real use."""
    marks = own_traffic()
    if key.startswith(marks.sessions):
        return True
    for span in spans:
        if any(get(span.attrs, a) not in (None, "") for a in marks.attributes):
            return True
        user = str(get(span.attrs, "user.id") or "")
        if any(pattern.match(user) for pattern in marks.users):
            return True
    return False


def conversations(spans: Sequence[Span], attribute: str) -> dict[str, list[Span]]:
    """``session -> its spans``: the AI spans of every trace where one of them carries
    *attribute*. Other spans (plain HTTP server spans, say) and traces without the
    attribute are left out, and so is eval traffic (:func:`is_eval`)."""
    groups: dict[str, list[Span]] = {}
    for rows in by_trace(filter(is_ai, spans)).values():
        key = next((v for s in rows if (v := session_of(s, attribute)) is not None), None)
        if key is not None:
            groups.setdefault(str(key), []).extend(rows)
    return {key: rows for key, rows in groups.items() if not is_eval(key, rows)}


async def recent_sessions(
    source: TraceSource, attribute: str, sessions: int, max_pages: int = PAGE_CAP
) -> list[list[Span]]:
    """The *sessions* newest conversations' spans (see :func:`conversations`), oldest span
    first in each. Reads back from now in growing windows until it has found that many, read
    :data:`LOOKBACK_HOURS`, or read *max_pages* pages holding AI spans, so a project flooded with
    other spans still yields its conversations (:data:`FLOOD_PAGES` bounds the reading)."""
    now = time.time()
    oldest, end, window = now - LOOKBACK_HOURS * 3600, now, FIRST_WINDOW_HOURS * 3600.0
    spans: list[Span] = []
    pages = read = 0
    while end > oldest and pages < max_pages and read < max_pages * FLOOD_PAGES:
        start = max(oldest, end - window)
        async with contextlib.aclosing(source.pages(start, end)) as found:
            async for page in found:
                ours = [s for s in page if is_ai(s)]
                spans += ours
                read += 1
                pages += bool(ours)
                if pages >= max_pages or read >= max_pages * FLOOD_PAGES:
                    break
        if len(conversations(spans, attribute)) >= sessions:
            break
        end, window = start, window * 2
    groups = conversations(spans, attribute).values()
    newest = sorted(groups, key=lambda rows: max(s.end for s in rows), reverse=True)
    return [sorted(rows, key=lambda s: s.start) for rows in newest[:sessions]]


def _shape(value: Any) -> Any:
    """The type shape of a value: ``"str"``, ``{"key": "int"}``, ``["str"]``."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())[:12]}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


def offered_tools(request: Mapping[str, Any]) -> dict[str, list[str]]:
    """``tool name -> its parameter names`` from a request's tool list (any provider's shape)."""
    out: dict[str, list[str]] = {}
    for tool in request.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name")
        schema = fn.get("input_schema") or fn.get("parameters") or {}
        if isinstance(name, str):
            props = schema.get("properties") if isinstance(schema, dict) else None
            out[tool_name(name)] = sorted(props) if isinstance(props, dict) else []
    return out


def prompt_sections(text: str) -> list[dict[str, Any]]:
    """The system prompt cut at its headings (markdown ``#`` lines or ``<tag>`` lines), each
    with its size and hash; one ``(start)`` section for text before the first heading."""
    sections: list[tuple[str, list[str]]] = [("(start)", [])]
    for line in text.splitlines():
        if _HEADING.match(line):
            sections.append((line.strip().lstrip("#").strip()[:80], []))
        else:
            sections[-1][1].append(line)
    return [
        {
            "name": name,
            "chars": len(body := "\n".join(lines).strip()),
            "hash": hashlib.sha256(body.encode()).hexdigest()[:12],
        }
        for name, lines in sections
        if name != "(start)" or "\n".join(lines).strip()
    ]


def _depth(span: Span, by_id: Mapping[str, Span]) -> int:
    return sum(at.kind == "AGENT" for at in ancestors(span, by_id))


def _sequence(names: Sequence[str]) -> str:
    """Tool names in order with repeats folded: ``search×3 > write``."""
    folded: list[list[Any]] = []
    for name in names:
        if folded and folded[-1][0] == name:
            folded[-1][1] += 1
        else:
            folded.append([name, 1])
    parts = [f"{n}×{c}" if c > 1 else n for n, c in folded[:_SEQ_LEN]]
    return " > ".join(parts) + (" > …" if len(folded) > _SEQ_LEN else "") or "(no tools)"


def _median(values: Sequence[float]) -> float:
    return round(statistics.median(values), 4) if values else 0.0


def _p90(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 4)


def digest(sessions: Sequence[Sequence[Span]], price: Pricer | None = None) -> dict[str, Any]:
    """The digest of *sessions* (each a session's spans). Time, tokens, cost and calls are per
    ask (per trace), never per session."""
    called: Counter[str] = Counter()
    args: dict[str, dict[str, Any]] = {}
    offered: dict[str, list[str]] = {}
    sequences: Counter[str] = Counter()
    #: ``role -> system prompt hash -> count``; ``main`` is the app's own agent.
    systems: dict[str, Counter[str]] = {"main": Counter(), "helper": Counter()}
    system_by_hash: dict[str, str] = {}
    delegated = 0
    max_depth = 0
    per: dict[str, list[float]] = {
        "seconds": [],
        "tokens": [],
        "usd": [],
        "model_calls": [],
        "tool_calls": [],
    }
    asks = 0
    #: Per ask: its main agent's tool calls in order, every tool it set off, whether it
    #: delegated (the understanding's paths and diagram edges).
    per_ask: list[dict[str, Any]] = []
    for spans in sessions:
        by_id = {s.id: s for s in spans}
        for span in spans:
            if span.kind == "TOOL":
                name = tool_of(span)
                called[name] += 1
                shape = _shape(tool_args(span))
                if isinstance(shape, dict):
                    args.setdefault(name, {}).update(shape)
            elif span.kind == "LLM":
                request = request_of(span)
                for name, keys in offered_tools(request).items():
                    offered[name] = keys
                if system := system_text(request):
                    digest_id = hashlib.sha256(system.encode()).hexdigest()[:12]
                    systems["helper" if helper_of(span, by_id) else "main"][digest_id] += 1
                    system_by_hash[digest_id] = system
        max_depth = max([max_depth, *(_depth(s, by_id) for s in spans)])
        for trace in by_trace(spans).values():
            asks += 1
            tools = [s for s in trace if s.kind == "TOOL"]
            llms = [s for s in trace if s.kind == "LLM"]
            top = [s for s in tools if not helper_of(s, by_id)]
            sequences[_sequence([tool_of(s) for s in top])] += 1
            helped = any(helper_of(s, by_id) for s in trace)
            delegated += helped
            per_ask.append(
                {
                    "tools": [tool_of(s) for s in top],
                    "all": sorted({tool_of(s) for s in tools}),
                    "delegated": helped,
                }
            )
            per["seconds"].append(max(s.end for s in trace) - min(s.start for s in trace))
            per["tokens"].append(sum(sum(span_tokens(s)) for s in llms))
            per["usd"].append(sum(span_usd(s, price) for s in llms))
            per["model_calls"].append(len(llms))
            per["tool_calls"].append(len(tools))
    main = systems["main"] or systems["helper"]
    system_hash = main.most_common(1)[0][0] if main else ""
    system = system_by_hash.get(system_hash, "")
    return {
        "sessions": len(sessions),
        "asks": asks,
        "tools": {
            name: {
                "calls": called.get(name, 0),
                "args": args.get(name, {}),
                "offered": name in offered,
                "params": offered.get(name),
            }
            for name in sorted(set(called) | set(offered))
        },
        "tools_offered_known": bool(offered),
        "no_tool_asks": sum(1 for n in per["tool_calls"] if not n),
        "sequences": dict(sequences.most_common(8)),
        "delegated_share": round(delegated / asks, 2) if asks else 0.0,
        "max_depth": max_depth,
        "system": {
            "hash": system_hash,
            "hashes": sorted(main),
            "variants": len(main),
            "chars": len(system),
            "sections": prompt_sections(system),
            "helpers": sorted(systems["helper"]),
        },
        "typical": {
            key: {"median": _median(values), "p90": _p90(values)} for key, values in per.items()
        },
        "per_ask": per_ask,
    }


def render(found: Mapping[str, Any]) -> str:
    """The digest as a short markdown page."""
    asks, sessions = found.get("asks", 0), found["sessions"]
    lines = [
        f"# Trace digest ({asks} recent asks in {sessions} conversations)",
        "",
        "## Tools",
        "",
    ]
    for name, tool in found["tools"].items():
        shape = json.dumps(tool["args"], ensure_ascii=False) if tool["args"] else "-"
        offered = "" if tool["offered"] or not found["tools_offered_known"] else " (not offered)"
        lines.append(f"- {name}: {tool['calls']} call(s){offered}; args {shape[:300]}")
    lines.append(f"- {found.get('no_tool_asks', 0)} of {asks} asks used no tools")
    lines += ["", "## Trajectories (per ask)", ""]
    lines += [f"- {count}× {seq}" for seq, count in found["sequences"].items()]
    lines.append(
        f"- delegated work in {found['delegated_share']:.0%} of asks; "
        f"agent depth up to {found['max_depth']}"
    )
    system = found["system"]
    lines += [
        "",
        f"## Main system prompt ({system['chars']} chars, {system['variants']} variant(s))",
        "",
    ]
    lines += [f"- {s['name']} ({s['chars']} chars)" for s in system["sections"]] or [
        "- (none seen)"
    ]
    if system.get("helpers"):
        lines.append(f"- helpers use {len(system['helpers'])} other prompt(s)")
    typical = found["typical"]

    def pair(key: str, fmt: str) -> str:
        value = typical[key]
        return f"median {fmt.format(value['median'])}, p90 {fmt.format(value['p90'])}"

    lines += [
        "",
        "## Typical ask (one message and everything it set off)",
        "",
        f"- time: {pair('seconds', '{:.0f} s')}",
        f"- cost: {pair('usd', '${:.4f}')}",
        f"- tokens: {pair('tokens', '{:.0f}')}",
        f"- model calls: {pair('model_calls', '{:.0f}')}",
        f"- tool calls: {pair('tool_calls', '{:.0f}')}",
    ]
    return "\n".join(lines) + "\n"


def fingerprint(found: Mapping[str, Any]) -> dict[str, Any]:
    """What drift compares: the tools offered to the model with their parameter names (tools
    merely seen called are not compared: a small sample misses some), the main agent's prompt
    (its usual hash, every variant seen and its section hashes; helpers' prompts apart), and
    trajectory shape counts."""
    tools = {
        name: sorted(tool["params"])
        for name, tool in found["tools"].items()
        if tool["params"] is not None
    }
    return {
        "taken_at": time.time(),
        "sessions": found["sessions"],
        "asks": found.get("asks", 0),
        "tools": tools,
        "tools_offered_known": found["tools_offered_known"],
        "prompt": {
            "hash": found["system"]["hash"],
            "hashes": found["system"].get("hashes", []),
            "sections": {s["name"]: s["hash"] for s in found["system"]["sections"]},
            "helpers": found["system"].get("helpers", []),
        },
        "shapes": {
            "sequences": found["sequences"],
            "delegated_share": found["delegated_share"],
            "max_depth": found["max_depth"],
        },
    }
