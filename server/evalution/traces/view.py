"""What a try's spans say: the one place span attributes are read (docs/architecture.md,
"Traces"). Everything here reads OpenInference attributes and the OpenTelemetry GenAI semantic
conventions alike, and :func:`build` turns spans into what a try records: tool calls (for the
grade), the timeline, one ``ContextCall`` per LLM span, the trajectory, tokens and cost.

GenAI attributes read: ``gen_ai.request.model`` / ``gen_ai.response.model``,
``gen_ai.usage.input_tokens`` / ``output_tokens`` (and the older ``prompt_tokens`` /
``completion_tokens``), ``gen_ai.tool.name``, ``gen_ai.tool.call.arguments`` / ``result``,
``gen_ai.tool.definitions``, ``gen_ai.system_instructions``, ``gen_ai.input.messages`` /
``gen_ai.output.messages`` (and the older ``gen_ai.prompt`` / ``gen_ai.completion``). The
conversation id is :func:`evalution.traces.base.session_of`'s. The conventions define no cost:
a GenAI span is priced from its model and tokens.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evalution.core.trace import Call, Section, tool_name
from evalution.traces.base import (
    SESSION_ATTRIBUTES,
    Span,
    ancestors,
    get,
    is_ai,
    parent_session_of,
    session_of,
)

TEXT_CHARS = 400
#: A session id shorter than this could turn up in a tool's result by chance.
MIN_SESSION_ID = 8
PREVIEW_CHARS = 400
IO_CHARS = 2_000
#: Deeper JSON in an attribute stays text: what is read here recurses over it.
JSON_DEPTH = 64

Sectioner = Callable[[dict[str, Any]], list[Section]]
Pricer = Callable[[str, int, int], float]


def _clip(text: str, limit: int = TEXT_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _shallow(value: Any, room: int = JSON_DEPTH) -> bool:
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return room > 0 and all(_shallow(v, room - 1) for v in value)
    return True


def _parsed(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "[{":
        try:
            found = json.loads(value)
        except (ValueError, RecursionError):
            return value
        return found if _shallow(found) else value
    return value


def flat_text(value: Any) -> str:
    """Every piece of text in *value* (a string, a block list, a message, a request), joined."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(t for t in map(flat_text, value) if t)
    if isinstance(value, dict):
        skip = {"role", "type", "id", "tool_call_id", "tool_use_id", "model", "cache_control"}
        return "\n".join(t for k, v in value.items() if k not in skip and (t := flat_text(v)))
    return ""


def _parts(message: Mapping[str, Any]) -> dict[str, Any]:
    """One GenAI message (``{"role", "parts": [{"type", ...}]}``) as the shape below gives."""
    said, calls = [], []
    for part in message["parts"]:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            said.append(flat_text(part.get("content")))
        elif kind == "tool_call_response":
            said.append(flat_text(_parsed(part.get("result"))))
        elif kind == "tool_call":
            function = {"name": part.get("name"), "arguments": part.get("arguments")}
            calls.append({"id": part.get("id"), "function": function})
    out: dict[str, Any] = {"role": message.get("role") or "user", "content": "\n".join(said)}
    if calls:
        out["tool_calls"] = calls
    return out


def _message(item: Any) -> dict[str, Any]:
    """One message: OpenInference's (``{"message": {...}}`` or the message itself) or GenAI's."""
    message = item.get("message", item) if isinstance(item, dict) else {}
    if isinstance(message.get("parts"), list):
        return _parts(message)
    content = message.get("content")
    if content is None and message.get("contents"):
        content = [
            (c.get("message_content") or c) for c in message["contents"] if isinstance(c, dict)
        ]
    out: dict[str, Any] = {"role": message.get("role") or "user", "content": content or ""}
    if message.get("tool_calls"):
        out["tool_calls"] = message["tool_calls"]
    return out


def _messages(span: Span, *paths: str) -> list[dict[str, Any]]:
    """The messages at the first of *paths* that holds any: a list, or JSON text of one. Plain
    text (the older ``gen_ai.prompt`` could be) is one message."""
    for path in paths:
        found = _parsed(get(span.attrs, path))
        if isinstance(found, list) and found:
            return [_message(m) for m in found]
        if isinstance(found, str) and found:
            return [{"role": "user", "content": found}]
    return []


