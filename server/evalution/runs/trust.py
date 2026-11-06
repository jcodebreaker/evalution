"""Labelled examples into the record (docs/judging.md, "When a person disagrees"). A person agrees
or disagrees with the grader on one rubric line of one try; the example is kept
(:mod:`evalution.judging.labels`), and the runs of its set are read again, so a line the grader
turns out unreliable on stops passing tries, and one it is trusted on again gives their passes back
(:func:`evalution.results.record.distrust`). Offline: no model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from evalution.core.judgement import CheckKind, CheckResult
from evalution.core.rules import current, line_key
from evalution.judging import labels
from evalution.judging.agreement import Agreement, by_line, grader_said, unreliable_lines
from evalution.results.record import distrust
from evalution.results.store import RunStore
from evalution.results.views import totals
from evalution.storage import Docs


def _line(check: CheckResult) -> bool:
    return check.kind is CheckKind.RUBRIC and check.id != "rubric"


def try_of(doc: Mapping[str, Any] | None, case: str, n: int) -> Mapping[str, Any]:
    """Try *n* of test *case* in the run *doc*.

    :raises LookupError: No such run, test or try.
    """
    entry = (doc or {}).get("cases", {}).get(case) or {}
    found = next((t for t in entry.get("tries") or [] if t["n"] == n), None)
    if found is None:
        raise LookupError(f"{case} try {n}")
    return found


def said_on(checks: Iterable[CheckResult], line: str) -> bool | None:
    """What the grader says, among *checks*, of the rubric line whose key is *line*."""
    return grader_said(next((c for c in checks if _line(c) and line_key(c.why) == line), None))


def _example(store: RunStore, run: str, case: str, n: int, index: int) -> tuple[dict, dict, bool]:
    """The run, the example's identity on line *index* of try *n*, and what the grader says of it.

    :raises LookupError: No such run, test, try or line.
    :raises ValueError: The grader didn't decide that line on that try.
    """
    doc = store.load(run)
    attempt = try_of(doc, case, n)
    assert doc is not None
    found = current(attempt)
    check = next((c for c in found.checks if c.id == f"rubric:{index}"), None)
    if check is None:
        raise LookupError(f"{case} try {n} line {index}")
    said = grader_said(check)
    if said is None:
        raise ValueError("the grader didn't decide this line on this try")
    example = {
        "set": doc["set"],
        "case": case,
        "line": line_key(check.why),
        "text": check.why,
        "run": run,
        "try": n,
        "check": check.id,
        "said": said,
        "graded": said,
        "judge": labels.grader_of(found.judge),
    }
    return doc, example, said


def label(
    store: RunStore, docs: Docs, run: str, case: str, n: int, index: int, agree: bool
) -> dict[str, Any]:
    """Keep the person's answer on line *index* of try *n*: they *agree* with what the grader says
    of it there, or not. The line's agreement after it.

    :raises LookupError: No such run, test, try or line.
    :raises ValueError: The grader didn't decide that line on that try.
    """
    doc, example, said = _example(store, run, case, n, index)
    labels.put(docs, doc["target"], {**example, "holds": said if agree else not said})
    apply_stored(store, docs, doc["target"], doc["set"])
    return _line_view(docs, doc, case, n, example["text"])


def unlabel(store: RunStore, docs: Docs, run: str, case: str, n: int, index: int) -> dict[str, Any]:
    """Forget the person's answer on line *index* of try *n*. The line's agreement after it.

    :raises LookupError: No such run, test, try or line, or no answer on it.
    :raises ValueError: The grader didn't decide that line on that try.
    """
    doc, example, _ = _example(store, run, case, n, index)
    if not labels.clear(docs, doc["target"], example):
        raise LookupError("no answer on this line")
    apply_stored(store, docs, doc["target"], doc["set"])
    return _line_view(docs, doc, case, n, example["text"])


def apply(docs: Docs, doc: dict[str, Any]) -> int:
    """Read *doc* again with what its set's labelled examples say of its grader now."""
    return distrust(doc, unreliable_lines(labels.of(docs, doc["target"], doc["set"])))


def apply_stored(
    store: RunStore, docs: Docs, target: str, set_name: str, skip: str | None = None
) -> list[str]:
    """:func:`apply` to every stored run of the set but a going one, which is read again as it ends,
    and *skip*, which the caller holds; the runs changed."""
    lines = unreliable_lines(labels.of(docs, target, set_name))
    changed = []
    for run in store.all(target, set_name):
        if run.get("status") == "running" or run["run"] == skip:
            continue
        if distrust(run, lines):
            run["totals"] = totals(run)
            store.write(run)
            changed.append(run["run"])
    return changed


def lines_view(
    docs: Docs, doc: Mapping[str, Any], case: str, texts: Sequence[str], n: int | None = None
) -> list[dict[str, Any]]:
    """The agreement on each of *texts*, rubric lines of test *case*. With try *n*: ``mine``, the
    person agrees with what the grader says there now (``None``: no answer), and ``can``, whether
    the grader decided the line there, so it can be answered."""
    examples = [e for e in labels.of(docs, doc["target"], doc["set"]) if e["case"] == case]
    agreed = by_line(examples)
    checks: list[CheckResult] = []
    if n is not None:
        checks = list(current(try_of(doc, case, n)).checks)
    rows = []
    for text in texts:
        key = line_key(text)
        row = agreed.get((case, key), Agreement()).view()
        if n is not None:
            said = said_on(checks, key)
            mine = next(
                (e for e in examples if (e["line"], e["run"], e["try"]) == (key, doc["run"], n)),
                None,
            )
            row["mine"] = None if mine is None or said is None else mine["holds"] == said
            row["can"] = said is not None
        rows.append(row)
    return rows


def _line_view(docs: Docs, doc: Mapping[str, Any], case: str, n: int, text: str) -> dict[str, Any]:
    return lines_view(docs, doc, case, [text], n)[0]


def case_view(docs: Docs, doc: Mapping[str, Any], case: str, found: dict[str, Any]) -> None:
    """A ``CaseDetail`` with the agreement on each rubric line, and on each try the person's
    answers, a line each (``None``: none)."""
    rubric = list(found.get("reply_rubric") or [])
    found["agreement"] = [
        {"line": text, **row}
        for text, row in zip(rubric, lines_view(docs, doc, case, rubric), strict=True)
    ]
    for attempt in found["tries"]:
        rows = lines_view(docs, doc, case, rubric, attempt["n"])
        attempt["labels"] = [row["mine"] for row in rows]
