"""A run's evidence: every try's :class:`TryEvidence`, one entry each under the run's id
(kind :data:`EVIDENCE`). It holds prompts and tool outputs. Read only by what regrades a try, what
checks a claim and what shows or looks into a try's traces, through
:meth:`evalution.results.store.RunStore.evidence`; the list views never touch it.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from evalution.core.evidence import TryEvidence
from evalution.storage import Docs

_logger = logging.getLogger(__name__)
EVIDENCE = "evidence"
#: What :func:`look_into` shows of each place: how many, and how much text around the match.
_HITS = 30
_AROUND = 160


def keep(docs: Docs, run: str, evidence: TryEvidence) -> None:
    """Add one try, as soon as it was played, so a run cut short keeps what it played."""
    docs.append(EVIDENCE, run, evidence.capped().model_dump(mode="json"))


def read(docs: Docs, run: str) -> Iterator[TryEvidence]:
    """Every try kept for *run*, in the order played; one that no longer reads is left out and
    logged."""
    for entry in docs.entries(EVIDENCE, run):
        try:
            yield TryEvidence.model_validate(entry.body)
        except ValidationError:
            _logger.warning("evals: a try kept for run %s doesn't read", run)


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
