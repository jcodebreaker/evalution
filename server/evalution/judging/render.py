"""What the grader reads of a try: one function, one budget (docs/judging.md, "Evidence").

Check lines, the reference and the rubric are never cut. The transcript and the tool items share
what is left: every item keeps its head and tail with a visible ``[3,200 chars cut]``, and when even
that doesn't fit, the first and last items stay and the count of those dropped is shown. How much
was cut is returned, and recorded on the checks that read it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from evalution.core.evidence import TraceStatus, TryEvidence
from evalution.llm import fenced

BUDGET = 24_000
REPLY_CHARS = 8_000
#: Where a test starts a new conversation. Without it an assistant that can't see what came before
#: looks like one ignoring what's in front of it: what it knows from there on is what it remembered.
NEW_CONVERSATION = (
    "--- The person starts a new conversation here. The assistant can't see the messages above; "
    "it knows only what it kept in memory. ---"
)
#: Below this an item says nothing worth reading: whole items go instead.
_ITEM_FLOOR = 160
_NOTHING_SEEN = {
    TraceStatus.COMPLETE: "(no tool calls seen)",
    TraceStatus.PARTIAL: "(no tool calls seen, and the traces are incomplete)",
    TraceStatus.EMPTY: "(unknown: no traces arrived)",
    TraceStatus.NO_SOURCE: "(unknown: this app has no traces)",
    TraceStatus.UNREAD: "(unknown: the traces couldn't be read)",
}


@dataclass(frozen=True)
class Rendered:
    text: str
    #: Characters of the evidence left out.
    cut: int


def _cut(text: str, room: int) -> tuple[str, int]:
    """*text* in about *room* characters, its head and tail kept."""
    if len(text) <= room:
        return text, 0
    head = room * 2 // 3
    tail = room - head
    gone = len(text) - room
    return f"{text[:head]} [{gone:,} chars cut] {text[-tail:]}", gone


def _share(sizes: Sequence[int], room: int) -> int:
    """The largest cap on each item that keeps their sum within *room*."""
    left, count = room, len(sizes)
    for size in sorted(sizes):
        if size * count > left:
            return left // count
        left -= size
        count -= 1
    return room


def _fit(items: Sequence[str], room: int) -> tuple[list[str], int]:
    """*items* within *room*. Past what head and tail can carry, the middle items go."""
    if not items:
        return [], 0
    keep = max(2, room // _ITEM_FLOOR)
    dropped = 0
    kept = list(items)
    if len(items) > keep:
        first, last = keep // 2, keep - keep // 2
        missing = items[first : len(items) - last]
        dropped = sum(map(len, missing))
        kept = [
            *items[:first],
            f"[{len(missing):,} items cut, {dropped:,} chars]",
            *items[len(items) - last :],
        ]
    cap = _share([len(i) for i in kept], room)
    fitted = [_cut(i, cap) for i in kept]
    return [text for text, _ in fitted], dropped + sum(gone for _, gone in fitted)


def _tool_lines(evidence: TryEvidence) -> list[str]:
    lines = []
    for tool in sorted(evidence.tools, key=lambda t: t.start):
        who = "helper " if tool.helper else ""
        args = " ".join(tool.args.text.split())
        result = " ".join(tool.result.text.split()) or "(no result)"
        lines.append(f"- {who}called {tool.name}({args})\n  -> {result}")
    return lines


def render(
    evidence: TryEvidence,
    reference: str,
    rubric: Sequence[tuple[int, str]],
    check_lines: Sequence[str] = (),
    budget: int = BUDGET,
) -> Rendered:
    """The grader's input: the conversation, what the assistant did, the final reply, the reference
    and the numbered *rubric*, each fenced as data. *check_lines* are what the code found."""
    reply, reply_cut = _cut(evidence.reply.strip(), REPLY_CHARS)
    lines = "\n".join(f"{n}. {line}" for n, line in rubric)
    checks = "\n".join(check_lines)
    said = []
    for at, turn in enumerate(evidence.turns):
        if at in evidence.breaks:
            said.append(NEW_CONVERSATION)
        said.append(f"Person: {turn.message.text}\nAssistant: {turn.reply.text}")
    tools = _tool_lines(evidence)
    fixed = len(reply) + len(lines) + len(reference) + len(checks)
    room = max(budget - fixed, _ITEM_FLOOR * 2)
    # The conversation and the tool items divide the room by how much each has to say.
    want = sum(map(len, said)) + sum(map(len, tools)) or 1
    said_room = max(_ITEM_FLOOR, room * sum(map(len, said)) // want)
    fitted_said, said_cut = _fit(said, said_room)
    fitted_tools, tools_cut = _fit(tools, max(_ITEM_FLOOR, room - said_room))
    did = "\n".join([*fitted_tools, *check_lines]) or _NOTHING_SEEN[evidence.trace]
    if fitted_tools and evidence.trace is TraceStatus.PARTIAL:
        did = f"(the traces are incomplete: more may have happened)\n{did}"
    return Rendered(
        "\n\n".join(
            [
                fenced("the conversation", "\n\n".join(fitted_said) or "(none)"),
                fenced("what the assistant did", did),
                fenced("the final reply", reply or "(no reply)"),
                fenced("what a good reply does", reference or "(no reference)"),
                fenced("the rubric", lines or "(no rubric)"),
            ]
        ),
        reply_cut + said_cut + tools_cut,
    )
