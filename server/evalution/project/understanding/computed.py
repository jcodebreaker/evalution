"""What the engine computes: from the trace digest when an understanding is written
(:func:`build`), and on every read (:func:`view`)."""

from __future__ import annotations

import copy
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evalution.adapters import adapter_info, infos
from evalution.code import SKIP_DIRS
from evalution.config import ConfigError
from evalution.core.case import Case
from evalution.project import drift, library_fit
from evalution.project.history import History
from evalution.project.profile import read_profile
from evalution.project.understanding.steering import marks, overlay, steers
from evalution.project.understanding.store import load, progress
from evalution.results.views import latest_rows
from evalution.suite import library
from evalution.suite.cases import CaseError, slugify
from evalution.suite.store import load_cases, own_sets
from evalution.targets import Targets

PATHS_SHOWN = 5
#: The ``step_labels`` key for an ask that used no tools, and its label by default.
NO_TOOLS = "(no tools)"
ANSWERS_DIRECTLY = "Answers directly"
#: What each state check of an adapter says the app keeps.
STATE_WORDS = {
    "memory_contains": "memory",
    "memory_lacks": "memory",
    "file": "files",
    "checkpoint": "checkpoints",
    "messages_trimmed": "trimmed history",
    "state_contains": "thread state",
}
TRAIT_WORDS = {"memory": "memory", "compaction": "checkpoints"}
PY_FRAMEWORKS = (
    ("fastapi", "FastAPI"),
    ("flask", "Flask"),
    ("django", "Django"),
)
WEB_FRAMEWORKS = (("next", "Next.js web"), ("react", "React web"), ("vue", "Vue web"))
MODELS = (
    ("anthropic", "Claude models"),
    ("openai", "OpenAI models"),
    ("genai", "Gemini models"),
    ("generativeai", "Gemini models"),
    ("mistral", "Mistral models"),
    ("ollama", "Ollama models"),
)
#: API key names in a project's config that say which models it uses.
MODEL_KEYS = (
    ("ANTHROPIC_API_KEY", "Claude models"),
    ("OPENAI_API_KEY", "OpenAI models"),
    ("GEMINI_API_KEY", "Gemini models"),
    ("GOOGLE_API_KEY", "Gemini models"),
    ("MISTRAL_API_KEY", "Mistral models"),
    ("OLLAMA_HOST", "Ollama models"),
)
_CONFIG = re.compile(r"^(\.env(\..+)?|compose.*\.ya?ml|docker-compose.*\.ya?ml)$")
_KEY = re.compile(r"^\s*-?\s*([A-Z][A-Z0-9_]+)\s*[=:]", re.M)
#: Real asks needed before the card shows a typical time.
MIN_ASKS = 3
_DEPTH = 3
_PY_FILES = re.compile(r"^(pyproject\.toml|setup\.py|requirements.*\.txt)$")


# What the engine computes from the traces


def _fold(steps: Sequence[str]) -> list[str]:
    return [s for i, s in enumerate(steps) if i == 0 or steps[i - 1] != s]


def paths(
    per_ask: Sequence[Mapping[str, Any]],
    step_labels: Mapping[str, str],
    expected: Sequence[Sequence[str]] = (),
) -> list[dict[str, Any]]:
    """The most common per-ask paths, tool calls named by *step_labels* (else the tool's own
    name), repeats folded, each with its share of the asks; then each *expected* path never
    seen, with share 0."""
    counts: Counter[tuple[str, ...]] = Counter()
    for ask in per_ask:
        steps = _fold([step_labels.get(t) or t for t in ask.get("tools") or []])
        counts[tuple(steps or [step_labels.get(NO_TOOLS) or ANSWERS_DIRECTLY])] += 1
    total = sum(counts.values())
    out = [
        {"share": round(count / total, 2), "steps": list(steps), "seen": True}
        for steps, count in counts.most_common(PATHS_SHOWN)
    ]
    out += [
        {"share": 0.0, "steps": list(steps), "seen": False}
        for steps in expected
        if tuple(steps) not in counts
    ]
    return out


