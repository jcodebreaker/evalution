"""The sidecar of a run: every try's :class:`TryEvidence`, one JSON line each, gzipped, in
``runs/<run>.evidence.jsonl.gz`` next to the run file. It holds prompts and tool outputs, so it is
owner-only. Read only by what regrades a try, what checks a claim and what shows or looks into a
try's traces; the list views never touch it, and
:meth:`evalution.results.store.FileStore.all` doesn't see it.
"""

from __future__ import annotations

import gzip
import logging
import os
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evalution.core.evidence import TryEvidence

_logger = logging.getLogger(__name__)
SUFFIX = ".evidence.jsonl.gz"
#: What :func:`look_into` shows of each place: how many, and how much text around the match.
_HITS = 30
_AROUND = 160


def path_of(runs: Path, run: str) -> Path:
    return runs / f"{run}{SUFFIX}"


def append(runs: Path, run: str, evidence: TryEvidence) -> None:
    """Add one try. Each try is its own gzip member, so a run cut short keeps what it played."""
    runs.mkdir(parents=True, exist_ok=True)
    line = evidence.capped().model_dump_json() + "\n"
    descriptor = os.open(path_of(runs, run), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as raw:
        raw.write(gzip.compress(line.encode()))


def look_into(kept: TryEvidence, find: str | None = None) -> dict[str, Any]:
    """A try's traces at a glance: how many calls of what, and, with *find*, where it appears:
    in what the person said, what came back, what a model was sent or said, or a tool's result."""
    found: dict[str, Any] = {
        "messages": len(kept.turns),
        "seconds": kept.seconds,
        "models": dict(Counter(call.model for call in kept.model_calls)),
        "tools": dict(Counter(tool.name for tool in kept.tools).most_common(20)),
        "trace": kept.trace.value,
    }
    if not find:
        return found
    wanted = find.casefold()
    places: list[tuple[str, float, str]] = []
    for turn in kept.turns:
        at = turn.started - kept.t0
        places += [("person", at, turn.message.text), ("reply", at, turn.reply.text)]
    for call in kept.model_calls:
        places += [("model sent", call.t, call.sent.text), ("model said", call.t, call.said.text)]
    for tool in kept.tools:
        places.append((f"tool {tool.name}", tool.start, tool.result.text))
    hits = []
    for where, at, text in places:
        spot = text.casefold().find(wanted)
        if spot >= 0:
            hits.append(
                {
                    "where": where,
                    "seconds": round(at, 1),
                    "text": text[max(0, spot - _AROUND) : spot + len(find) + _AROUND],
                }
            )
    found["appears"] = len(hits)
    found["first"] = hits[:_HITS]
    return found


def read(runs: Path, run: str) -> Iterator[TryEvidence]:
    """Every try kept for *run*; none when it has no sidecar. A line that no longer reads is left
    out and logged."""
    path = path_of(runs, run)
    if not path.is_file():
        return
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            try:
                yield TryEvidence.model_validate_json(line)
            except ValidationError:
                _logger.warning("evals: a try in %s doesn't read", path)