def _genai_input(span: Span) -> list[dict[str, Any]]:
    return _messages(span, "gen_ai.input.messages", "gen_ai.prompt")


def request_of(span: Span) -> dict[str, Any]:
    """What an LLM span was sent: the provider body from ``input.value`` when it holds one,
    else messages and tools from ``llm.input_messages`` / ``llm.tools``, else from their GenAI
    counterparts, the system prompt apart in ``gen_ai.system_instructions``."""
    raw = _parsed(get(span.attrs, "input.value"))
    if isinstance(raw, dict) and isinstance(raw.get("messages"), list):
        return raw
    messages = _messages(span, "llm.input_messages") or _genai_input(span)
    tools = [
        _parsed(get(t, "tool.json_schema")) if isinstance(t, dict) else t
        for t in get(span.attrs, "llm.tools") or []
    ]
    defined = _parsed(get(span.attrs, "gen_ai.tool.definitions"))
    if not tools and isinstance(defined, list):
        tools = defined
    request: dict[str, Any] = {"messages": messages}
    if system := _parsed(get(span.attrs, "gen_ai.system_instructions")):
        request = {"system": system, **request}
    if tools:
        request["tools"] = tools
    if not messages and isinstance(raw, str) and raw:
        request["messages"] = [{"role": "user", "content": raw}]
    return request


def system_text(request: Mapping[str, Any]) -> str:
    """The system prompt of a request: its ``system`` field, else its system messages."""
    if request.get("system"):
        return flat_text(request["system"])
    return "\n".join(
        flat_text(m.get("content"))
        for m in request.get("messages") or []
        if isinstance(m, dict) and m.get("role") in ("system", "developer")
    )


def _tree(span: Span, by_id: Mapping[str, Span]) -> tuple[str, str]:
    """Which tree *span* is in, as far as has come: its trace, and the top span above it or the
    parent that one hangs from, which hasn't come yet."""
    top = next(reversed([span, *ancestors(span, by_id)]))
    return span.trace_id, top.parent or top.id


def answered_at(
    spans: Sequence[Span], since: float, *, enclosed: bool = True, asked: str | None = None
) -> float | None:
    """When the agent gave its answer: the end of a model call since *since* that said something and
    asked for no tool, in the turn's own tree (that of the turn's first span), with nothing but
    model calls begun after it, every tool its tree's calls asked for closed, and every span above
    it closed. ``None`` while it may be working: spans come when they end, so a tool still running
    shows only as a model call that asked for it, and a turn still open as a parent that hasn't
    come. A call with a root of its own, a title or a summary made on the side, is no answer. A
    model call with nothing above it shows no turn that ended at all, so that says nothing either.
    When in doubt this waits; the deadline still holds.

    :param enclosed: Off, for a turn known to be over, when only what the model said matters.
    :param asked: The message the turn was sent: a call whose captured request doesn't hold it was
        about something else, so it's no answer either.
    """
    by_id = {s.id: s for s in spans}
    turn = [s for s in spans if s.start >= since]
    if not turn:
        return None
    main = _tree(min(turn, key=lambda s: s.start), by_id)
    own = [s for s in turn if _tree(s, by_id) == main]
    models = [s for s in own if s.kind == "LLM"]
    calls = [s for s in models if output_text(s).strip()]
    if not calls:
        return None
    last = max(calls, key=lambda s: s.end)
    if any(s.kind != "LLM" and s.start >= last.end for s in spans) or _tools_asked(last):
        return None
    if sum(s.kind == "TOOL" for s in own) < sum(map(_tools_asked, models)):
        return None
    if not enclosed:
        return last.end
    sent = " ".join(flat_text(request_of(last)).split())
    if asked is not None and sent and " ".join(asked.split()) not in sent:
        return None
    above = [last, *ancestors(last, by_id)]
    return last.end if last.parent and not above[-1].parent else None


_TOOL_PARTS = frozenset({"tool_use", "tool_call", "function_call"})


