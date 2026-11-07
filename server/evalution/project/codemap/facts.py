"""Facts read off a file with no model: what a reader checks and a summary blurs. Constants with
their values, environment variables read and their defaults, routes and the function that serves
each, tools with their arguments, prompt strings word for word (capped), and a settings file's
keys with their values. Each is ``{kind, text, symbols, line}``, line 1-based.

They come from the file's parse (:mod:`.parse`) where it has the definition, and from patterns
for what is a call rather than a definition (an environment read) or a file with no grammar.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

#: A file's facts, at most, and how much of a prompt one carries.
PER_FILE = 25
PROMPT_CHARS = 300
VALUE_CHARS = 80

_ENV = (
    # os.environ.get("X", d) / os.getenv("X", d) / os.environ["X"]
    re.compile(
        r"""os\.(?:environ\.get|getenv)\(\s*['"]([A-Z][A-Z0-9_]*)['"]\s*(?:,\s*([^)]{1,80}))?\)"""
    ),
    re.compile(r"""os\.environ\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]()"""),
    # process.env.X ?? d / process.env["X"] || d
    re.compile(
        r"""process\.env(?:\.([A-Z][A-Z0-9_]*)|\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\])(?:\s*(?:\?\?|\|\|)\s*([^;,)\n]{1,80}))?"""
    ),
    # os.Getenv("X")
    re.compile(r"""os\.(?:Getenv|LookupEnv)\(\s*"([A-Z][A-Z0-9_]*)"\s*\)()"""),
)
_KEY_VALUE = re.compile(r"""^([A-Za-z_][\w.-]*)\s*[:=]\s*(.+?)\s*$""")
_SECTION = re.compile(r"^\[([^\]]+)\]\s*$")


def _fact(kind: str, text: str, symbols: Sequence[str], line: int) -> dict[str, Any]:
    return {"kind": kind, "text": " ".join(text.split()), "symbols": list(symbols), "line": line}


def _env(lines: Sequence[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n, line in enumerate(lines, 1):
        for pattern in _ENV:
            for match in pattern.finditer(line):
                groups = [g for g in match.groups()]
                if len(groups) == 3:  # process.env: name in one of two groups
                    name, default = groups[0] or groups[1], groups[2]
                else:
                    name, default = groups[0], groups[1]
                if not name or name in seen:
                    continue
                seen.add(name)
                said = f"reads the environment variable {name}"
                if default and default.strip():
                    said += f", default {default.strip()[:VALUE_CHARS]}"
                found.append(_fact("env", said, [name], n))
    return found


def _settings(path: str, text: str) -> list[dict[str, Any]]:
    """A settings file's keys with their short values: JSON's top level, YAML's and TOML's
    unindented lines (a TOML table's keys under its name)."""
    lines = text.splitlines()
    if PurePosixPath(path).suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        scalars = {
            k: v
            for k, v in (data.items() if isinstance(data, dict) else [])
            if not isinstance(v, dict | list)
        }
        return [
            _fact("config", f"{k} = {json.dumps(v)[:VALUE_CHARS]}", [k], _line_of(lines, k))
            for k, v in scalars.items()
        ]
    found = []
    section = ""
    for n, line in enumerate(lines, 1):
        if table := _SECTION.match(line):
            section = table[1] + "."
            continue
        if line[:1] in (" ", "\t", "#", "-"):
            continue
        match = _KEY_VALUE.match(line)
        if match and match[2] not in ("{", "[", "|", ">") and len(match[2]) <= VALUE_CHARS:
            key = section + match[1]
            found.append(_fact("config", f"{key} = {match[2]}", [key], n))
    return found


def _line_of(lines: Sequence[str], key: str) -> int:
    return next((n for n, line in enumerate(lines, 1) if f'"{key}"' in line), 1)


def static_facts(path: str, text: str, parsed: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The facts of the file at *path*, from its *text* and its parse."""
    lines = text.splitlines()
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in (".yaml", ".yml", ".json", ".toml", ".env", ".ini", ".cfg"):
        return _settings(path, text)[:PER_FILE]
    found: list[dict[str, Any]] = []
    for d in parsed.get("defs") or []:
        name, kind, line = d["name"], d["kind"], d["line"]
        if kind == "constant" and " = …" not in d["sig"] and "=" in d["sig"]:
            found.append(_fact("constant", d["sig"], [name], line))
        elif kind == "prompt" and suffix not in (".md", ".mdx"):
            body = " ".join("\n".join(lines[line - 1 : d["end"]]).split())
            said = body[:PROMPT_CHARS] + ("…" if len(body) > PROMPT_CHARS else "")
            found.append(_fact("prompt", said, [name], line))
        elif kind == "route":
            head = lines[line - 1].strip() if line <= len(lines) else d["sig"]
            served = "" if head == d["sig"] else f" served by {name}"
            found.append(_fact("route", f"{head}{served}", [name], line))
        elif kind == "tool":
            found.append(_fact("tool", f"tool {name}: {d['sig']}", [name], line))
    found += _env(lines)
    return sorted(found, key=lambda f: f["line"])[:PER_FILE]
