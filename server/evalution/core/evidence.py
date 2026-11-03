"""What one try left behind, as data: everything a judgement can rest on (docs/judging.md,
"Evidence"). ``runs/`` captures it, ``judging/`` reads it, and it is kept beside the run file in
``runs/<run>.evidence.jsonl.gz`` (:mod:`evalution.results.evidence`).

Texts are whole in memory, where checks read them, and capped when stored (:meth:`TryEvidence.
capped`): an item over :data:`ITEM_BYTES` keeps its head and tail, how much was left out and a hash
of the whole, so nothing vanishes silently.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any

from evalution.core.judgement import Frozen

ITEM_BYTES = 64_000
_TAIL_BYTES = 16_000
#: What a span holds in place of content kept from it: not captured, whatever else it says.
_REDACTED = re.compile(r"__REDACTED__|\[REDACTED\]|<REDACTED>", re.IGNORECASE)


class Failure(StrEnum):
    """What stopped a try early. A turn says ``app`` or ``unreachable``; the app was not there at
    all only for the second. The others are evalution's side of it, or neither side's fault yet."""

    APP = "app"
    UNREACHABLE = "unreachable"
    #: Out of time: :func:`evalution.core.rules.timeout_outcome` says what that comes to.
    TIMED_OUT = "timed_out"
    #: The app stopped to wait on something (an approval, say) and the test didn't expect it.
    BLOCKED = "blocked"
    NOT_SUPPORTED = "not_supported"
    OWN_MODEL = "own_model"
    #: The app did its part; the adapter didn't read it, or never came back.
    ADAPTER = "adapter"
    #: The app turned the credentials down (401, 403): nothing can be played until they're fixed.
    REFUSED = "refused"
    #: The app or a gateway in front of it was busy or down for now (429, 502, 503, 504).
    TRANSIENT = "transient"


class TurnEnd(StrEnum):
    """How a turn ended, as the adapter could tell. ``send`` waits for the work to end, so a reply
    is a final answer unless it says otherwise."""

    ANSWERED = "answered"
    #: The work is waiting on something: an approval, a person, an input.
    BLOCKED = "blocked"
    #: The work failed. Answering badly is the app's doing, whatever it replied.
    FAILED = "failed"

    @classmethod
    def _missing_(cls, value: Any) -> TurnEnd | None:
        """Evidence kept when an adapter could say a turn was only acknowledged: its try's
        ``failure`` and ``error`` say the rest."""
        return cls.ANSWERED if value == "acknowledged" else None


class TraceStatus(StrEnum):
    """How much of a try's spans were read."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    EMPTY = "empty"
    NO_SOURCE = "no_source"
    #: The source itself failed before anything was read: not the same as no spans having come.
    UNREAD = "unread"


class Clipped(Frozen):
    """A text, whole or capped. ``elided`` bytes were left out of the middle of it; ``sha256`` is
    of the whole text, when any were."""

    text: str = ""
    elided: int = 0
    sha256: str | None = None

    def capped(self, limit: int = ITEM_BYTES) -> Clipped:
        raw = self.text.encode()
        if len(raw) <= limit:
            return self
        head, tail = raw[: limit - _TAIL_BYTES], raw[-_TAIL_BYTES:]
        return Clipped(
            text=f"{head.decode(errors='ignore')}{tail.decode(errors='ignore')}",
            elided=self.elided + len(raw) - limit,
            sha256=hashlib.sha256(raw).hexdigest(),
        )


class TurnRecord(Frozen):
    """One message of the transcript and what came back. ``kind``: ``message`` or ``filler``.

    :param expected_blocked: The test wants this turn to stop and wait (``"blocked": true``).
    """

    kind: str
    message: Clipped
    reply: Clipped
    started: float
    ended: float
    error: str | None = None
    ended_as: TurnEnd = TurnEnd.ANSWERED
    expected_blocked: bool = False


class ToolRecord(Frozen):
    """One tool call. ``args`` is its arguments as JSON text."""

    id: str
    name: str
    args: Clipped
    result: Clipped
    start: float
    end: float | None = None
    helper: str | None = None


class ModelCallRecord(Frozen):
    """One model call: what it was sent (``sent``, empty when content was not captured) and said.

    :param helper: The delegated agent it ran under; ``None`` for the assistant's own.
    """

    n: int
    span: str
    t: float
    model: str
    sent: Clipped
    said: Clipped
    helper: str | None = None
    #: When it ended, from the try's start as ``t`` is; ``None`` in evidence kept before it was.
    ended: float | None = None

    @property
    def captured(self) -> bool:
        """Whether ``sent`` is what the model was sent, with nothing held back from it."""
        return bool(self.sent.text.strip()) and not _REDACTED.search(self.sent.text)


class StateRecord(Frozen):
    """One state check as it was made.

    :param at: Seconds from the try's first message when it was made.
    :param ok: The adapter's answer; ``None`` for a context check, decided from the traces.
    :param observed: Whether the adapter really read what it answers about.
    :param supported: Whether this target answers this check at all.
    """

    index: int
    key: str
    value: object
    at: float
    ok: bool | None = None
    observed: bool = True
    supported: bool = True
    actual: str | None = None
    reason: str = ""


class TryEvidence(Frozen):
    """One try: its transcript, tool calls, model calls and state checks, and how far its traces
    could be read. Span times are seconds from ``t0``, the first message."""

    case: str
    n: int
    t0: float
    seconds: float
    turns: tuple[TurnRecord, ...] = ()
    tools: tuple[ToolRecord, ...] = ()
    model_calls: tuple[ModelCallRecord, ...] = ()
    states: tuple[StateRecord, ...] = ()
    #: Where the person started a new conversation: how many turns had been played by then.
    breaks: tuple[int, ...] = ()
    #: Every AI span of the try, which a claim may cite.
    spans: tuple[str, ...] = ()
    trace: TraceStatus = TraceStatus.NO_SOURCE
    #: Why the trace read fell short, when a source said.
    trace_note: str | None = None
    failure: Failure | None = None
    error: str | None = None

    @property
    def reply(self) -> str:
        """The last thing the app said."""
        return self.turns[-1].reply.text if self.turns else ""

    @property
    def called(self) -> bool:
        """Whether the assistant was reached at all: any model call, or anything it said."""
        return bool(self.model_calls) or any(t.reply.text.strip() for t in self.turns)

    def capped(self) -> TryEvidence:
        """As stored: every item within :data:`ITEM_BYTES`."""
        return self.model_copy(
            update={
                "turns": tuple(
                    t.model_copy(update={"message": t.message.capped(), "reply": t.reply.capped()})
                    for t in self.turns
                ),
                "tools": tuple(
                    t.model_copy(update={"args": t.args.capped(), "result": t.result.capped()})
                    for t in self.tools
                ),
                "model_calls": tuple(
                    c.model_copy(update={"sent": c.sent.capped(), "said": c.said.capped()})
                    for c in self.model_calls
                ),
            }
        )
