"""The sidecar of a run: every try's :class:`TryEvidence`, one JSON line each, gzipped, in
``runs/<run>.evidence.jsonl.gz`` next to the run file. It holds prompts and tool outputs, so it is
owner-only. Read only by what regrades a try and what checks a claim; the views never touch it, and
:meth:`evalution.results.store.FileStore.all` doesn't see it.
"""

from __future__ import annotations

import gzip
import logging
import os
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from evalution.core.evidence import TryEvidence

_logger = logging.getLogger(__name__)
SUFFIX = ".evidence.jsonl.gz"


def path_of(runs: Path, run: str) -> Path:
    return runs / f"{run}{SUFFIX}"


def append(runs: Path, run: str, evidence: TryEvidence) -> None:
    """Add one try. Each try is its own gzip member, so a run cut short keeps what it played."""
    runs.mkdir(parents=True, exist_ok=True)
    line = evidence.capped().model_dump_json() + "\n"
    descriptor = os.open(path_of(runs, run), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as raw:
        raw.write(gzip.compress(line.encode()))


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