def _tools_named(found: Any) -> int:
    """How many tools a model's output, in any provider's shape, asks for; at least one when only
    its stop reason says so."""
    if isinstance(found, list):
        return sum(map(_tools_named, found))
    if not isinstance(found, dict):
        return 0
    calls = found.get("tool_calls")
    named = (len(calls) if isinstance(calls, list) else int(bool(calls))) + int(
        found.get("type") in _TOOL_PARTS
    )
    named += sum(_tools_named(found.get(key)) for key in ("content", "choices", "message", "parts"))
    stopped = found.get("stop_reason") == "tool_use" or found.get("finish_reason") in (
        "tool_calls",
        "tool_use",
    )
    return max(named, int(stopped))


def _tools_asked(span: Span) -> int:
    messages = _messages(span, "llm.output_messages", "gen_ai.output.messages", "gen_ai.completion")
    return max(_tools_named(messages), _tools_named(_parsed(get(span.attrs, "output.value"))))


def output_text(span: Span) -> str:
    """What a span answered: the assistant messages of an LLM span, else ``output.value``."""
    messages = _messages(span, "llm.output_messages", "gen_ai.output.messages", "gen_ai.completion")
    said = "\n".join(flat_text(m.get("content")) for m in messages)
    if said.strip():
        return said
    out = _parsed(get(span.attrs, "output.value"))
    if isinstance(out, dict):
        choices = out.get("choices")
        if isinstance(choices, list) and choices:
            return flat_text((choices[0] or {}).get("message", {}).get("content"))
        if isinstance(out.get("content"), (list, str)):
            return flat_text(out["content"])
    return flat_text(out) if out is not None else ""


def ask_text(span: Span) -> str:
    """The person's message a (root) span received: the last user message of a request, a
    common field (``message``, ``input``, ``query``, ``question``), else the input as text."""
    raw = _parsed(get(span.attrs, "input.value"))
    if raw is None and (messages := _genai_input(span)):
        raw = {"messages": messages}
    if isinstance(raw, dict):
        users = [m for m in raw.get("messages") or [] if isinstance(m, dict)]
        users = [m for m in users if m.get("role") in ("user", "human")]
        if users:
            return flat_text(users[-1].get("content")).strip()
        for key in ("message", "input", "query", "question", "text", "prompt"):
            if isinstance(raw.get(key), str):
                return raw[key].strip()
    return flat_text(raw).strip() if raw is not None else ""


def tool_args(span: Span) -> dict[str, Any]:
    raw = _parsed(get(span.attrs, "tool.parameters"))
    if raw is None:
        raw = _parsed(get(span.attrs, "gen_ai.tool.call.arguments"))
    if raw is None:
        raw = _parsed(get(span.attrs, "input.value"))
    if isinstance(raw, dict):
        return raw
    return {} if raw in (None, "") else {"input": raw}


def tool_of(span: Span) -> str:
    named = get(span.attrs, "tool.name") or get(span.attrs, "gen_ai.tool.name")
    return tool_name(str(named or span.name))


def tool_output(span: Span) -> str:
    out = get(span.attrs, "output.value")
    found = _parsed(get(span.attrs, "gen_ai.tool.call.result") if out is None else out)
    said = flat_text(found)
    # Data rather than text blocks: flattened, its numbers and names would be lost.
    is_data = isinstance(found, dict) and not {"text", "content", "output", "result"} & found.keys()
    return json.dumps(found, ensure_ascii=False) if is_data else said


def model_of(span: Span) -> str:
    """The model a span called: the one that answered when it says, else the one asked for."""
    for path in ("llm.model_name", "gen_ai.response.model", "gen_ai.request.model"):
        if found := get(span.attrs, path):
            return str(found)
    return ""


def span_tokens(span: Span) -> tuple[int, int]:
    def n(*paths: str) -> int:
        for path in paths:
            value = get(span.attrs, path)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return 0

    tokens_in = n(
        "llm.token_count.prompt", "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"
    )
    tokens_out = n(
        "llm.token_count.completion", "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"
    )
    if not tokens_in and not tokens_out:
        tokens_in = n("llm.token_count.total", "gen_ai.usage.total_tokens")
    return tokens_in, tokens_out


