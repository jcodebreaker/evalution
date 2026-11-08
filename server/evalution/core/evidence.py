"""What one try left behind, as data: everything a judgement can rest on (docs/judging.md,
"Evidence"). ``runs/`` captures it, ``judging/`` reads it, and it is kept with its run
(:mod:`evalution.results.evidence`).

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
#: The most console lines, and failed requests, a page try keeps: the first ones.
PAGE_LINES = 200
#: A page try's video, by name in the run's file store.
VIDEO = "try.webm"
#: Why a passed try has no video, when its page map keeps one only when a try fails.
VIDEO_ON_FAILURE = "kept only when a try fails"
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
    #: Before its first message the app already held what the try was to show it come to hold:
    #: what an earlier try left, so the try wasn't played (:mod:`evalution.runs.leftover`).
    LEFTOVER_STATE = "leftover_state"
    #: The page target didn't find an element its page map names (ADR 0010): the map is off, not
    #: the app or the adapter.
    PAGE_MAP = "page_map"


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
    :param shown: What the app's own page showed, when the try played through it (ADR 0010).
    """

    kind: str
    message: Clipped
    reply: Clipped
    started: float
    ended: float
    error: str | None = None
    ended_as: TurnEnd = TurnEnd.ANSWERED
    expected_blocked: bool = False
    shown: Clipped | None = None


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


class MemoryRecord(Frozen):
    """The app's memory, read part-way through a try to measure whether it holds *want*.

    :param committed: What a new conversation would be given.
    :param written: What has been written, when that can differ; ``None`` when it can't.
    :param supported: Whether this target can show its memory at all.
    :param error: Why reading it failed.
    :param compacted: Whether older turns had left the conversation by then; ``None`` when the
        target can't say.
    """

    want: str
    committed: Clipped = Clipped()
    written: Clipped | None = None
    supported: bool = True
    error: str | None = None
    compacted: bool | None = None


class PageTurn(Frozen):
    """One turn of the try as the page showed it, at the same place in :attr:`PageRecord.turns`
    as in :attr:`TryEvidence.turns`. What it showed is :attr:`TurnRecord.shown`.

    :param shown_at: Seconds from the try's first message when the reply appeared; ``None`` when it
        never did.
    :param instead: What the page's replies showed when the reply didn't appear (a placeholder, an
        error, an old reply); empty when it did.
    :param shot: The file name of the screenshot taken after the turn, in the run's file store
        (:mod:`evalution.storage.files`); ``None`` when there is none.
    """

    shown_at: float | None = None
    instead: Clipped = Clipped()
    shot: str | None = None
    #: The error status the app answered the page's request that carried the message with: it
    #: refused the message (a 503, say). ``None``: it didn't, or the adapter under the page sent.
    refused: int | None = None
    #: The app didn't answer the page's request that carried the message in the map's
    #: ``send_seconds``, or the request failed on the way: it didn't take the message.
    unanswered: bool = False

    @property
    def not_taken(self) -> bool:
        """The app refused the page's message, or never answered it: the app's, not the page's."""
        return self.refused is not None or self.unanswered


class ConsoleLine(Frozen):
    """An error or a warning the page wrote to its console, scrubbed of secrets.

    :param level: ``error`` or ``warning``.
    :param at: Seconds from the try's first message.
    """

    level: str
    text: Clipped
    at: float


class FailedRequest(Frozen):
    """A request from the page to the copy's own address that failed.

    :param path: Its path, without the query, where tokens ride.
    :param status: What the copy answered (400 and up); ``None``: no answer came.
    :param at: Seconds from the try's first message.
    :param message: It carried the person's message: the app's answer to it is its turn's
        (:attr:`PageTurn.refused`), not the page's health.
    """

    method: str
    path: str
    status: int | None = None
    at: float
    message: bool = False


class PageRecord(Frozen):
    """What the app's page showed of a try played through it (ADR 0010). A try played through the
    page always has one, however little it holds; one played through the API alone has none.

    :param files: The try's screenshots and video, by name in the run's file store.
    :param video_missing: Why there is no video when one was to be kept, e.g. the try was cut off
        before the browser could finish it.
    :param video_policy: When the try's video is kept, as the page map said while it played:
        ``failed`` (a passed try's is deleted once it is judged, :data:`VIDEO_ON_FAILURE`) or
        ``always``; ``None`` when none was recorded.
    :param interrupted: The try was cut short (a restart, a stop) before the page was closed.
    :param left_out: Console lines and failed requests past :data:`PAGE_LINES` each, not kept.
    :param page_only: The target was reached through its page alone, with no API adapter: each
        turn's reply is what the page showed, and nothing else saw it (ADR 0010, amended).
    """

    turns: tuple[PageTurn, ...] = ()
    console: tuple[ConsoleLine, ...] = ()
    failed_requests: tuple[FailedRequest, ...] = ()
    files: tuple[str, ...] = ()
    video_missing: str | None = None
    video_policy: str | None = None
    interrupted: bool = False
    left_out: int = 0
    page_only: bool = False

    def capped(self) -> PageRecord:
        """As stored: every text within :data:`ITEM_BYTES`, and at most :data:`PAGE_LINES` console
        lines and failed requests, the first ones."""
        dropped = max(len(self.console) - PAGE_LINES, 0)
        dropped += max(len(self.failed_requests) - PAGE_LINES, 0)
        return self.model_copy(
            update={
                "turns": tuple(
                    t.model_copy(update={"instead": t.instead.capped()}) for t in self.turns
                ),
                "console": tuple(
                    c.model_copy(update={"text": c.text.capped()})
                    for c in self.console[:PAGE_LINES]
                ),
                "failed_requests": self.failed_requests[:PAGE_LINES],
                "left_out": self.left_out + dropped,
            }
        )


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
    memory: MemoryRecord | None = None
    #: Where the person started a new conversation: how many turns had been played by then.
    breaks: tuple[int, ...] = ()
    #: Every AI span of the try, which a claim may cite.
    spans: tuple[str, ...] = ()
    trace: TraceStatus = TraceStatus.NO_SOURCE
    #: Why the trace read fell short, when a source said.
    trace_note: str | None = None
    failure: Failure | None = None
    error: str | None = None
    #: What the app's own page showed, when the try played through it (ADR 0010); ``None`` for a
    #: try played through the API alone.
    page: PageRecord | None = None

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
                    t.model_copy(
                        update={
                            "message": t.message.capped(),
                            "reply": t.reply.capped(),
                            "shown": t.shown.capped() if t.shown is not None else None,
                        }
                    )
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
                "page": self.page.capped() if self.page is not None else None,
            }
        )
