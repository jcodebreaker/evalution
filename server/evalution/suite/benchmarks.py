"""Ready-made benchmarks: a public golden dataset turned into a set of ordinary tests.

LongMemEval (Wu et al., ICLR 2025, https://github.com/xiaowu0162/LongMemEval, MIT) asks questions
with known answers about a person's chat history. :func:`install` writes its questions as cases
under ``sets/<target>/longmemeval-<size>/``, in the usual case format, so they play, are graded and
are stored like any test. The data stays out of this repository: it is fetched into
``<data_dir>/benchmarks/`` the first time it's needed.

**How a question plays.** The person's recorded messages are sent one by one, so the agent's own way
of saving things is what gets tested, then a new conversation asks the question. The agent's replies
aren't the recorded ones, so questions about what the assistant said can't be played and are left
out. Each history's date goes in its first message, since a turn carries no timestamp.

**How far a fact got.** Before that new conversation the app's memory is read, for adapters that can
show it, and the Grader says whether it holds the golden answer. It only measures: it never decides
the question.

**How a question is judged.** One rubric line, holding the golden answer and the same allowances as
LongMemEval's own judge prompts (``src/evaluation/evaluate_qa.py``), for each kind of question.

:func:`report` works out LongMemEval's numbers from a stored run.
"""

from __future__ import annotations

import json
import random
import re
import statistics
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx

from evalution.core.judgement import VerdictKind
from evalution.core.rules import line_reason
from evalution.results.record import verdict_of
from evalution.suite.cases import parse_case

NAME = "longmemeval"
_BASE = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/"
#: Each size: the file it comes from, and how many questions of each kind a sample takes.
SIZES: dict[str, tuple[str, int | None]] = {
    "quick": ("longmemeval_oracle.json", 10),
    "oracle": ("longmemeval_oracle.json", None),
    "s": ("longmemeval_s_cleaned.json", None),
    "m": ("longmemeval_m_cleaned.json", None),
}
#: What each size is, as a person chooses between them.
ABOUT = {
    "quick": "A fixed sample with short histories: 10 of each kind of question",
    "oracle": "Every question that can play, with short histories",
    "s": "The same questions among unrelated conversations, about 40 per question",
    "m": "The same questions among about 500 conversations each",
}
#: Needs the recorded replies, which a live agent won't repeat.
_CANNOT_PLAY = "single-session-assistant"
_SOURCE = re.compile(rf"^{NAME}:([a-z-]+)(:unanswerable)?$")
_SAMPLE_SEED = 0
_REPLY_CHARS = 4000
_RUNS_SHOWN = 20
#: Fewer answers than this, or facts measured, and a sentence about them would claim too much.
_TOO_FEW = 5
#: A try's time limit: each message gets this long on average, and never less than the floor.
_SECONDS_PER_MESSAGE = 60
_MINIMUM_SECONDS = 300

_SAME = "It counts if it says the same thing or shows every step to get there, not if it has part."
_LINES = {
    "single-session-user": "The reply contains the correct answer: {answer}. " + _SAME,
    "multi-session": "The reply contains the correct answer: {answer}. " + _SAME,
    "temporal-reasoning": (
        "The reply contains the correct answer: {answer}. "
        + _SAME
        + " Being off by one in a number of days, weeks or months still counts."
    ),
    "knowledge-update": (
        "The reply contains the correct answer: {answer}. It still counts if it also mentions "
        "earlier information, as long as the updated answer is the one it gives."
    ),
    "single-session-preference": (
        "The reply recalls and uses the person's own information correctly, though not "
        "necessarily every point of this: {answer}"
    ),
}
_UNANSWERABLE = (
    "The reply says it doesn't know, or that the information is missing or incomplete, rather "
    "than answering as if it knew. Why it can't be answered: {answer}"
)


def fetch(file: str, data_dir: Path) -> Path:
    """The dataset file in ``<data_dir>/benchmarks/longmemeval/``, downloaded if it isn't there."""
    path = data_dir / "benchmarks" / NAME / file
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with httpx.stream("GET", _BASE + file, follow_redirects=True, timeout=120) as answer:
        answer.raise_for_status()
        with partial.open("wb") as out:
            for chunk in answer.iter_bytes():
                out.write(chunk)
    partial.replace(path)
    return path


def _unanswerable(entry: Mapping[str, Any]) -> bool:
    return "_abs" in entry["question_id"]