def span_usd(span: Span, price: Pricer | None) -> float:
    cost = get(span.attrs, "llm.cost.total")
    if isinstance(cost, (int, float)):
        return float(cost)
    if not price:
        return 0.0
    # A dated model that answered may be priced only under the name that was asked for.
    for model in (model_of(span), get(span.attrs, "gen_ai.request.model")):
        if model and (usd := price(str(model), *span_tokens(span))):
            return usd
    return 0.0


def estimate_tokens(text: str) -> int:
    """A rough token count (characters / 4), the same everywhere a size is shown."""
    return math.ceil(len(text) / 4)


def context_call(
    n: int, span: Span, sectioner: Sectioner | None, *, preview: bool = True
) -> dict[str, Any]:
    """The ``ContextCall`` of LLM *span*: its sections (the adapter's names when it has them,
    else one ``turns`` section), tokens in from the span, and ``text`` (the whole request, which
    ``context_*`` checks search; not part of the API)."""
    request = request_of(span)
    sections = sectioner(request) if sectioner else []
    if not sections:
        sections = [Section("turns", flat_text(request))]
    rows: dict[str, dict[str, Any]] = {}
    for section in sections:
        row = rows.setdefault(section.name, {"name": section.name, "tokens": 0, "_text": []})
        row["tokens"] += (
            section.tokens if section.tokens is not None else estimate_tokens(section.text)
        )
        row["_text"].append(section.text)
    out = []
    for row in rows.values():
        text = "\n\n".join(t for t in row.pop("_text") if t)
        if preview and text:
            row["preview"] = text[:PREVIEW_CHARS]
        out.append(row)
    tokens_in = span_tokens(span)[0]
    return {
        "n": n,
        "span": span.id,
        "model": model_of(span),
        "tokens_in": tokens_in or sum(r["tokens"] for r in out),
        "sections": out,
        "text": flat_text(request),
        "system": system_text(request),
    }


@dataclass
class TraceView:
    """What a try's spans say. Times are seconds from the try's first message."""

    calls: list[Call] = field(default_factory=list)
    #: Each tool call's whole result, by its id. The trajectory and the timeline only show a clip.
    results: dict[str, str] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    context: list[dict[str, Any]] = field(default_factory=list)
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    usd: float = 0.0
    trace_ids: list[str] = field(default_factory=list)


def sessions_started_by_tools(tools: Iterable[Span], sessions: Iterable[str]) -> set[str]:
    """Those of *sessions* a tool's result names. Spans may carry no link at all from the call that
    started a sub-agent to its work; the id the call returned is then the only tie between them."""
    results = [str(get(s.attrs, "output.value") or "") for s in tools if s.kind == "TOOL"]
    return {
        session
        for session in sessions
        if len(session) >= MIN_SESSION_ID and any(session in result for result in results)
    }


def _agents_above(span: Span, by_id: Mapping[str, Span]) -> list[Span]:
    return [at for at in ancestors(span, by_id) if at.kind == "AGENT"]


def _crossed_at(span: Span, by_id: Mapping[str, Span]) -> Span | None:
    """The nearest of *span* and those above it that began delegated work: it names a parent
    session, or its parent is in another session or, not sharing a session, another trace."""
    name = SESSION_ATTRIBUTES[0]
    for at in (span, *ancestors(span, by_id)):
        above = by_id.get(at.parent or "")
        if parent_session_of(at):
            return at
        if above is None:
            continue
        mine, theirs = session_of(at, name), session_of(above, name)
        if (mine and theirs and mine != theirs) or (
            at.trace_id != above.trace_id and not (mine and mine == theirs)
        ):
            return at
    return None


def helper_of(span: Span, by_id: Mapping[str, Span]) -> str | None:
    """The delegated agent a span ran under: its nearest AGENT ancestor when that one has an
    AGENT above it (the top agent is the app itself), or the session or trace it was handed to.
    Agents on one session with no tool call between them are one agent in a wrapper, as when an app
    spans its whole turn: not delegation."""
    if (crossed := _crossed_at(span, by_id)) is not None:
        for at in (span, *ancestors(span, by_id)):
            if at.kind == "AGENT" or at is crossed:
                return at.id
    agents = _agents_above(span, by_id)
    if len(agents) < 2:
        return None
    near, top = agents[0], agents[-1]
    name = SESSION_ATTRIBUTES[0]
    same = session_of(near, name) is not None and session_of(near, name) == session_of(top, name)
    if same and not any(at.kind == "TOOL" for at in ancestors(near, by_id)):
        return None
    return near.id


