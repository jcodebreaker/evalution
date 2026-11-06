"""Sets and cases (docs/case-format.md): load, validate, save, and carry them to and from files.

Tests are documents of the workspace (:mod:`evalution.storage`), one kind each, keyed by path:

* :data:`SET`, ``<target>/<set>``: the set's ``set.json``, as text;
* :data:`CASE`, ``<target>/<set>/<case>``: a case's ``<case>.case.json``, the text exactly as
  written, since the page edits it as text and its version is the text's hash;
* :data:`FILE`, ``<target>/<set>/<path>``: any other file of the set, e.g. ``fixtures/orders.csv``
  a turn sends, or the ``plan.json`` that chose a benchmark: text as text, other bytes as
  ``{"base64": ...}``.

:func:`export_sets` and :func:`import_sets` carry them to and from folders laid out as
``<folder>/<target>/<set>/set.json``, ``*.case.json`` and the files, which is how a repo keeps its
tests for CI. Every error names the file and the field, e.g.
``my-bot/core/two-files.case.json: tools.expected[1].times: must be a whole number of at least 1``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from evalution.core.case import (
    CASE_SUFFIX,
    CLEAN_KINDS,
    CONTEXT_CHECKS,
    DEFAULT_PASS_MARKS,
    DEFAULT_TRIES,
    GRADERS,
    MATCHERS,
    ORDERS,
    SLUG,
    Case,
    CaseTurn,
    EvalSet,
)
from evalution.storage import Docs

#: The set's ``set.json``, under ``<target>/<set>``.
SET = "set"
#: A case's text, under ``<target>/<set>/<case>``.
CASE = "case"
#: Any other file of a set, under ``<target>/<set>/<path>``.
FILE = "set-file"
SET_FILE = "set.json"
BENCHMARK_PREFIX = "longmemeval-"


class CaseError(ValueError):
    """A set or case that does not follow the schema; the message names file and field."""


def _version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _key(target: str, slug: str, *rest: str) -> str:
    return "/".join((target, slug, *rest))


def _parse_object(text: str, where: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CaseError(f"{where}: not JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise CaseError(f"{where}: must be a JSON object")
    return data


def _json_text(data: Any, indent: int = 2) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def _score(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise CaseError(f"{where}: must be a number from 0 to 1")
    return float(value)


def parse_pass_marks(value: Any, where: str) -> dict[str, float]:
    """``{tools, reply}`` from *value*, each 0..1; a missing key keeps the default."""
    if value is None:
        return dict(DEFAULT_PASS_MARKS)
    if not isinstance(value, dict):
        raise CaseError(f"{where}: must be an object with tools and reply")
    unknown = set(value) - set(DEFAULT_PASS_MARKS)
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    return {
        key: _score(value[key], f"{where}.{key}") if key in value else default
        for key, default in DEFAULT_PASS_MARKS.items()
    }


def _text(data: dict[str, Any], key: str, where: str) -> str | None:
    """The optional one-line field *key*: ``None`` when absent or blank."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CaseError(f"{where}: {key}: must be a string")
    return " ".join(value.split()) or None


def _set_text(docs: Docs, target: str, slug: str) -> str:
    """:raises CaseError: An unsafe id or no such set."""
    if not SLUG.match(slug):
        raise CaseError(f"{slug}: not a set id")
    text = docs.get(SET, _key(target, slug))
    if not isinstance(text, str):
        raise CaseError(f"{target}/{slug}: no such set")
    return text