def sample(entries: Iterable[Mapping[str, Any]], each: int | None) -> list[Mapping[str, Any]]:
    """What can be played; with *each*, that many of every kind and of the unanswerable ones. The
    same questions every time, so runs compare."""
    playable = [e for e in entries if e["question_type"] != _CANNOT_PLAY]
    if each is None:
        return playable
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for entry in sorted(playable, key=lambda e: e["question_id"]):
        kind = "unanswerable" if _unanswerable(entry) else entry["question_type"]
        groups.setdefault(kind, []).append(entry)
    picker = random.Random(_SAMPLE_SEED)
    return [
        chosen
        for _, group in sorted(groups.items())
        for chosen in picker.sample(group, min(each, len(group)))
    ]


def to_case(entry: Mapping[str, Any], boundary: str = "break") -> dict[str, Any]:
    """One question as a case file's JSON. *boundary* is how one dated session gives way to the
    next: ``break``, a new conversation; ``same``, on in one conversation, for an app that has only
    one per person."""
    parted = boundary == "break"
    kind, answer = entry["question_type"], entry["answer"]
    turns: list[dict[str, Any]] = []
    for date, session in zip(entry["haystack_dates"], entry["haystack_sessions"], strict=True):
        said = [turn["content"] for turn in session if turn["role"] == "user"]
        if not said:
            continue
        if turns and parted:
            turns.append({"break": "conversation"})
        turns.append({"message": f"[This conversation is on {date}]\n{said[0]}"})
        turns.extend({"message": text} for text in said[1:])
    messages = sum("message" in turn for turn in turns) + 1
    unanswerable = _unanswerable(entry)
    if not unanswerable:
        turns.append({"memory_holds": str(answer)})
    if turns and parted:
        turns.append({"break": "conversation"})
    turns.append({"message": f"[Today is {entry['question_date']}]\n{entry['question']}"})
    turns[0]["fresh"] = True
    line = (_UNANSWERABLE if unanswerable else _LINES[kind]).format(answer=answer)
    question = " ".join(str(entry["question"]).split())
    return {
        "name": question[:70],
        "about": f"LongMemEval {entry['question_id']}.",
        "source": f"{NAME}:{kind}" + (":unanswerable" if unanswerable else ""),
        "needs_clean": ["memory"],
        "turns": turns,
        "reply": {"rubric": [line]},
        "limits": {
            "tries": 1,
            "seconds": max(_MINIMUM_SECONDS, messages * _SECONDS_PER_MESSAGE),
        },
    }


def _chosen(size: str, each: int | None, data_dir: Path) -> list[Mapping[str, Any]]:
    file, default = SIZES[size]
    return sample(json.loads(fetch(file, data_dir).read_text()), each or default)


def size_of(size: str, each: int | None, data_dir: Path) -> tuple[int, int]:
    """How many questions a choice has, and how many messages they send."""
    chosen = _chosen(size, each, data_dir)
    sent = sum(sum("message" in turn for turn in to_case(entry)["turns"]) for entry in chosen)
    return len(chosen), sent


def install(
    size: str,
    target: str,
    sets_dir: Path,
    data_dir: Path,
    *,
    boundary: str = "break",
    each: int | None = None,
    plan: Mapping[str, Any] | None = None,
) -> tuple[str, int]:
    """Write the benchmark's *size* as a set of *target*, *plan* beside it if a plan chose it. Its
    name, which says how it plays, and how many questions it has."""
    slug = f"{NAME}-{size}" + ("-same" if boundary == "same" else "")
    folder = sets_dir / target / slug
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.case.json"):
        old.unlink()
    chosen = _chosen(size, each, data_dir)
    if plan is not None:
        (folder / "plan.json").write_text(json.dumps(plan, indent=1))
    for entry in chosen:
        case = to_case(entry, boundary)
        stem = re.sub(r"[^a-z0-9_-]", "-", str(entry["question_id"]).lower())
        parse_case(case, f"{slug}/{stem}")
        (folder / f"{stem}.case.json").write_text(json.dumps(case, indent=1))
    about = {
        "name": f"LongMemEval ({size})",
        "about": "Memory benchmark: questions with known answers about a person's chat history.",
        "grader": "light",
    }
    (folder / "set.json").write_text(json.dumps(about, indent=1))
    return slug, len(chosen)