def build(written: Mapping[str, Any], found: Mapping[str, Any]) -> dict[str, Any]:
    """The understanding to store: what the agent *written*, with what the trace digest
    *found* gives: tool and per-ask numbers, edges, which nodes were seen, and paths."""
    doc = copy.deepcopy(dict(written))
    tools = found.get("tools") or {}
    per_ask = found.get("per_ask") or []
    asks = int(found.get("asks") or len(per_ask))
    named = {t for node in doc["nodes"] for t in node["tools"]}
    called = {n for n, t in tools.items() if t.get("calls")}
    typical = found.get("typical") or {}

    def median(key: str) -> float | None:
        value = (typical.get(key) or {}).get("median")
        return round(float(value), 4) if asks and value is not None else None

    nodes = doc["nodes"]
    main = next((n for n in nodes if n["kind"] == "agent"), None)
    person = next((n for n in nodes if n["kind"] == "person"), None)
    edges = []
    for node in nodes:
        mine = set(node["tools"])
        if node is main or node is person:
            count = asks
        elif mine:
            count = sum(1 for a in per_ask if mine & set(a.get("all") or []))
        elif node["kind"] == "worker":
            count = sum(1 for a in per_ask if a.get("delegated"))
        else:
            count = 0
        node["seen"] = count > 0 or (
            not mine and any(e["kind"] == "traces" for e in node["evidence"])
        )
        if main is not None and node is not main and node is not person:
            edges.append({"from": main["id"], "to": node["id"], "asks": count, "seen": count > 0})
    if person is not None and main is not None:
        edges.insert(0, {"from": person["id"], "to": main["id"], "asks": asks, "seen": asks > 0})
    doc["edges"] = edges
    doc["paths"] = paths(per_ask, doc["step_labels"], doc["expected_paths"])
    doc["traced"] = {
        "asks": asks,
        "tools": len(set(tools) | named),
        "tools_seen": len(called),
        "ask_seconds": median("seconds"),
    }
    return doc


# Computed on every read


def _package_files(folder: Path) -> tuple[str, set[str], bool]:
    """The Python package files' text (lowercased), the JS dependency names, and whether
    there is TypeScript, from the folder and a few levels under it."""
    python, js, typescript = [], set(), False
    for current, dirs, names in os.walk(folder):
        here = Path(current)
        depth = len(here.relative_to(folder).parts)
        dirs[:] = sorted(
            d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and depth < _DEPTH
        )
        for file in names:
            path = here / file
            try:
                if _PY_FILES.match(file):
                    python.append(path.read_text(encoding="utf-8", errors="replace")[:100_000])
                elif file == "package.json":
                    data = json.loads(path.read_text(encoding="utf-8"))
                    for key in ("dependencies", "devDependencies", "peerDependencies"):
                        if isinstance(data.get(key), dict):
                            js.update(data[key])
                elif file == "tsconfig.json":
                    typescript = True
            except (OSError, ValueError):
                continue
    return "\n".join(python).casefold(), js, typescript or "typescript" in js


def stack(folder: Path | None, adapter: str, traces: str | None) -> list[str]:
    """What the target is built with: ``["Python · FastAPI", "React web", "Claude models",
    "HTTP adapter", "Traces: evalution"]``, from its package files."""
    out: list[str] = []
    if folder is not None and folder.is_dir():
        python, js, typescript = _package_files(folder)
        if python:
            named = [*PY_FRAMEWORKS, *(i.framework for i in infos().values() if i.framework)]
            frameworks = [label for key, label in named if re.search(rf"\b{key}\b", python)]
            out.append(" · ".join(["Python", *frameworks]))
        if js:
            web = next((label for key, label in WEB_FRAMEWORKS if key in js), None)
            if web:
                out.append(web)
            else:
                language = "TypeScript" if typescript else "JavaScript"
                out.append(f"{language} · Express" if "express" in js else language)
        # Which models it really uses: the API keys its config names (names only, never values),
        # else its dependencies (a library like openai is often there only as a client shim).
        keys = _config_keys(folder)
        from_keys = [label for key, label in MODEL_KEYS if key in keys]
        words = python + " " + " ".join(js).casefold()
        found = from_keys or [label for key, label in MODELS if key in words]
        for label in found:
            if label not in out:
                out.append(label)
    info = adapter_info(adapter)
    out.append(info.label if info else f"{adapter} adapter")
    if traces:
        out.append(f"Traces: {traces}")
    return out


def _config_keys(folder: Path) -> set[str]:
    """The variable names set in the project's env and compose files (names only: values are
    never read into the result)."""
    keys: set[str] = set()
    for path in [*folder.glob("*"), *folder.glob("deployment/*")]:
        if path.is_file() and _CONFIG.match(path.name) and path.stat().st_size < 200_000:
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for name in _KEY.findall(text):
                keys.add(name)
    return keys


def state_kinds(state_checks: Sequence[str] | None, profile: str) -> list[str]:
    """The kinds of state the target keeps: from the adapter's state checks, and from what
    the profile says it keeps."""
    out: list[str] = []
    words = [STATE_WORDS.get(k, k.replace("_", " ")) for k in sorted(state_checks or [])]
    words += [w for t, w in TRAIT_WORDS.items() if library.TRAITS[t][0].search(profile)]
    for word in words:
        if word not in out:
            out.append(word)
    return out


def _sets(targets: Targets, name: str) -> dict[str, list[Case]]:
    """The target's tests by set; a set that does not read is left out."""
    found = {}
    for set_name in own_sets(targets.docs, name):
        try:
            found[set_name] = load_cases(targets.docs, name, set_name)
        except CaseError:
            continue
    return found


def _library_ids(cases: Sequence[Case]) -> set[str]:
    """The library tests *cases* were made from (``source: library: <id>``)."""
    return {
        c.source.split(":", 1)[1].strip()
        for c in cases
        if c.source and c.source.startswith("library:")
    }


