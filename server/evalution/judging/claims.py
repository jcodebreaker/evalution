"""What a model says about a run, held to the evidence (docs/judging.md, "Facts and its reading").

A claim names where it looked (``span:ab12``, ``turn:3``, ``state:2``, ``reply``) and quotes what it
read. Code checks that the refs exist and that the quotes occur in the evidence, whitespace aside. A
claim that doesn't check out is kept and marked, not dropped: what the model said is still there to
read, as what it is.

That is all this can show. A wrong reading with valid refs still gets through.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from evalution.core.evidence import TryEvidence
from evalution.core.judgement import Claim, EvidenceRef

CLAIM_CHARS = 400
_REFS = 8
_QUOTES = 3
_QUOTE_CHARS = 200


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class EvidenceIndex:
    """What claims can cite: every ref there is, and every word that was said."""

    refs: frozenset[str]
    text: str

    def has_quote(self, quote: str) -> bool:
        return _plain(quote) in self.text

    def holds(self, refs: Sequence[str], quotes: Sequence[str]) -> bool:
        """Whether a claim that cites these checks out; one that cites nothing doesn't."""
        return bool(refs or quotes) and (
            all(r in self.refs for r in refs) and all(map(self.has_quote, quotes))
        )


NO_EVIDENCE = EvidenceIndex(frozenset(), "")


def _said(evidence: TryEvidence) -> Iterable[str]:
    for turn in evidence.turns:
        yield turn.message.text
        yield turn.reply.text
    for tool in evidence.tools:
        yield tool.args.text
        yield tool.result.text
    for call in evidence.model_calls:
        yield call.sent.text
        yield call.said.text
    for state in evidence.states:
        yield state.actual or ""
        yield state.reason


def _refs(evidence: TryEvidence) -> Iterable[str]:
    yield "reply"
    yield from (f"span:{span}" for span in evidence.spans)
    yield from (f"span:{tool.id}" for tool in evidence.tools)
    yield from (f"span:{call.span}" for call in evidence.model_calls)
    yield from (f"turn:{n}" for n in range(1, len(evidence.turns) + 1))
    yield from (f"state:{state.index}" for state in evidence.states)


def index_of(tries: Iterable[TryEvidence]) -> EvidenceIndex:
    """What these tries can back up."""
    found = list(tries)
    return EvidenceIndex(
        frozenset(ref for e in found for ref in _refs(e)),
        "\n".join(_plain(text) for e in found for text in _said(e)),
    )


def checked_quotes(quotes: Sequence[str], index: EvidenceIndex) -> tuple[EvidenceRef, ...]:
    """A grader's quotes of the reply, each marked with whether it is there."""
    return tuple(EvidenceRef(ref="reply", quote=q, verified=index.has_quote(q)) for q in quotes)


def _strings(value: Any, limit: int, chars: int = _QUOTE_CHARS) -> tuple[str, ...]:
    found = value if isinstance(value, list) else []
    return tuple(v.strip()[:chars] for v in found if isinstance(v, str) and v.strip())[:limit]


def read_claim(value: Any, index: EvidenceIndex) -> Claim:
    """A claim as a model wrote it: ``{"text", "refs", "quotes"}``, or bare text, citing nothing.

    :raises ValueError: No text.
    """
    fields: Mapping[str, Any] = value if isinstance(value, dict) else {"text": value}
    text = fields.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a claim needs text")
    refs = _strings(fields.get("refs"), _REFS)
    quotes = _strings(fields.get("quotes"), _QUOTES)
    return Claim(
        text=" ".join(text.split())[:CLAIM_CHARS],
        refs=refs,
        quotes=quotes,
        verified=index.holds(refs, quotes),
    )