def _memory(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    return ((entry.get("tries") or [{}])[-1]).get("memory")


def report(run: Mapping[str, Any]) -> dict[str, Any]:
    """LongMemEval's numbers for a stored run: right / judged, per kind of question; the mean of the
    kinds; how many known answers reached committed memory; and, apart, how often the agent admitted
    what it couldn't know."""
    kinds: dict[str, list[int]] = {}
    admitted = [0, 0]
    unjudged = waiting = 0
    saved = {
        "measured": 0,
        "written": 0,
        "committed": 0,
        "used": 0,
        "reach_measured": 0,
        "reached": 0,
        "compact_measured": 0,
        "compacted": 0,
    }
    for entry in (run.get("cases") or {}).values():
        found = _SOURCE.match(str(entry.get("source") or ""))
        if not found:
            continue
        said = verdict_of(entry).kind
        if said is VerdictKind.NOT_RUN:
            waiting += 1
            continue
        if said is VerdictKind.UNJUDGED:
            unjudged += 1
            continue
        tally = admitted if found[2] else kinds.setdefault(found[1], [0, 0])
        tally[0] += said is VerdictKind.PASS
        tally[1] += 1
        memory = _memory(entry)
        if found[2] or not memory:
            continue
        if memory.get("committed") is not None and memory.get("written") is not None:
            saved["measured"] += 1
            saved["written"] += memory["written"]
            saved["committed"] += memory["committed"]
            saved["used"] += said is VerdictKind.PASS
        if memory.get("reached") is not None:
            saved["reach_measured"] += 1
            saved["reached"] += memory["reached"]
        if memory.get("compacted") is not None:
            saved["compact_measured"] += 1
            saved["compacted"] += memory["compacted"]
    right = sum(tally[0] for tally in kinds.values())
    judged = sum(tally[1] for tally in kinds.values())
    return {
        "accuracy": right / judged if judged else None,
        "right": right,
        "judged": judged,
        "task_average": (
            sum(tally[0] / tally[1] for tally in kinds.values()) / len(kinds) if kinds else None
        ),
        "kinds": {k: {"right": t[0], "judged": t[1]} for k, t in sorted(kinds.items())},
        "write_recall": saved["committed"] / saved["measured"] if saved["measured"] else None,
        "reached": saved["reached"] / saved["reach_measured"] if saved["reach_measured"] else None,
        "memory": saved,
        "abstention": admitted[0] / admitted[1] if admitted[1] else None,
        "admitted": admitted[0],
        "unanswerable": admitted[1],
        "unjudged": unjudged,
        "waiting": waiting,
    }


def _leaning(line: Mapping[str, Any]) -> str | None:
    """Which way a line that couldn't be judged leaned, at its last grading."""
    said = line.get("second")
    if line.get("outcome") != "unjudged" or not said:
        return None
    return "right" if said.get("pass") else "wrong"


def questions(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Each question of a stored run: what was asked and said, and how it came out."""
    out = []
    for stem, entry in (run.get("cases") or {}).items():
        found = _SOURCE.match(str(entry.get("source") or ""))
        if not found:
            continue
        turns = entry.get("turns") or [{}]
        asked = str(turns[-1].get("message") or entry.get("name") or "")
        last = (entry.get("tries") or [{}])[-1]
        lines = last.get("rubric") or []
        rubric = entry.get("reply_rubric") or []
        out.append(
            {
                "case": stem,
                "kind": found[1],
                "unanswerable": bool(found[2]),
                "question": asked.split("\n", 1)[-1],
                "verdict": verdict_of(entry).kind.value,
                "memory": _memory(entry),
                "tie_break": bool(lines and lines[0].get("tie_break")),
                "leaning": _leaning(lines[0]) if lines else None,
                "right_is": str(rubric[0]) if rubric else "",
                "reply": str(last.get("reply_text") or "")[:_REPLY_CHARS],
                "reason": line_reason(lines[0]) if lines else "",
                "error": last.get("error"),
                "seconds": last.get("seconds"),
                "tokens": last.get("tokens"),
                "usd": last.get("usd"),
                "messages": last.get("messages"),
                "trace_url": last.get("trace_url"),
                "models": last.get("models") or [],
            }
        )
    return out


def _facts(found: Mapping[str, Any]) -> str:
    """How far the facts got, where the memory numbers say so plainly; else just the score."""
    right, judged = found["right"], found["judged"]
    if not judged:
        return f"Said it didn't know {found['admitted']} of {found['unanswerable']} times."
    memory = found["memory"]
    measured, written, committed = memory["measured"], memory["written"], memory["committed"]
    if not measured:
        return f"{right} of {judged} right; memory wasn't measured."
    if measured < _TOO_FEW:
        return f"{right} of {judged} right."
    if (measured - written) * 2 > measured:
        return (
            f"Lost most facts before saving: {measured - written} of {measured} "
            "never reached memory."
        )
    if (written - committed) * 2 > measured:
        return f"Wrote facts but didn't commit them: {written - committed} of {measured}."
    reached, asked = memory.get("reached", 0), memory.get("reach_measured", 0)
    if asked >= _TOO_FEW and reached * 2 >= asked and memory["used"] * 2 < reached:
        return f"Had the answer but didn't use it: {reached} of {asked} reached the model."
    if committed * 2 >= measured and memory["used"] * 2 < committed:
        return (
            f"Saved facts but didn't use them: {committed} of {measured} saved, "
            f"{memory['used']} answered right."
        )
    return f"{right} of {judged} right, with {committed} of {measured} facts saved."


def _abstained(found: Mapping[str, Any]) -> str:
    """What abstention adds to the score, when it adds anything."""
    asked, admitted = found["unanswerable"], found["admitted"]
    if asked < _TOO_FEW or not found["judged"]:
        return ""
    if admitted * 2 < asked:
        return f"Made up {asked - admitted} of {asked} answers it couldn't know."
    if admitted >= asked * 0.8 and found["right"] * 2 < found["judged"]:
        return "High abstention here may only mean it remembers little."
    return ""


def untested(found: Mapping[str, Any], plan: Mapping[str, Any] | None) -> bool:
    """Whether the run couldn't have tested memory: in one conversation, older messages never left
    the context, so the answer was still in front of the model."""
    memory = found.get("memory") or {}
    return bool(
        plan
        and plan.get("boundary") == "same"
        and memory.get("compact_measured")
        and not memory.get("compacted")
    )


def happened(found: Mapping[str, Any], plan: Mapping[str, Any] | None = None) -> str:
    """A stored run's :func:`report` in a sentence or two, from the numbers alone; empty if nothing
    was played."""
    if untested(found, plan):
        return "Nothing compacted, so the answers were still in context. This didn't test memory."
    answered = found["judged"] + found["unanswerable"]
    unjudged = found["unjudged"]
    if not answered and not unjudged:
        return ""
    first = _facts(found) if answered >= _TOO_FEW else "Too few questions to say."
    second = f"{unjudged} couldn't be judged." if unjudged else _abstained(found)
    return f"{first} {second}".strip()


def _totals(run: Mapping[str, Any]) -> dict[str, Any]:
    """What the run's tries came to. ``None`` where nothing was recorded, which isn't zero."""
    tries = [
        attempt
        for entry in (run.get("cases") or {}).values()
        if _SOURCE.match(str(entry.get("source") or ""))
        for attempt in entry.get("tries") or []
    ]
    seconds = [t["seconds"] for t in tries if t.get("seconds") is not None]
    return {
        "tokens": sum(t.get("tokens") or 0 for t in tries) or None,
        "usd": round(sum(t.get("usd") or 0 for t in tries), 4) or None,
        "median_seconds": statistics.median(seconds) if seconds else None,
        "models": sorted({model for t in tries for model in t.get("models") or []}),
    }


def overview(target: str, sets_dir: Path, runs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The sizes that can be run on *target* and the runs it has had, newest first."""
    sizes = []
    for size in SIZES:
        folder = sets_dir / target / f"{NAME}-{size}"
        sizes.append(
            {
                "size": size,
                "set": folder.name,
                "about": ABOUT[size],
                "questions": len(list(folder.glob("*.case.json"))) or None,
            }
        )
    played = [run for run in runs if str(run.get("set")).startswith(NAME) and questions(run)]
    return {"sizes": sizes, "runs": [_run_view(run) for run in played[:_RUNS_SHOWN]]}


def _run_view(run: Mapping[str, Any]) -> dict[str, Any]:
    found = report(run)
    return {
        "run": run["run"],
        "set": run["set"],
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "judge": ((run.get("judge") or {}).get("model")),
        "plan": run.get("plan"),
        "report": found,
        "happened": happened(found, run.get("plan")),
        "untested": untested(found, run.get("plan")),
        "totals": _totals(run),
        "current": run.get("current") if run.get("status") == "running" else None,
        "questions": questions(run),
    }
