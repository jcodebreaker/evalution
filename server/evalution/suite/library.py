"""The stateful test library (ADR 0002; docs/architecture.md, "Stateful test library").

Each template is ``<library>/<folder>/<id>.json``, in ``stateful/`` or, for the kinds a test
is written as from the page (:data:`KINDS`), ``kinds/``: what it checks (``title``, ``checks``,
``why``), what a target needs for it (``needs``), about how many messages it sends
(``est_turns``), and a ``case`` skeleton in the usual case format with ``{{placeholders}}``
the agent fills from the target's profile (:data:`PLACEHOLDERS`).

* :func:`load_templates` reads and checks every template: its fields, its placeholders, that
  its case is a valid case once filled, and that ``needs`` covers what the case uses.
* :func:`fit` says whether a template fits a target, from the adapter's capabilities and
  state checks, its traces and its profile: ``fits``, ``needs`` (something the adapter or the
  setup lacks) or ``not_applicable`` (the profile shows the app doesn't keep that kind of
  state).
* :func:`adapt` fills a template with the agent's values into a case draft. A value taken from
  the profile names the profile line it rests on, which must be there with its evidence tag.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evalution.core.case import CONTEXT_CHECKS, SLUG
from evalution.suite.cases import CaseError, parse_case

FOLDERS = ("kinds", "stateful")
#: The kinds of test the page offers to write, each one template with this ``kind``.
KINDS = ("exact", "tools", "delegation", "memory", "hard_input", "attack")
#: The evidence a profile line carries: ``[trace]``, ``[admin]`` or ``[code: path]``.
EVIDENCE = re.compile(r"\[(trace|admin|code:\s*([^\]]+?))\s*\]")

#: Each placeholder: its kind and what to fill it with. ``profile``: from the target's profile,
#: with the profile line it rests on; ``made_up``: test data that fits what the app is for;
#: ``given``: the owner's own words, asked for as they stand here; ``number``: a whole number.
PLACEHOLDERS: dict[str, tuple[str, str]] = {
    "prompt": ("given", "What the person sends"),
    "exact": ("given", "The exact words or figure the reply must hold"),
    "tool": ("given", "The tool it must use"),
    "want": ("given", "What that tool must return"),
    "outcome": ("given", "What should happen"),
    "must_not": ("given", "What must not happen"),
    "test_name": ("made_up", "A short sentence-case name for the test"),
    "topic": ("profile", "What the person works on with the app, as the profile describes it"),
    "ask": ("profile", "A typical request the app handles, as the person would send it"),
    "task": (
        "profile",
        "A task the app does in more than one step, as a verb phrase: 'plan a three-day trip'",
    ),
    "filler_topic": (
        "profile",
        "What the generated messages in between are about: another thing the app is used for",
    ),
    "fact": (
        "made_up",
        "One sentence the person says about themselves or their work, holding {{fact_word}}",
    ),
    "fact_word": (
        "made_up",
        "The distinctive word or number in {{fact}} (a name, place or date) that any faithful "
        "recall repeats as it is",
    ),
    "fact_question": ("made_up", "A question whose answer is {{fact_word}}, without saying it"),
    "new_fact": ("made_up", "{{fact}} corrected: the same subject, holding {{new_fact_word}}"),
    "new_fact_word": ("made_up", "The distinctive word of {{new_fact}}, not {{fact_word}}"),
    "distractor": (
        "made_up",
        "A sentence like {{fact}} about a different subject, holding {{distractor_word}}",
    ),
    "distractor_word": ("made_up", "The distinctive word of {{distractor}}"),
    "preference": (
        "made_up",
        "A standing preference whose effect shows in any reply, as an instruction: "
        "'end every answer with a line starting \"In short:\"'",
    ),
    "effect": (
        "made_up",
        "What a reply that follows {{preference}} visibly does, as a verb phrase: "
        "'ends with a line starting \"In short:\"'",
    ),
    "name": ("made_up", "The person's first name"),
    "role": ("made_up", "The person's role, as a phrase: 'the head of sales at a bakery chain'"),
    "n": (
        "number",
        "Generated messages in between (1 to 200): enough to fill the app's context for a "
        "compaction template (see tokens per ask in the profile), else about 20",
    ),
    "seconds": (
        "number",
        "The try's time limit in seconds. Leave it out: it is worked out from how long messages "
        "take, and the plan says so",
    ),
}
_NUMBER_RANGE = {"n": (1, 200), "seconds": (30, 14_400)}
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

#: What the adapter must have, by its capabilities.
CAPABILITY_NEEDS = ("state", "conversation_break", "sections", "background")
#: What the app must keep, as its profile shows it: the pattern, and the words for it.
TRAITS: dict[str, tuple[re.Pattern[str], str]] = {
    "memory": (re.compile(r"memor(y|ies)|remember|recall", re.I), "memory"),
    "compaction": (
        re.compile(r"compact|checkpoint|summari[sz]|rollover|context window", re.I),
        "compacting long chats",
    ),
}
NEEDS = (*CAPABILITY_NEEDS, "traces", *TRAITS)
#: The badge for each missing need.
LABELS = {
    "profile": "Needs a profile",
    "state": "Needs state checks",
    "conversation_break": "Needs new conversations",
    "sections": "Needs context sections",
    "background": "Needs waiting on background work",
    "traces": "Needs traces",
}


class LibraryError(ValueError):
    """A template that does not follow the shape, or values that do not fill it."""


@dataclass(frozen=True)
class Template:
    id: str
    title: str
    checks: str
    why: str
    needs: tuple[str, ...]
    est_turns: int
    case: dict[str, Any]
    #: The placeholders the case uses, in order of first use.
    placeholders: tuple[str, ...]
    #: The adapter state checks the case uses (``context_*`` aside).
    state_keys: tuple[str, ...]
    #: Which of :data:`KINDS` it is, if it is one.
    kind: str | None = None

    def asks(self) -> list[dict[str, str]]:
        """What the owner is asked for, in order."""
        return [
            {"name": n, "label": PLACEHOLDERS[n][1]}
            for n in self.placeholders
            if PLACEHOLDERS[n][0] == "given"
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "checks": self.checks,
            "why": self.why,
            "needs": list(self.needs),
            "est_turns": self.est_turns,
            "kind": self.kind,
            "asks": self.asks(),
        }


def _fill(value: Any, values: dict[str, Any]) -> Any:
    """*value* with each ``{{name}}`` replaced; a string that is one number placeholder becomes
    that number."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and isinstance(values.get(whole[1]), int):
            return values[whole[1]]
        return _PLACEHOLDER.sub(lambda m: str(values[m[1]]), value)
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    return value