_KIND = {"TOOL": "tool", "LLM": "model"}


def build(
    spans: Sequence[Span],
    t0: float,
    *,
    sectioner: Sectioner | None = None,
    price: Pricer | None = None,
    preview: bool = True,
) -> TraceView:
    """A try's calls, timeline, context, trajectory, tokens and cost from its *spans*. Only AI
    spans are in it; the others (plain HTTP and framework spans) only link AI spans together."""
    view = TraceView()
    by_id = {s.id: s for s in spans}
    ordered = sorted(filter(is_ai, spans), key=lambda s: s.start)
    session_name = SESSION_ATTRIBUTES[0]
    # A child session's later traces have no link of their own, and are its work all the same.
    handed_off = {
        session_of(at, session_name) for s in spans if (at := _crossed_at(s, by_id)) is not None
    } - {None}
    every_session = {str(v) for s in spans if (v := session_of(s, session_name)) is not None}
    for tool in filter(lambda s: s.kind == "TOOL", spans):
        mine = str(session_of(tool, session_name))
        handed_off |= sessions_started_by_tools([tool], every_session - {mine})
    # A sub-agent's results can name the session that started it, which is no sub-agent of its own.
    first = min(spans, key=lambda s: s.start, default=None)
    if first is not None:
        handed_off.discard(str(session_of(first, session_name)))

    def rel(at: float) -> float:
        return round(max(0.0, at - t0), 2)

    llm_n = 0
    for span in ordered:
        if span.trace_id and span.trace_id not in view.trace_ids:
            view.trace_ids.append(span.trace_id)
        helper = helper_of(span, by_id)
        if helper is None and session_of(span, session_name) in handed_off:
            helper = span.id
        actor = "helper" if helper else "assistant"
        row: dict[str, Any] = {
            "id": span.id,
            "parent": next((a.id for a in ancestors(span, by_id) if is_ai(a)), None),
            "kind": _KIND.get(span.kind, "agent"),
            "name": span.name,
            "start": rel(span.start),
            "seconds": round(max(0.0, span.end - span.start), 2),
        }
        if helper:
            row["helper"] = True
        if span.kind == "TOOL":
            args = tool_args(span)
            name = tool_of(span)
            row["name"] = name
            view.calls.append(Call(span.id, name, args, rel(span.start), rel(span.end), helper))
            row["input"] = json.dumps(args, ensure_ascii=False)[:IO_CHARS]
            result = tool_output(span)
            view.results[span.id] = result
            if result:
                row["output"] = result[:IO_CHARS]
            view.trajectory.append(
                {
                    "t": rel(span.start),
                    "actor": actor,
                    "kind": "called",
                    "text": _clip(f"{name}({json.dumps(args, ensure_ascii=False)})"),
                    "span": span.id,
                }
            )
            view.trajectory.append(
                {
                    "t": rel(span.end),
                    "actor": actor,
                    "kind": "result",
                    "text": _clip(result),
                    "span": span.id,
                }
            )
        elif span.kind == "LLM":
            llm_n += 1
            tokens_in, tokens_out = span_tokens(span)
            usd = span_usd(span, price)
            view.tokens += tokens_in + tokens_out
            view.usd += usd
            call = context_call(llm_n, span, sectioner, preview=preview)
            said = output_text(span)
            view.context.append(
                {
                    **call,
                    "t": rel(span.start),
                    "ended": rel(span.end),
                    "said": said,
                    "helper": helper,
                }
            )
            model = call["model"]
            row.update(
                name=model or span.name,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                usd=round(usd, 6),
            )
            if said.strip():
                view.trajectory.append(
                    {
                        "t": rel(span.end),
                        "actor": actor,
                        "kind": "said",
                        "text": _clip(said),
                        "span": span.id,
                    }
                )
        view.timeline.append(row)
    view.usd = round(view.usd, 6)
    view.trajectory.sort(key=lambda s: s["t"])
    return view
