"""What one writer turn took, kept on its session so a learn can be measured: time, model calls,
characters sent and received, and the provider's token counts, cache included, when it gives
them. Never money: what a token costs is the provider's business, and changes."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from evalution.llm import Reply

#: Turns kept on a session, newest last.
TURNS_KEPT = 20


@dataclass
class TurnUsage:
    """One admin message and the work it set off. *learn*: it was a learn. The ``light_`` fields
    count a learn's file summaries apart; the others count every call, those too."""

    learn: bool = False
    at: float = field(default_factory=time.time)
    calls: int = 0
    chars_in: int = 0
    chars_out: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    light_calls: int = 0
    light_chars_in: int = 0
    light_chars_out: int = 0
    #: A learn's code map: how long making it took, the files parsed for it (the rest were kept,
    #: parsed by whoever made the map first: a learn, walk_code or the change card), its
    #: definitions and references, how much of it a full learn sent (the map and the key limits),
    #: and how much of the touched definitions a patch sent.
    map_seconds: float = 0.0
    map_files_parsed: int = 0
    map_defs: int = 0
    map_refs: int = 0
    map_chars: int = 0
    patch_chars: int = 0
    _started: float = field(default_factory=time.monotonic, repr=False)

    def add(self, sent: int, reply: Reply, light: bool = False) -> None:
        """One model call: *sent*, the characters of its request (instructions, transcript and
        tool definitions), and what came back."""
        out = len(reply.text) + sum(
            len(c["name"]) + len(str(c["arguments"])) for c in reply.tool_calls
        )
        self.calls += 1
        self.chars_in += sent
        self.chars_out += out
        if light:
            self.light_calls += 1
            self.light_chars_in += sent
            self.light_chars_out += out
        self.tokens_in += reply.tokens_in
        self.tokens_out += reply.tokens_out
        self.cache_read += reply.cache_read
        self.cache_write += reply.cache_write

    def note(self, stats: dict[str, float]) -> None:
        """What a learn's start says it did, beside the calls."""
        for key, value in stats.items():
            setattr(self, key, type(getattr(self, key))(value))

    def record(self, session: dict[str, Any]) -> None:
        """Put this turn on *session*, with how long it took."""
        found = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        found["seconds"] = round(time.monotonic() - self._started, 2)
        kept = session.setdefault("usage", [])
        kept.append(found)
        del kept[:-TURNS_KEPT]