def dummy_values(names: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Stand-in values for *names*, for checking a template."""
    return {n: _NUMBER_RANGE[n][0] if n in _NUMBER_RANGE else f"<{n}>" for n in names}


def _used(case: dict[str, Any]) -> tuple[tuple[str, ...], set[str], tuple[str, ...]]:
    """The placeholders, the needs and the adapter state keys a case skeleton uses."""
    names = tuple(dict.fromkeys(_PLACEHOLDER.findall(json.dumps(case))))
    needs: set[str] = set()
    keys: list[str] = []
    for turn in case.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        if "break" in turn:
            needs.add("conversation_break")
        for key in turn.get("expect_state") or {}:
            if key in CONTEXT_CHECKS:
                needs.add("traces")
            else:
                needs.add("state")
                keys.append(key)
    return names, needs, tuple(dict.fromkeys(keys))


def parse_template(data: Any, stem: str, folder: str = FOLDERS[1]) -> Template:
    """:raises LibraryError: The first field that does not follow the shape."""
    where = f"library/{folder}/{stem}.json"
    if not SLUG.match(stem):
        raise LibraryError(f"{where}: not a template id")
    if not isinstance(data, dict):
        raise LibraryError(f"{where}: must be a JSON object")
    unknown = set(data) - {"title", "checks", "why", "needs", "est_turns", "case", "kind"}
    if unknown:
        raise LibraryError(f"{where}: {sorted(unknown)[0]}: unknown field")
    for key in ("title", "checks", "why"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise LibraryError(f"{where}: {key}: must be a non-empty string")
    needs = data.get("needs")
    if not isinstance(needs, list) or not all(n in NEEDS for n in needs):
        raise LibraryError(f"{where}: needs: must list some of {', '.join(NEEDS)}")
    kind = data.get("kind")
    if kind is not None and kind not in KINDS:
        raise LibraryError(f"{where}: kind: one of {', '.join(KINDS)}")
    turns = data.get("est_turns")
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
        raise LibraryError(f"{where}: est_turns: must be a whole number of at least 1")
    case = data.get("case")
    if not isinstance(case, dict):
        raise LibraryError(f"{where}: case: must be a case skeleton")
    names, used, keys = _used(case)
    unknown = [n for n in names if n not in PLACEHOLDERS]
    if unknown:
        raise LibraryError(f"{where}: case: no placeholder {{{{{unknown[0]}}}}}")
    if left := used - set(needs):
        raise LibraryError(f"{where}: needs: the case also uses {', '.join(sorted(left))}")
    try:
        parse_case(_fill(case, dummy_values(names)), f"{where}: case")
    except CaseError as exc:
        raise LibraryError(str(exc)) from exc
    return Template(
        id=stem,
        title=data["title"].strip(),
        checks=data["checks"].strip(),
        why=data["why"].strip(),
        needs=tuple(needs),
        est_turns=turns,
        case=case,
        placeholders=names,
        state_keys=keys,
        kind=kind,
    )


def load_templates(root: Path) -> list[Template]:
    """Every template under *root* (the library folder), by id.

    :raises LibraryError: The first bad template.
    """
    found = []
    for folder in FOLDERS:
        for path in sorted((root / folder).glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise LibraryError(
                    f"library/{folder}/{path.name}: not readable JSON ({exc})"
                ) from exc
            found.append(parse_template(data, path.stem, folder))
    return found


def find(root: Path, template_id: str) -> Template:
    """:raises LookupError: No such template."""
    for template in load_templates(root):
        if template.id == template_id:
            return template
    raise LookupError(f"no library test {template_id}")


def fit(
    template: Template,
    capabilities: set[str],
    state_checks: frozenset[str] | None,
    traced: bool,
    profile: str,
    starts_clean: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Whether *template* fits a target: ``{verdict, label, missing, reason}``.

    *state_checks* is the adapter's own list, ``None`` when it doesn't say; *traced* whether
    the target's traces can be read; *starts_clean* what a try of it finds nothing of, ``None``
    when it doesn't say.
    """
    traits = [n for n in template.needs if n in TRAITS]
    lacking = [t for t in traits if not TRAITS[t][0].search(profile)]
    if profile.strip() and lacking:
        words = " or ".join(TRAITS[t][1] for t in lacking)
        return {
            "verdict": "not_applicable",
            "label": "Not for this agent",
            "missing": lacking,
            "reason": f"Its profile doesn't mention {words}.",
        }
    # What the adapter or the setup lacks comes first: a profile is only a step away.
    missing = [n for n in template.needs if n in CAPABILITY_NEEDS and n not in capabilities]
    if "state" in capabilities and state_checks is not None:
        missing += [f"check:{k}" for k in template.state_keys if k not in state_checks]
    if "traces" in template.needs and not traced:
        missing.append("traces")
    if traits and not profile.strip():
        missing.append("profile")
    if starts_clean is not None:
        wanted = template.case.get("needs_clean") or []
        missing += [f"clean:{k}" for k in wanted if k not in starts_clean]
    if not missing:
        return {"verdict": "fits", "label": "Fits", "missing": [], "reason": ""}
    labels = [
        LABELS.get(m)
        or (
            f"Needs a clean start of {m.removeprefix('clean:')}"
            if m.startswith("clean:")
            else f"Needs {m.removeprefix('check:').replace('_', ' ')} checks"
        )
        for m in missing
    ]
    return {
        "verdict": "needs",
        "label": labels[0],
        "missing": missing,
        "reason": "; ".join(labels) + ".",
    }


def _normal(text: str) -> str:
    return " ".join(text.split()).casefold()


def adapt(template: Template, values: Any, profile: str) -> dict[str, Any]:
    """The case draft of *template* filled with *values*, and the evidence of each value taken
    from the profile.

    *values* maps each placeholder to its value: a whole number for a number, text for a
    made-up value, and ``{"value", "evidence"}`` for a value from the profile, ``evidence``
    quoting the profile line it rests on (with its tag).

    :raises LibraryError: Every value that is missing or wrong, then a case that doesn't load.
    """
    if not isinstance(values, dict):
        raise LibraryError("values: must be an object, one entry per placeholder")
    tagged = [line.strip() for line in profile.splitlines() if EVIDENCE.search(line)]
    filled: dict[str, Any] = {}
    evidence: list[dict[str, str]] = []
    problems: list[str] = []
    for name in template.placeholders:
        kind, help_text = PLACEHOLDERS[name]
        given = values.get(name)
        if kind == "number":
            low, high = _NUMBER_RANGE[name]
            if isinstance(given, bool) or not isinstance(given, int) or not low <= given <= high:
                problems.append(f"{name}: a whole number from {low} to {high} ({help_text})")
            else:
                filled[name] = given
            continue
        quote = None
        if kind == "profile":
            if not isinstance(given, dict):
                problems.append(f"{name}: {{value, evidence}} ({help_text})")
                continue
            given, quote = given.get("value"), given.get("evidence")
        if not isinstance(given, str) or not given.strip() or "{{" in given or "}}" in given:
            problems.append(f"{name}: text ({help_text})")
            continue
        filled[name] = " ".join(given.split())
        if kind == "profile":
            line = (
                next((t for t in tagged if _normal(str(quote)) in _normal(t)), None)
                if isinstance(quote, str) and quote.strip()
                else None
            )
            if line is None:
                problems.append(
                    f"{name}: evidence must quote a line of the profile that carries its tag"
                )
                continue
            evidence.append({"placeholder": name, "value": filled[name], "from": line})
    if problems:
        raise LibraryError("Fill these:\n" + "\n".join(problems))
    case = _fill(template.case, filled)
    case["source"] = f"library: {template.id}"
    try:
        parse_case(case, f"library: {template.id}")
    except CaseError as exc:
        raise LibraryError(str(exc)) from exc
    return {"case": case, "evidence": evidence}