def existing(targets: Targets, name: str) -> Callable[[Mapping[str, Any]], bool]:
    """Whether a starter is already a test of the target: one made from its library test, or
    one with its name (or the file name its name gives)."""
    cases = [c for found in _sets(targets, name).values() for c in found]
    made = _library_ids(cases)
    names = {c.name.casefold() for c in cases}
    stems = {c.case for c in cases}

    def exists(starter: Mapping[str, Any]) -> bool:
        title = str(starter.get("title") or "")
        return (
            starter.get("library") in made
            or title.strip().casefold() in names
            or slugify(title) in stems
        )

    return exists


def tests_numbers(
    targets: Targets, runs: History, name: str, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Tests, how many passed when last played (``None`` before any was), and the library
    tests covered of those that apply (*rows*: the library with its fit)."""
    found = drift.read(targets, name)
    tests, passing, played, sources = 0, 0, False, set()
    for set_name, cases in _sets(targets, name).items():
        for row in latest_rows(
            cases, runs.all(name, set_name), lambda case: drift.stale(case, found)
        ):
            tests += 1
            played = played or row["verdict"] is not None
            passing += row["verdict"] == "pass"
        sources |= _library_ids(cases)
    applies = {r["id"] for r in rows if r["fit"]["verdict"] != "not_applicable"}
    return {
        "tests": tests,
        "tests_passing": passing if played else None,
        "library_covered": len(applies & sources),
        "library_total": len(applies),
    }


def view(targets: Targets, runs: History, name: str) -> dict[str, Any]:
    """The ``Understanding`` the page reads: the stored one with the steers laid over it,
    and the parts that move (stack, tests, library, state, status) computed now. Before the
    agent wrote one: the card alone, ``learning``. ``progress``: how the last learn went, area by
    area (:func:`evalution.project.understanding.store.progress`); ``overview``: what the
    overview was written from (``state``, ``first_pass`` or ``final``, and its ``basis``), or
    ``None`` before a learn said."""
    stored = load(targets, name)
    log = steers(targets, name)
    doc = overlay(stored, log) if stored else None
    if doc is not None:
        marked = marks(targets, name, log)
        for section in doc["sections"]:
            for claim in section["claims"]:
                claim.update(marked.get(f"claim:{claim['id']}") or {})
        for node in doc["nodes"]:
            node.update(marked.get(f"node:{node['id']}") or {})
    conf = targets.conf(name)
    try:
        state_checks = targets.adapter(name).capabilities.state_checks
    except (ConfigError, ImportError):
        state_checks = None
    try:
        rows = library_fit.for_target(targets, name)
    except library.LibraryError:
        rows = []
    traces = "evalution, own Phoenix" if conf.phoenix else "evalution"
    changes = len(drift.read(targets, name).get("changes") or [])
    traced = (doc or {}).get("traced") or {}
    enough = int(traced.get("asks") or 0) >= MIN_ASKS and int(traced.get("tools_seen") or 0) > 0
    numbers = {
        "tools": int(traced.get("tools") or 0),
        "tools_seen": int(traced.get("tools_seen") or 0),
        "state_kinds": state_kinds(state_checks, read_profile(targets, name)),
        # Too few real asks make a typical time misleading: say nothing rather than a guess.
        "ask_seconds": traced.get("ask_seconds") if enough else None,
        **tests_numbers(targets, runs, name, rows),
    }
    card = {
        "name": name,
        "one_line": (doc or {}).get("one_line", ""),
        "summary": (doc or {}).get("summary", ""),
        "stack": stack(conf.code.path if conf.code else None, conf.adapter, traces),
        "status": "learning" if doc is None else "out_of_date" if changes else "up_to_date",
        "changes": changes,
        "numbers": numbers,
        "watched": ["code"] if conf.code else [],
    }
    if doc is None:
        return {
            "version": 0,
            "written_at": 0,
            "fingerprint": "",
            "card": card,
            "diagram": {"nodes": [], "edges": []},
            "paths": [],
            "sections": [],
            "priorities": [],
            "starters": [],
            "overview": None,
            "progress": progress(targets, name),
            "profile": None,
        }
    return {
        "version": doc["version"],
        "written_at": doc["written_at"],
        "fingerprint": doc["fingerprint"],
        "card": card,
        "diagram": {
            "nodes": [{k: v for k, v in n.items() if k != "tools"} for n in doc["nodes"]],
            "edges": doc["edges"],
        },
        "paths": doc["paths"],
        "sections": doc["sections"],
        "priorities": doc["priorities"],
        "starters": suggested(targets, name, doc["starters"]),
        "overview": doc.get("overview"),
        "progress": progress(targets, name),
        "profile": doc.get("profile"),
    }


def suggested(
    targets: Targets, name: str, starters: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The starters as the page's Suggested tests: each marked ``exists`` when it is already a
    test, and then never selected."""
    exists = existing(targets, name)
    out = []
    for starter in starters:
        made = exists(starter)
        out.append({**starter, "exists": made, "selected": bool(starter["selected"]) and not made})
    return out
