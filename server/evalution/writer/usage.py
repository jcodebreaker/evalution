"""What one writer turn took, kept on its session so a learn can be measured: time, model calls,
characters sent and received, and the provider's token counts, cache included, when it gives
them. Never money: what a token costs is the provider's business, and changes."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from evalution.llm import Reply

#: Turns kept on a session, newest last.
TURNS_KEPT = 20
#: A learn's stages, in the order they are shown: the code map and the skeleton (no model), the
#: first overview (one call from the skeleton, when the learn has most of the app to learn), Read
#: (file summaries: only the fallback's, for files no grammar reads), the folder and app summaries
#: (none since ADR 0012; kept so older learns still show), the areas, the overview, and the rest
#: (checking whether anything moved, the traces, the docs' summary, the merge and its save).
STAGES = (
    "code map",
    "skeleton",
    "first overview",
    "read",
    "modules",
    "areas",
    "overview",
    "other",
)
#: What a stage counts.
STAGE_COUNTS = ("calls", "tokens_in", "tokens_out", "chars_in", "chars_out")


def _order(stage: dict[str, Any]) -> int:
    name = stage["stage"]
    return STAGES.index(name) if name in STAGES else len(STAGES)


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
    #: Of ``tokens_in`` and ``tokens_out``, the light model's: the rest is the strong one's.
    light_tokens_in: int = 0
    light_tokens_out: int = 0
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
    #: How many files the light model was asked to read (only the fallback's since ADR 0012:
    #: files no grammar reads, in an area about to be learned), and how many facts the code map
    #: read off the code with no model.
    slice_files: int = 0
    static_facts: int = 0
    #: A learn's skeleton: its entries, and the trace hits matched to them.
    skeleton_entries: int = 0
    skeleton_hits: int = 0
    #: A learn's areas: how many the code was split into, how many were understood again, how
    #: many were listed and not learned (no weight), the names no section said, and the
    #: overview's calls: the first one's (from the skeleton, early) and the final one's.
    areas_total: int = 0
    areas_run: int = 0
    areas_listed: int = 0
    uncovered: int = 0
    first_overview_calls: int = 0
    overview_calls: int = 0
    #: The tools called, in order.
    tools: list[str] = field(default_factory=list)
    #: A learn's areas: each run's area, outcome, calls, tokens and seconds.
    areas: list[dict[str, Any]] = field(default_factory=list)
    #: A learn's stages (:data:`STAGES`), each with its seconds and its share of the calls,
    #: tokens and characters above: a call counts for the stage running when it is made.
    stages: list[dict[str, Any]] = field(default_factory=list)
    _started: float = field(default_factory=time.monotonic, repr=False)
    _stage: dict[str, Any] | None = field(default=None, repr=False)
    _stage_at: float = field(default=0.0, repr=False)

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
            self.light_tokens_in += reply.tokens_in
            self.light_tokens_out += reply.tokens_out
        self.tokens_in += reply.tokens_in
        self.tokens_out += reply.tokens_out
        self.cache_read += reply.cache_read
        self.cache_write += reply.cache_write
        if self._stage is not None:
            for key, value in zip(
                STAGE_COUNTS, (1, reply.tokens_in, reply.tokens_out, sent, out), strict=True
            ):
                self._stage[key] += value

    def stage(self, name: str) -> None:
        """A learn's stage *name* starts: the time and the calls from now on are its, until the
        next one or the end of the turn. A stage met again adds to what it had."""
        self._close()
        found = next((s for s in self.stages if s["stage"] == name), None)
        if found is None:
            found = {"stage": name, "seconds": 0.0, **dict.fromkeys(STAGE_COUNTS, 0)}
            self.stages.append(found)
            self.stages.sort(key=_order)
        self._stage = found
        self._stage_at = time.monotonic()

    def _close(self) -> None:
        if self._stage is not None:
            self._stage["seconds"] += time.monotonic() - self._stage_at
            self._stage = None

    def step(self, tool: str) -> None:
        """A tool call, in order: what each strong call was for."""
        self.tools.append(tool)

    def note(self, stats: dict[str, float]) -> None:
        """What a learn's start says it did, beside the calls."""
        for key, value in stats.items():
            setattr(self, key, type(getattr(self, key))(value))

    def record(self, session: dict[str, Any]) -> None:
        """Put this turn on *session*, with how long it took: a stage still running (a learn
        cut by its time limit) ends here."""
        self._close()
        found = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        for stage in found["stages"]:
            stage["seconds"] = round(stage["seconds"], 2)
        found["seconds"] = round(time.monotonic() - self._started, 2)
        kept = session.setdefault("usage", [])
        kept.append(found)
        del kept[:-TURNS_KEPT]


def last_learn(turns: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The newest of *turns* that was a learn measured by stage and did some work: one that found
    nothing changed is passed over, as is one from before stages were measured."""
    learns = [
        t
        for t in turns
        if t.get("learn")
        and t.get("stages")
        and (t.get("calls") or any(s.get("stage") != "other" for s in t["stages"]))
    ]
    return max(learns, key=lambda t: float(t.get("at") or 0), default=None)
