"""Labelled examples into the record (docs/judging.md, "When a person disagrees"). A person agrees
or disagrees with the grader on one rubric line of one try, or on one question about how the page
looks (``look:<i>``, a vision model's answer); the example is kept
(:mod:`evalution.judging.labels`), and the runs of its set are read again, so a line the grader
turns out unreliable on stops passing tries, and one it is trusted on again gives their passes back
(:func:`evalution.results.record.distrust`), through each run's lock in its
:class:`~evalution.results.store.RunStore`. Offline: no model.
"""

from __future__ import annotations

import asyncio
import re
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


#: A question about how the page looks, as the try's check names it.
LOOK_ID = re.compile(r"look:(\d+)")


def look_questions(doc: Mapping[str, Any], case: str) -> list[str]:
    """The questions test *case* asked of its screenshots (``page.looks``) when *doc* played it,
    by place: ``look:<i>`` is the *i*-th."""
    raw = ((doc.get("cases") or {}).get(case) or {}).get("case") or {}
    page = raw.get("page") if isinstance(raw, dict) else None
    looks = page.get("looks") if isinstance(page, dict) else None
    return [
        " ".join(str(look.get("question") or "").split()) if isinstance(look, dict) else ""
        for look in looks or []
    ]


def look_key(question: str) -> str:
    """A look question's identity across runs, apart from any rubric line of the same words."""
    return line_key(f"look: {question}")


def _example(
    store: RunStore, run: str, case: str, n: int, index: int | str
) -> tuple[dict, dict, bool]:
    """The run, the example's identity on line *index* of try *n* (a rubric line's place, or a
    look's id, ``look:<i>``), and what the grader says of it.

    :raises LookupError: No such run, test, try or line.
    :raises ValueError: The grader didn't decide that line on that try.
    """
    doc = store.load(run)
    attempt = try_of(doc, case, n)
    assert doc is not None
    found = current(attempt)
    wanted = index if isinstance(index, str) else f"rubric:{index}"
    check = next((c for c in found.checks if c.id == wanted), None)
    if check is None:
        raise LookupError(f"{case} try {n} line {index}")
    said = grader_said(check)
    if said is None:
        raise ValueError("the grader didn't decide this line on this try")
    if check.kind is CheckKind.PAGE_LOOK:
        match = LOOK_ID.fullmatch(check.id)
        questions = look_questions(doc, case)
        at = int(match.group(1)) if match else -1
        text = questions[at] if 0 <= at < len(questions) and questions[at] else check.id
        key = look_key(text)
    else:
        text, key = check.why, line_key(check.why)
    example = {
        "set": doc["set"],
        "case": case,
        "line": key,
        "text": text,
        "run": run,
        "try": n,
        "check": check.id,
        "said": said,
        "graded": said,
        "judge": labels.grader_of(found.judge),
    }
    return doc, example, said


async def label(
    store: RunStore, docs: Docs, run: str, case: str, n: int, index: int | str, agree: bool
) -> dict[str, Any]:
    """Keep the person's answer on line *index* of try *n*: they *agree* with what the grader says
    of it there, or not. The line's agreement after it.

    :raises LookupError: No such run, test, try or line.
    :raises ValueError: The grader didn't decide that line on that try.
    """
    doc, example, said = await asyncio.to_thread(_example, store, run, case, n, index)
    held = {**example, "holds": said if agree else not said}
    await asyncio.to_thread(labels.put, docs, doc["target"], held)
    await apply_stored(store, docs, doc["target"], doc["set"])
    return await asyncio.to_thread(_view_of, docs, doc, case, n, example)


async def unlabel(
    store: RunStore, docs: Docs, run: str, case: str, n: int, index: int | str
) -> dict[str, Any]:
    """Forget the person's answer on line *index* of try *n*. The line's agreement after it.

    :raises LookupError: No such run, test, try or line, or no answer on it.
    :raises ValueError: The grader didn't decide that line on that try.
    """
    doc, example, _ = await asyncio.to_thread(_example, store, run, case, n, index)
    if not await asyncio.to_thread(labels.clear, docs, doc["target"], example):
        raise LookupError("no answer on this line")
    await apply_stored(store, docs, doc["target"], doc["set"])
    return await asyncio.to_thread(_view_of, docs, doc, case, n, example)


def apply(docs: Docs, doc: dict[str, Any]) -> int:
    """Read *doc* again with what its set's labelled examples say of its grader now."""
    return distrust(doc, unreliable_lines(labels.of(docs, doc["target"], doc["set"])))


def settle(docs: Docs, doc: dict[str, Any]) -> bool:
    """:func:`apply`, with *doc*'s totals counted again when it changed it: what a run store does
    before each save, so a run held in memory while a label changed can't write over it."""
    if not apply(docs, doc):
        return False
    doc["totals"] = totals(doc)
    return True


async def apply_stored(store: RunStore, docs: Docs, target: str, set_name: str) -> list[str]:
    """:func:`settle` every stored run of the set, each under its run's lock; the runs changed. One
    held in memory is settled again as it is saved."""
    ids = [r["run"] for r in await asyncio.to_thread(store.all, target, set_name)]
    return [run for run in ids if await store.update(run, lambda doc: settle(docs, doc))]


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


def _view_of(
    docs: Docs, doc: Mapping[str, Any], case: str, n: int, example: Mapping[str, Any]
) -> dict[str, Any]:
    """The agreement on the line *example* labels, after it: a look's or a rubric line's."""
    if str(example.get("check", "")).startswith("look:"):
        return looks_view(docs, doc, case, n)[example["check"]]
    return _line_view(docs, doc, case, n, example["text"])


def looks_view(docs: Docs, doc: Mapping[str, Any], case: str, n: int) -> dict[str, dict[str, Any]]:
    """The agreement on each question test *case* asks of its screenshots, by its check's id
    (``look:<i>``), on try *n*: as :func:`lines_view` says a rubric line's, with ``mine`` and
    ``can``."""
    examples = [e for e in labels.of(docs, doc["target"], doc["set"]) if e["case"] == case]
    agreed = by_line(examples)
    checks = {c.id: c for c in current(try_of(doc, case, n)).checks}
    rows: dict[str, dict[str, Any]] = {}
    for i, question in enumerate(look_questions(doc, case)):
        id_ = f"look:{i}"
        key = look_key(question or id_)
        row = agreed.get((case, key), Agreement()).view()
        said = grader_said(checks.get(id_))
        mine = next(
            (e for e in examples if (e["line"], e["run"], e["try"]) == (key, doc["run"], n)),
            None,
        )
        row["mine"] = None if mine is None or said is None else mine["holds"] == said
        row["can"] = said is not None
        rows[id_] = row
    return rows


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