def load_set(docs: Docs, target: str, slug: str) -> EvalSet:
    """The set *slug* of *target*.

    :raises CaseError: An unsafe id, no such set or a bad ``set.json``.
    """
    where = f"{target}/{slug}/{SET_FILE}"
    text = _set_text(docs, target, slug)
    data = _parse_object(text, where)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CaseError(f"{where}: name: must be a non-empty string")
    grader = data.get("grader", "judge")
    if grader not in GRADERS:
        raise CaseError(f"{where}: grader: must be one of {', '.join(GRADERS)}")
    filler = data.get("filler", "light")
    if filler not in GRADERS:
        raise CaseError(f"{where}: filler: must be one of {', '.join(GRADERS)}")
    return EvalSet(
        set=slug,
        name=name.strip(),
        grader=grader,
        pass_marks=parse_pass_marks(
            data.get("pass_marks", data.get("pass_scores")), f"{where}: pass_marks"
        ),
        version=_version(text),
        about=_text(data, "about", where),
        filler=filler,
    )


def save_set(docs: Docs, target: str, slug: str, data: dict[str, Any]) -> None:
    """Write *data* as the ``set.json`` of set *slug* of *target*, made or replaced; its cases and
    files stay."""
    if not SLUG.match(slug):
        raise CaseError(f"{slug}: not a set id")
    docs.put(SET, _key(target, slug), _json_text(data))


def save_pass_marks(docs: Docs, target: str, slug: str, value: Any) -> dict[str, float]:
    """Set the pass marks of set *slug* in its ``set.json`` (the old ``pass_scores`` key goes);
    a key left out keeps the set's current mark. Returns the marks now in force.

    :raises CaseError: An unknown set, or marks that are not numbers from 0 to 1.
    """
    current = load_set(docs, target, slug).pass_marks
    if not isinstance(value, dict):
        raise CaseError("pass marks: must be an object with tools and reply")
    marks = parse_pass_marks({**current, **value}, "pass marks")
    data = _parse_object(_set_text(docs, target, slug), f"{target}/{slug}/{SET_FILE}")
    data.pop("pass_scores", None)
    data["pass_marks"] = marks
    save_set(docs, target, slug, data)
    return marks


def list_sets(docs: Docs, target: str) -> list[str]:
    """The set ids of *target*, sorted."""
    return [key.split("/", 1)[1] for key in docs.keys(SET, f"{target}/")]


def is_benchmark(slug: str | None) -> bool:
    """Whether a set is a ready-made benchmark, kept apart from the app's own tests."""
    return str(slug).startswith(BENCHMARK_PREFIX)


def own_sets(docs: Docs, target: str) -> list[str]:
    """The app's own tests: its sets, benchmarks left out."""
    return [slug for slug in list_sets(docs, target) if not is_benchmark(slug)]


def _result(spec: Any, where: str) -> None:
    """``{"name": tool, "path": "a.b", "want": matcher}``: without a path, *want* reads the whole
    result as text."""
    if not isinstance(spec, dict):
        raise CaseError(f"{where}: must be an object")
    unknown = set(spec) - {"name", "path", "want"}
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        raise CaseError(f"{where}.name: must be a tool name")
    path = spec.get("path")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise CaseError(f"{where}.path: must be a dotted path")
    if "want" not in spec:
        raise CaseError(f"{where}.want: required")
    _matcher(spec["want"], f"{where}.want")


def _matcher(value: Any, where: str) -> None:
    if not (isinstance(value, dict) and len(value) == 1 and next(iter(value)) in MATCHERS):
        return  # a plain value: equal
    op, arg = next(iter(value.items()))
    if op == "contains" and not isinstance(arg, str):
        raise CaseError(f"{where}.contains: must be a string")
    if op == "re":
        if not isinstance(arg, str):
            raise CaseError(f"{where}.re: must be a string")
        try:
            re.compile(arg)
        except re.error as exc:
            raise CaseError(f"{where}.re: not a valid pattern ({exc})") from exc
    if op == "len":
        if not isinstance(arg, dict) or not arg or set(arg) - {"min", "max"}:
            raise CaseError(f"{where}.len: must be an object with min and/or max")
        for key, bound in arg.items():
            if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
                raise CaseError(f"{where}.len.{key}: must be a whole number")
    if op == "any" and arg is not True:
        raise CaseError(f"{where}.any: must be true")


