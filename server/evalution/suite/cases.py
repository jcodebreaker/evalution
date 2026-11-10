"""What a set and a case may say (docs/case-format.md): their text read and checked, how two
versions of a test differ in what they check, and the words their ids come from. Where they are
kept is :mod:`evalution.suite.store`.

Every error names the file and the field, e.g.
``my-bot/core/two-files.case.json: tools.expected[1].times: must be a whole number of at least 1``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from evalution.core.case import (
    ARIA_ROLES,
    CLEAN_KINDS,
    CONTEXT_CHECKS,
    DEFAULT_PASS_MARKS,
    DEFAULT_TRIES,
    GRADERS,
    LOOK_CHARS,
    MATCHERS,
    MOST_TRIES,
    ORDERS,
    PROOF_KEEPS,
    PROOF_PARTS,
    SLUG,
    WITHIN,
    Case,
    CaseTurn,
    EvalSet,
    UiElement,
    UiLook,
)

BENCHMARK_PREFIX = "longmemeval-"


class CaseError(ValueError):
    """A set or case that does not follow the schema; the message names file and field."""


def _version(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def read_object(text: str, where: str) -> dict[str, Any]:
    """*text* as a JSON object; *where* names the file in errors.

    :raises CaseError: Not JSON, or not an object.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CaseError(f"{where}: not JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise CaseError(f"{where}: must be a JSON object")
    return data


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


def read_set(text: str, where: str, slug: str) -> EvalSet:
    """Set *slug* from its ``set.json`` *text*; *where* names the file in errors.

    :raises CaseError: Text that is not a valid ``set.json``.
    """
    data = read_object(text, where)
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


def with_pass_marks(text: str, where: str, value: Any) -> tuple[str, dict[str, float]]:
    """``set.json`` *text* with its pass marks set from *value* (the old ``pass_scores`` key
    goes); a key left out keeps the set's current mark. The new text, and the marks now in force.

    :raises CaseError: Marks that are not numbers from 0 to 1, or a bad ``set.json``.
    """
    current = read_set(text, where, "").pass_marks
    if not isinstance(value, dict):
        raise CaseError("pass marks: must be an object with tools and reply")
    marks = parse_pass_marks({**current, **value}, "pass marks")
    data = read_object(text, where)
    data.pop("pass_scores", None)
    data["pass_marks"] = marks
    return as_text(data), marks


def as_text(data: Any) -> str:
    """A set's or case's JSON as evalution writes it."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def is_benchmark(slug: str | None) -> bool:
    """Whether a set is a ready-made benchmark, kept apart from the app's own tests."""
    return str(slug).startswith(BENCHMARK_PREFIX)


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
    unknown = set(step) - {"name", "args", "times", "in"}
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    if not isinstance(step.get("name"), str) or not step["name"].strip():
        raise CaseError(f"{where}.name: must be a tool name")
    whose = step.get("in", "any")
    if not isinstance(whose, str) or not whose.strip():
        raise CaseError(f"{where}.in: must be main, any or a delegated agent's name")
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
    if isinstance(tries, bool) or not isinstance(tries, int) or not 1 <= tries <= MOST_TRIES:
        raise CaseError(f"{where}.tries: must be a whole number from 1 to {MOST_TRIES}")
    limits: dict[str, Any] = {"tries": tries}
    # An old file's "usd" is left unread: a test has no cost limit.
    if "seconds" in value:
        bound = value["seconds"]
        if isinstance(bound, bool) or not isinstance(bound, (int, float)) or bound <= 0:
            raise CaseError(f"{where}.seconds: must be a number above 0")
        limits["seconds"] = float(bound)
    return limits


def _page(value: Any, where: str) -> dict[str, Any]:
    """The ``page`` block: words the page must and must not show, whether its checks decide, and
    what a try keeps (``proof``)."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise CaseError(f"{where}: must be an object")
    unknown = set(value) - {"contains", "lacks", "decides", "proof", "shows", "hides", "looks"}
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    found: dict[str, Any] = {}
    for key in ("contains", "lacks"):
        words = value.get(key, [])
        if not isinstance(words, list) or not all(isinstance(w, str) and w.strip() for w in words):
            raise CaseError(f"{where}.{key}: must be a list of words")
        found[key] = tuple(words)
    decides = value.get("decides", False)
    if not isinstance(decides, bool):
        raise CaseError(f"{where}.decides: must be true or false")
    found["decides"] = decides
    found["proof"] = _proof(value.get("proof"), f"{where}.proof")
    for key in ("shows", "hides"):
        items = value.get(key, [])
        if not isinstance(items, list):
            raise CaseError(f"{where}.{key}: must be a list of elements")
        found[key] = tuple(
            _element(item, f"{where}.{key}[{i}]", hides=key == "hides")
            for i, item in enumerate(items)
        )
    looks = value.get("looks", [])
    if not isinstance(looks, list):
        raise CaseError(f"{where}.looks: must be a list of questions")
    found["looks"] = tuple(_look(item, f"{where}.looks[{i}]") for i, item in enumerate(looks))
    return found


_ELEMENT_FIELDS = {"role", "name", "test_id", "text", "within", "count", "turn"}
_LOCATOR_FIELDS = ("role", "name", "test_id", "text")


def _turn_number(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CaseError(f"{where}: must be a message turn's number, from 1")
    return value


def _element(value: Any, where: str, *, hides: bool) -> UiElement:
    """One element of ``page.shows`` or ``page.hides``, named as the page map names one: a role
    (one Playwright knows) with its accessible name, a test id, visible text, or more than one."""
    if not isinstance(value, dict):
        raise CaseError(f"{where}: must be an object")
    unknown = set(value) - _ELEMENT_FIELDS
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    if not any(key in value for key in _LOCATOR_FIELDS):
        raise CaseError(f"{where}: name the element by role, name, test_id or text")
    said: dict[str, str] = {}
    for key in _LOCATOR_FIELDS:
        if key in value:
            if not isinstance(value[key], str) or not value[key].strip():
                raise CaseError(f"{where}.{key}: must be a non-empty string")
            said[key] = value[key]
    role = said.get("role")
    if role is not None and role not in ARIA_ROLES:
        raise CaseError(f"{where}.role: {role!r} isn't an ARIA role (button, link, img, ...)")
    if "name" in said and role is None:
        raise CaseError(f'{where}.name: a name needs its role, like {{"role": "button", ...}}')
    within = value.get("within", "reply")
    if within not in WITHIN:
        raise CaseError(f"{where}.within: must be one of {', '.join(WITHIN)}")
    least, most = 1, None
    if "count" in value:
        if hides:
            raise CaseError(f"{where}.count: an element the page hides shows none; leave it out")
        least, most = _count(value["count"], f"{where}.count")
    turn = _turn_number(value["turn"], f"{where}.turn") if "turn" in value else None
    return UiElement(
        role=role,
        name=said.get("name"),
        test_id=said.get("test_id"),
        text=said.get("text"),
        within=within,
        least=least,
        most=most,
        turn=turn,
    )


def _count(value: Any, where: str) -> tuple[int, int | None]:
    """``count``: a whole number (exactly that many), or ``{min, max}`` with either left out."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 1:
            raise CaseError(f"{where}: must be at least 1; to say none show, use hides")
        return value, value
    if not isinstance(value, dict) or not value or set(value) - {"min", "max"}:
        raise CaseError(f"{where}: a whole number, or an object with min and/or max")
    for key, bound in value.items():
        if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
            raise CaseError(f"{where}.{key}: must be a whole number")
    least, most = value.get("min", 1), value.get("max")
    if most is not None and (most < least or most < 1):
        raise CaseError(f"{where}: max must be at least min, and at least 1")
    return least, most


def _look(value: Any, where: str) -> UiLook:
    """One ``page.looks`` entry: a yes/no question about a turn's screenshot."""
    if not isinstance(value, dict):
        raise CaseError(f"{where}: must be an object with a question")
    unknown = set(value) - {"question", "turn"}
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    question = value.get("question")
    if not isinstance(question, str) or not question.strip():
        raise CaseError(f"{where}.question: must be a yes/no question")
    question = " ".join(question.split())
    if len(question) > LOOK_CHARS:
        raise CaseError(f"{where}.question: one yes/no question of at most {LOOK_CHARS} characters")
    turn = _turn_number(value["turn"], f"{where}.turn") if "turn" in value else None
    return UiLook(question=question, turn=turn)


#: Why a test that checks the page's elements or look can't be saved: it says it plays through
#: the API alone.
UI_NEEDS_PAGE = "UI checks need the test to play through the page"
#: What a test may say of how it plays (``plays``): ``api``, through the app's API alone.
PLAYS = ("api",)


def ui_refusal(case: Case) -> str | None:
    """Why *case* can't be saved; ``None`` when it can. A test with UI checks (``page.shows``,
    ``hides``, ``looks``) is refused only when it says itself it plays through the API alone
    (``plays: api``). A target with no page map yet never refuses one: it is saved, its UI checks
    read as not judged (``no_page_map``) until the page is learned, and the rest of the test plays
    through the API."""
    if not case.ui_checks or case.plays != "api":
        return None
    return (
        f"{UI_NEEDS_PAGE}: this test says it plays through the API alone (plays: api). Take out "
        "plays, or page.shows, page.hides and page.looks."
    )


def _ui_turns(case: Case, where: str) -> None:
    """A UI check's ``turn`` must be one of the test's message turns."""
    most = case.message_turns
    for key, items in (("shows", case.shows), ("hides", case.hides), ("looks", case.looks)):
        for i, item in enumerate(items):
            if item.turn is not None and item.turn > most:
                raise CaseError(
                    f"{where}: page.{key}[{i}].turn: the test has {most} message turn(s)"
                )


def _proof(value: Any, where: str) -> dict[str, str]:
    """A test's own ``page.proof``: when its screenshots and its video are kept, part by part."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError(f"{where}: must be an object")
    unknown = set(value) - set(PROOF_PARTS)
    if unknown:
        raise CaseError(f"{where}.{sorted(unknown)[0]}: unknown field")
    for part, keep in value.items():
        if keep not in PROOF_KEEPS:
            raise CaseError(f"{where}.{part}: must be one of {', '.join(PROOF_KEEPS)}")
    return {part: str(value[part]) for part in PROOF_PARTS if part in value}


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
        "page",
        "plays",
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
    page = _page(data.get("page"), f"{where}: page")
    plays = data.get("plays")
    if plays is not None and plays not in PLAYS:
        raise CaseError(f"{where}: plays: must be {' or '.join(PLAYS)}, or left out")
    case = Case(
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
        shown_contains=page["contains"],
        shown_lacks=page["lacks"],
        page_decides=page["decides"],
        page_proof=page["proof"],
        shows=page["shows"],
        hides=page["hides"],
        looks=page["looks"],
        plays=plays,
    )
    _ui_turns(case, where)
    return case


def read_case(text: str, where: str, stem: str) -> Case:
    """Case *stem* from its file's *text*, versioned by it; *where* names the file in errors.

    :raises CaseError: Text that is not JSON, or the first field that does not follow the schema.
    """
    return parse_case(read_object(text, where), where, stem, _version(text))


def slugify(name: str) -> str:
    """A case stem from a case's name: lowercase words joined by ``-``."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    return "-".join(words)[:60].strip("-") or "case"


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
    ("Words the page must show", _part("page", "contains")),
    ("Words the page must not show", _part("page", "lacks")),
    ("Page checks decide", _part("page", "decides")),
    ("Elements the page must show", _part("page", "shows")),
    ("Elements the page must not show", _part("page", "hides")),
    ("Questions about how the page looks", _part("page", "looks")),
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