def _call(step: Any, where: str, *, allow_times: bool = True) -> None:
    if not isinstance(step, dict):
        raise CaseError(f"{where}: must be an object")
    unknown = set(step) - {"name", "args", "times"}
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    if not isinstance(step.get("name"), str) or not step["name"].strip():
        raise CaseError(f"{where}.name: must be a tool name")
    args = step.get("args", {})
    if not isinstance(args, dict):
        raise CaseError(f"{where}.args: must be an object")
    for key, value in args.items():
        _matcher(value, f"{where}.args.{key}")
    if "times" in step:
        times = step["times"]
        if not allow_times:
            raise CaseError(f"{where}.times: not allowed inside parallel")
        if isinstance(times, bool) or not isinstance(times, int) or times < 1:
            raise CaseError(f"{where}.times: must be a whole number of at least 1")


def _tools(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CaseError(f"{where}: must be an object")
    unknown = set(value) - {"order", "extras", "expected", "never", "strict", "results"}
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    order = value.get("order", "in_order")
    if order not in ORDERS:
        raise CaseError(f"{where}.order: must be one of {', '.join(ORDERS)}")
    extras = value.get("extras", True)
    if not isinstance(extras, bool):
        raise CaseError(f"{where}.extras: must be true or false")
    expected = value.get("expected", [])
    if not isinstance(expected, list):
        raise CaseError(f"{where}.expected: must be a list of steps")
    for i, step in enumerate(expected):
        at = f"{where}.expected[{i}]"
        if isinstance(step, dict) and "parallel" in step:
            if set(step) != {"parallel"}:
                raise CaseError(f"{at}: a parallel step has only 'parallel'")
            group = step["parallel"]
            if not isinstance(group, list) or len(group) < 2:
                raise CaseError(f"{at}.parallel: must list at least two calls")
            for j, member in enumerate(group):
                _call(member, f"{at}.parallel[{j}]", allow_times=False)
        else:
            _call(step, at)
    never = value.get("never", [])
    if not isinstance(never, list) or not all(isinstance(n, str) and n for n in never):
        raise CaseError(f"{where}.never: must be a list of tool names")
    strict = value.get("strict", False)
    if not isinstance(strict, bool):
        raise CaseError(f"{where}.strict: must be true or false")
    results = value.get("results", [])
    if not isinstance(results, list):
        raise CaseError(f"{where}.results: must be a list")
    for i, spec in enumerate(results):
        _result(spec, f"{where}.results[{i}]")
    return {
        **value,
        "order": order,
        "extras": extras,
        "expected": expected,
        "never": never,
        "strict": strict,
        "results": results,
    }


MAX_FILLER = 200


def _filler(turn: dict[str, Any], at: str) -> CaseTurn:
    unknown = set(turn) - {"filler", "about"}
    if unknown:
        raise CaseError(f"{at}.{sorted(unknown)[0]}: unknown field")
    n, about = turn["filler"], turn.get("about")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_FILLER:
        raise CaseError(f"{at}.filler: must be a whole number from 1 to {MAX_FILLER}")
    if not isinstance(about, str) or not about.strip():
        raise CaseError(f"{at}.about: must be a non-empty string")
    return CaseTurn("", filler=n, about=about.strip())


def _state(turn: dict[str, Any], at: str) -> CaseTurn:
    """A state check: ``context_*`` texts (decided from the traces), or the adapter's own keys
    with a plain value (true/false, text or a number)."""
    if set(turn) != {"expect_state"}:
        raise CaseError(f"{at}: a state check has only 'expect_state'")
    checks = turn["expect_state"]
    if not isinstance(checks, dict) or not checks:
        raise CaseError(f"{at}.expect_state: must be an object with at least one check")
    for key, value in checks.items():
        if not isinstance(key, str) or not SLUG.match(key):
            raise CaseError(f"{at}.expect_state.{key}: not a check name")
        if key in CONTEXT_CHECKS and (not isinstance(value, str) or not value.strip()):
            raise CaseError(f"{at}.expect_state.{key}: must be a non-empty string")
        if not isinstance(value, (bool, str, int, float)) or value == "":
            raise CaseError(f"{at}.expect_state.{key}: must be true/false, text or a number")
    return CaseTurn("", state=dict(checks))


def _turns(value: Any, where: str) -> tuple[CaseTurn, ...]:
    """The turns; a turn of only ``{"stop_after": n}`` presses Stop on the turn before it."""
    if not isinstance(value, list) or not value:
        raise CaseError(f"{where}: must be a non-empty list")
    turns: list[CaseTurn] = []
    for i, turn in enumerate(value):
        at = f"{where}[{i}]"
        if not isinstance(turn, dict):
            raise CaseError(f"{at}: must be an object")
        if "filler" in turn:
            turns.append(_filler(turn, at))
            continue
        if "expect_state" in turn:
            turns.append(_state(turn, at))
            continue
        if "memory_holds" in turn:
            wanted = turn["memory_holds"]
            if set(turn) != {"memory_holds"} or not isinstance(wanted, str) or not wanted.strip():
                raise CaseError(f"{at}: a memory_holds turn is only that, with non-empty text")
            turns.append(CaseTurn("", memory_holds=wanted))
            continue
        if "break" in turn:
            if turn != {"break": "conversation"}:
                raise CaseError(f'{at}: a break turn is only {{"break": "conversation"}}')
            turns.append(CaseTurn("", new_conversation=True))
            continue
        unknown = set(turn) - {"message", "files", "stop_after", "fresh", "blocked"}
        if unknown:
            raise CaseError(f"{at}.{sorted(unknown)[0]}: unknown field")
        stop = turn.get("stop_after")
        if stop is not None and (
            isinstance(stop, bool) or not isinstance(stop, (int, float)) or stop <= 0
        ):
            raise CaseError(f"{at}.stop_after: must be a number of seconds above 0")
        if "message" not in turn:
            if stop is None or not turns or set(turn) != {"stop_after"}:
                raise CaseError(f"{at}.message: must be a non-empty string")
            if not turns[-1].is_message:
                raise CaseError(f"{at}.stop_after: must follow a message turn")
            last = turns.pop()
            turns.append(
                CaseTurn(
                    last.message,
                    last.files,
                    float(stop),
                    last.fresh,
                    expect_blocked=last.expect_blocked,
                )
            )
            continue
        message = turn["message"]
        if not isinstance(message, str) or not message.strip():
            raise CaseError(f"{at}.message: must be a non-empty string")
        files = turn.get("files", [])
        if not isinstance(files, list) or not all(isinstance(f, str) and f for f in files):
            raise CaseError(f"{at}.files: must be a list of paths")
        for j, name in enumerate(files):
            if name.startswith("/") or ".." in Path(name).parts:
                raise CaseError(f"{at}.files[{j}]: must be a path inside the set folder")
        fresh = turn.get("fresh", False)
        if not isinstance(fresh, bool):
            raise CaseError(f"{at}.fresh: must be true or false")
        blocked = turn.get("blocked", False)
        if not isinstance(blocked, bool):
            raise CaseError(f"{at}.blocked: must be true or false")
        turns.append(
            CaseTurn(
                message,
                tuple(files),
                float(stop) if stop is not None else None,
                fresh,
                expect_blocked=blocked,
            )
        )
    return tuple(turns)


def _limits(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CaseError(f"{where}: must be an object")
    unknown = set(value) - {"tries", "seconds", "usd"}
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    tries = value.get("tries", DEFAULT_TRIES)
    if isinstance(tries, bool) or not isinstance(tries, int) or not 1 <= tries <= 20:
        raise CaseError(f"{where}.tries: must be a whole number from 1 to 20")
    limits: dict[str, Any] = {"tries": tries}
    # An old file's "usd" is left unread: a test has no cost limit.
    if "seconds" in value:
        bound = value["seconds"]
        if isinstance(bound, bool) or not isinstance(bound, (int, float)) or bound <= 0:
            raise CaseError(f"{where}.seconds: must be a number above 0")
        limits["seconds"] = float(bound)
    return limits


def parse_case(data: Any, where: str, stem: str = "", version: str = "") -> Case:
    """A case from its file's JSON *data*.

    :raises CaseError: The first field that does not follow the schema.
    """
    if not isinstance(data, dict):
        raise CaseError(f"{where}: must be a JSON object")
    known = {
        "name",
        "about",
        "source",
        "turns",
        "tools",
        "reply",
        "limits",
        "must_hold",
        "needs_clean",
        "fold_at_tokens",
    }
    unknown = set(data) - known
    if unknown:
        raise CaseError(f"{where}: {sorted(unknown)[0]}: unknown field")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CaseError(f"{where}: name: must be a non-empty string")
    reply = data.get("reply", {})
    if not isinstance(reply, dict):
        raise CaseError(f"{where}: reply: must be an object")
    reference = reply.get("reference", "")
    if not isinstance(reference, str):
        raise CaseError(f"{where}: reply.reference: must be a string")
    rubric = reply.get("rubric", [])
    if not isinstance(rubric, list) or not all(isinstance(r, str) and r.strip() for r in rubric):
        raise CaseError(f"{where}: reply.rubric: must be a list of lines")
    words = {}
    for key in ("contains", "lacks"):
        found = reply.get(key, [])
        if not isinstance(found, list) or not all(isinstance(w, str) and w.strip() for w in found):
            raise CaseError(f"{where}: reply.{key}: must be a list of words")
        words[key] = tuple(found)
    unknown = set(reply) - {"reference", "rubric", "pass", "contains", "lacks"}
    if unknown:
        raise CaseError(f"{where}: reply.{sorted(unknown)[0]}: unknown field")
    need = reply.get("pass")
    if need is not None and (
        isinstance(need, bool) or not isinstance(need, int) or not 1 <= need <= len(rubric)
    ):
        raise CaseError(f"{where}: reply.pass: must be a whole number from 1 to {len(rubric)}")
    fold_at_tokens = data.get("fold_at_tokens")
    if fold_at_tokens is not None and (
        not isinstance(fold_at_tokens, int)
        or isinstance(fold_at_tokens, bool)
        or fold_at_tokens < 1
    ):
        raise CaseError(f"{where}: fold_at_tokens: a positive number")
    needs_clean = data.get("needs_clean", [])
    if not isinstance(needs_clean, list) or set(map(str, needs_clean)) - set(CLEAN_KINDS):
        raise CaseError(f"{where}: needs_clean: a list of {', '.join(CLEAN_KINDS)}")
    must_hold = data.get("must_hold", False)
    if not isinstance(must_hold, bool):
        raise CaseError(f"{where}: must_hold: must be true or false")
    return Case(
        case=stem,
        name=name.strip(),
        turns=_turns(data.get("turns"), f"{where}: turns"),
        tools=_tools(data.get("tools"), f"{where}: tools"),
        reference=reference,
        rubric=tuple(rubric),
        limits=_limits(data.get("limits"), f"{where}: limits"),
        version=version,
        raw=data,
        about=_text(data, "about", where),
        source=_text(data, "source", where),
        reply_pass=need,
        must_hold=must_hold,
        reply_contains=words["contains"],
        reply_lacks=words["lacks"],
        needs_clean=tuple(needs_clean),
        fold_at_tokens=fold_at_tokens,
    )


def _case_where(target: str, slug: str, stem: str) -> str:
    return f"{target}/{slug}/{stem}{CASE_SUFFIX}"


def case_text(docs: Docs, target: str, slug: str, stem: str) -> str:
    """Case *stem* of set *slug* as written.

    :raises CaseError: An unsafe id or no such case.
    """
    if not SLUG.match(slug) or not SLUG.match(stem):
        raise CaseError(f"{slug}/{stem}: not a case id")
    text = docs.get(CASE, _key(target, slug, stem))
    if not isinstance(text, str):
        raise CaseError(f"{slug}/{stem}: no such test")
    return text


def load_case(docs: Docs, target: str, slug: str, stem: str) -> Case:
    """Case *stem* of set *slug*.

    :raises CaseError: An unsafe id, no such case or a bad case.
    """
    return _case_of(target, slug, stem, case_text(docs, target, slug, stem))


def _case_of(target: str, slug: str, stem: str, text: str) -> Case:
    where = _case_where(target, slug, stem)
    return parse_case(_parse_object(text, where), where, stem, _version(text))


def case_stems(docs: Docs, target: str, slug: str) -> list[str]:
    """The case ids of set *slug*, sorted."""
    prefix = _key(target, slug, "")
    return [key[len(prefix) :] for key in docs.keys(CASE, prefix)]


def load_cases(docs: Docs, target: str, slug: str) -> list[Case]:
    """Every case of set *slug*, by id.

    :raises CaseError: The first bad case.
    """
    prefix = _key(target, slug, "")
    return [
        _case_of(target, slug, key[len(prefix) :], text)
        for key, text, _ in docs.items(CASE, prefix)
    ]


def slugify(name: str) -> str:
    """A case stem from a case's name: lowercase words joined by ``-``."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    return "-".join(words)[:60].strip("-") or "case"


def save_case(docs: Docs, target: str, slug: str, data: Any, stem: str | None = None) -> str:
    """Validate *data* and write it as a new case of set *slug*, or over case *stem* of that set
    when given; returns its stem.

    :raises CaseError: An unknown set or case, or a bad case.
    """
    load_set(docs, target, slug)
    case = parse_case(data, f"{slug}/{stem or '(new case)'}")
    if stem is not None:
        case_text(docs, target, slug, stem)
    else:
        taken = set(case_stems(docs, target, slug))
        base = slugify(case.name)
        stem, n = base, 1
        while stem in taken:
            n += 1
            stem = f"{base}-{n}"
    put_case(docs, target, slug, stem, data)
    return stem


def put_case(docs: Docs, target: str, slug: str, stem: str, data: Any) -> None:
    """Write *data* as case *stem* of set *slug*, made or replaced, once it reads as a case.

    :raises CaseError: An unsafe id or a bad case.
    """
    if not SLUG.match(stem):
        raise CaseError(f"{slug}/{stem}: not a case id")
    parse_case(data, _case_where(target, slug, stem), stem)
    docs.put(CASE, _key(target, slug, stem), _json_text(data))


def _said(data: dict[str, Any]) -> list[Any]:
    return [t.get("message", t) for t in data.get("turns") or [] if isinstance(t, dict)]


def _part(*keys: str) -> Any:
    def read(data: dict[str, Any]) -> Any:
        for key in keys:
            data = data.get(key) if isinstance(data, dict) else None
        return data

    return read


def _each_turn(key: str) -> Any:
    def read(data: dict[str, Any]) -> Any:
        found = [t.get(key) or None for t in data.get("turns") or [] if isinstance(t, dict)]
        return found if any(found) else None

    return read


#: What makes a test the test it is, in words. A name, a description or how many tries it gets
#: change none of it.
_MEANING = (
    ("What the person sends", _said),
    ("Rubric", _part("reply", "rubric")),
    ("How many rubric lines must pass", _part("reply", "pass")),
    ("What a good reply does", _part("reply", "reference")),
    ("Words the reply must have", _part("reply", "contains")),
    ("Words the reply must not have", _part("reply", "lacks")),
    ("Expected tools", _part("tools", "expected")),
    ("Tool order", _part("tools", "order")),
    ("Unexpected tool calls allowed", _part("tools", "extras")),
    ("Steps are strict", _part("tools", "strict")),
    ("What tools must return", _part("tools", "results")),
    ("Tools never to call", _part("tools", "never")),
    ("Clean start", _part("needs_clean")),
    ("Must hold", _part("must_hold")),
    ("Files sent", _each_turn("files")),
    ("A turn expected to be blocked", _each_turn("blocked")),
    ("A turn that must be new each try", _each_turn("fresh")),
    ("Time limit", _part("limits", "seconds")),
)


def meaning_changes(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """What differs between two tests in what they check: ``{what, before, after}``, so results of
    one can't be read against the other. An empty list says they ask the same thing."""
    return [
        {"what": what, "before": read(old), "after": read(new)}
        for what, read in _MEANING
        if (read(old) or None) != (read(new) or None)
    ]


def _typed(docs: Docs, target: str, slug: str, stem: str, text: str) -> tuple[str, dict]:
    """The case as written now and *text* read as a valid case to go over it.

    :raises CaseError: An unknown set or test, text that is not JSON, or a bad test.
    """
    load_set(docs, target, slug)
    now = case_text(docs, target, slug, stem)
    where = _case_where(target, slug, stem)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CaseError(f"{where}: not JSON ({exc})") from exc
    parse_case(data, where, stem)
    return now, data


def changes_in_text(
    docs: Docs, target: str, slug: str, stem: str, text: str
) -> list[dict[str, Any]]:
    """What saving *text* over test *stem* would change in its meaning.

    :raises CaseError: An unknown set or test, text that is not JSON, or a bad test.
    """
    now, data = _typed(docs, target, slug, stem, text)
    return meaning_changes(_parse_object(now, _case_where(target, slug, stem)), data)


def write_case_text(docs: Docs, target: str, slug: str, stem: str, text: str) -> Case:
    """Write *text*, as typed, over test *stem* of set *slug* once it reads as a valid test;
    returns the test as saved.

    :raises CaseError: An unknown set or test, text that is not JSON, or a bad test.
    """
    _typed(docs, target, slug, stem, text)
    docs.put(CASE, _key(target, slug, stem), text if text.endswith("\n") else text + "\n")
    return load_case(docs, target, slug, stem)


# A set's other files


def _file_body(data: bytes) -> Any:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(data).decode("ascii")}


def _file_bytes(body: Any) -> bytes:
    if isinstance(body, dict):
        return base64.b64decode(body["base64"])
    return str(body).encode("utf-8")


def _file_name(name: str) -> str:
    """*name* as a path inside the set, ``a/b.csv``.

    :raises CaseError: One that would leave it, or names a set's own file.
    """
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or ".." in parts or "\\" in name:
        raise CaseError(f"{name}: must be a path inside the set")
    path = "/".join(p for p in parts if p != ".")
    if path == SET_FILE or ("/" not in path and path.endswith(CASE_SUFFIX)):
        raise CaseError(f"{name}: is the set's own file")
    return path


def write_file(docs: Docs, target: str, slug: str, name: str, data: bytes) -> str:
    """Write *data* as the set's file *name* (``fixtures/x.csv``), made or replaced; its path.

    :raises CaseError: An unknown set, or a name outside it.
    """
    load_set(docs, target, slug)
    path = _file_name(name)
    docs.put(FILE, _key(target, slug, path), _file_body(data))
    return path


def read_file(docs: Docs, target: str, slug: str, name: str) -> bytes | None:
    """The set's file *name*, ``None`` when it has none."""
    try:
        path = _file_name(name)
    except CaseError:
        return None
    body = docs.get(FILE, _key(target, slug, path))
    return None if body is None else _file_bytes(body)


def sent_files(docs: Docs, target: str, slug: str, case: Case) -> dict[str, bytes]:
    """The set's files *case*'s turns send, by name as the case writes it; those the set has."""
    found = {}
    for name in dict.fromkeys(name for turn in case.turns for name in turn.files):
        data = read_file(docs, target, slug, name)
        if data is not None:
            found[name] = data
    return found


def _files(docs: Docs, target: str, slug: str) -> list[tuple[str, bytes]]:
    prefix = _key(target, slug, "")
    return [(key[len(prefix) :], _file_bytes(body)) for key, body, _ in docs.items(FILE, prefix)]


def unpack_files(docs: Docs, target: str, slug: str, into: Path) -> Path:
    """The set's files written out under *into*, emptied first, for an adapter that is handed
    paths; *into*."""
    if into.is_dir():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    for path, data in _files(docs, target, slug):
        out = into / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return into


# To and from folders


def stored_targets(docs: Docs) -> list[str]:
    """The targets that have a set, sorted."""
    return sorted({key.split("/", 1)[0] for key in docs.keys(SET)})


def delete_set(docs: Docs, target: str, slug: str) -> None:
    """Set *slug* of *target* gone: its ``set.json``, cases and files."""
    docs.delete(SET, _key(target, slug))
    docs.delete_prefix(CASE, _key(target, slug, ""))
    docs.delete_prefix(FILE, _key(target, slug, ""))


def _read_text(path: Path, where: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaseError(f"{where}: must be UTF-8 text") from exc


def _import_set(docs: Docs, target: str, folder: Path) -> None:
    slug = folder.name
    where = f"{target}/{slug}"
    texts = {SET_FILE: _read_text(folder / SET_FILE, f"{where}/{SET_FILE}")}
    files: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink() or not path.is_file() or rel == SET_FILE:
            continue
        if path.parent == folder and rel.endswith(CASE_SUFFIX):
            if SLUG.match(rel[: -len(CASE_SUFFIX)]):
                texts[rel] = _read_text(path, f"{where}/{rel}")
        else:
            files[rel] = path.read_bytes()
    delete_set(docs, target, slug)
    docs.put(SET, _key(target, slug), texts.pop(SET_FILE))
    for rel, text in texts.items():
        docs.put(CASE, _key(target, slug, rel[: -len(CASE_SUFFIX)]), text)
    for rel, data in files.items():
        docs.put(FILE, _key(target, slug, rel), _file_body(data))


def _set_folders(folder: Path, targets: Iterable[str] | None) -> list[tuple[str, Path]]:
    names = sorted(targets) if targets is not None else None
    found = []
    for target in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not target.is_dir() or not SLUG.match(target.name):
            continue
        if names is not None and target.name not in names:
            continue
        found += [
            (target.name, s)
            for s in sorted(target.iterdir())
            if SLUG.match(s.name) and (s / SET_FILE).is_file() and not s.is_symlink()
        ]
    return found


def import_sets(docs: Docs, folder: Path, targets: Iterable[str] | None = None) -> list[str]:
    """Every set in *folder* (``<target>/<set>/set.json``, of *targets* only when given) put in
    the workspace, each replacing the set of that name whole; ``<target>/<set>`` of each.
    Symbolic links are left out.

    :raises CaseError: A file that is not UTF-8 text where text is wanted; nothing of that set is
        changed.
    """
    done = []
    for target, path in _set_folders(folder, targets):
        _import_set(docs, target, path)
        done.append(f"{target}/{path.name}")
    return done


def export_sets(docs: Docs, folder: Path, targets: Iterable[str] | None = None) -> list[str]:
    """Every set of the workspace (of *targets* only when given) written into *folder* as
    :func:`import_sets` reads it, each set's folder replaced whole; ``<target>/<set>`` of each."""
    done = []
    for target in sorted(targets) if targets is not None else stored_targets(docs):
        for slug in list_sets(docs, target):
            out = folder / target / slug
            if out.is_dir() and not out.is_symlink():
                shutil.rmtree(out)
            out.mkdir(parents=True, exist_ok=True)
            (out / SET_FILE).write_bytes(_set_text(docs, target, slug).encode())
            for stem in case_stems(docs, target, slug):
                text = case_text(docs, target, slug, stem)
                (out / f"{stem}{CASE_SUFFIX}").write_bytes(text.encode())
            for path, data in _files(docs, target, slug):
                (out / path).parent.mkdir(parents=True, exist_ok=True)
                (out / path).write_bytes(data)
            done.append(f"{target}/{slug}")
    return done
