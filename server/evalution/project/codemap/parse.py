"""One file into what it defines and what it uses, with no model: tree-sitter for Python,
TypeScript, JavaScript and Go, headings and keys for Markdown, YAML and JSON (where agents keep
their instructions and settings), and a line pattern for anything else.

A **definition** is ``{name, kind, line, end, sig}``: where it is (1-based lines) and its first
line.
Its kind is what it is to the app, guessed from its shape and name: a ``tool``, ``prompt``,
``agent``, ``route`` or ``state`` first (what an understanding must cover), else ``function``,
``method``, ``class``, ``constant``, ``section`` or ``config``. A **reference** is
``[name, definition it is used in]``: the calls a definition makes, and the capitalized names it
uses (constants, classes, types), which is how a prompt reaches the code that sends it. Names
resolve to definitions only when the map is read (:mod:`.graph`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import cache
from pathlib import PurePosixPath
from typing import Any

import tree_sitter
import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript

#: Bumped when what a parse gives changes, so cached maps are made again.
VERSION = 1
SIG_CHARS = 160
REFS_PER_FILE = 3_000
#: A string this long, held in a module-level name, is taken for a prompt whatever its name.
PROMPT_CHARS = 200
#: The kinds an understanding must cover.
BEHAVIOUR = ("tool", "prompt", "agent", "route", "state")

_PROMPTISH = re.compile(r"prompt|instruction|system|persona|template", re.IGNORECASE)
_AGENTISH = re.compile(r"agent|assistant|planner|orchestrat", re.IGNORECASE)
_STATEISH = re.compile(r"memory|store|state|session|cache|history|repo(sitory)?$|db$", re.I)
_TOOLISH = re.compile(r"tool", re.IGNORECASE)
_ROUTE_CALL = re.compile(r"^(get|post|put|patch|delete|route|api_route|websocket)$")
_ROUTE_DECORATOR = re.compile(r"\.(get|post|put|patch|delete|route|api_route|websocket)\(")
_AGENT_FILE = re.compile(r"agent|prompt|skill|instruction|system|claude|persona", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_YAML_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:")
_FALLBACK = re.compile(
    r"^\s*(?:export\s+|pub\s+|public\s+|private\s+|static\s+|async\s+)*"
    r"(def|function|func|fn|class|struct|interface|trait|module|sub|proc)\s+([A-Za-z_]\w*)"
)


@cache
def _parser(suffix: str) -> tree_sitter.Parser | None:
    language: Callable[[], Any] | None = {
        ".py": tree_sitter_python.language,
        ".ts": tree_sitter_typescript.language_typescript,
        ".mts": tree_sitter_typescript.language_typescript,
        ".cts": tree_sitter_typescript.language_typescript,
        ".tsx": tree_sitter_typescript.language_tsx,
        ".js": tree_sitter_javascript.language,
        ".mjs": tree_sitter_javascript.language,
        ".cjs": tree_sitter_javascript.language,
        ".jsx": tree_sitter_javascript.language,
        ".go": tree_sitter_go.language,
    }.get(suffix)
    return tree_sitter.Parser(tree_sitter.Language(language())) if language else None


def parse(path: str, text: str) -> dict[str, list]:
    """``{"defs": [...], "refs": [...]}`` of the file at *path* holding *text*."""
    suffix = PurePosixPath(path).suffix.lower()
    parser = _parser(suffix)
    if parser is not None:
        return _Tree(text).walk(parser.parse(text.encode("utf-8", "replace")).root_node)
    if suffix in (".md", ".mdx"):
        return _markdown(path, text)
    if suffix in (".yaml", ".yml"):
        return _keys(
            path, text, [m[1] for line in text.splitlines() if (m := _YAML_KEY.match(line))]
        )
    if suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        return _keys(path, text, list(data) if isinstance(data, dict) else [])
    return _fallback(text)


def _sig(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:SIG_CHARS]


def _named_kind(base: str, name: str) -> str:
    """What a class or name is to the app, by its name."""
    if _AGENTISH.search(name) and base in ("class", "constant", "variable"):
        return "agent"
    if _STATEISH.search(name) and base in ("class", "constant", "variable"):
        return "state"
    if _TOOLISH.search(name) and base in ("class", "constant", "variable"):
        return "tool"
    return "constant" if base == "variable" else base


class _Tree:
    """One walk of a tree-sitter tree: definitions as they open, references inside them."""

    _FUNCTIONS = frozenset(
        {
            "function_definition",
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "method_declaration",
        }
    )
    _CLASSES = frozenset(
        {
            "class_definition",
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "type_spec",
        }
    )
    _VARIABLES = frozenset({"variable_declarator", "assignment", "const_spec", "var_spec"})
    _CALLS = frozenset({"call", "call_expression"})
    _STRINGS = frozenset(
        {"string", "template_string", "interpreted_string_literal", "raw_string_literal"}
    )

    def __init__(self, text: str) -> None:
        self.text = text
        self.defs: list[dict[str, Any]] = []
        self.refs: set[tuple[str, str | None]] = set()

    def _text(self, node: Any) -> str:
        return node.text.decode("utf-8", "replace") if node is not None else ""

    def _add(self, name: str, kind: str, node: Any, sig_node: Any | None = None) -> str:
        self.defs.append(
            {
                "name": name,
                "kind": kind,
                "line": node.start_point[0] + 1,
                "end": node.end_point[0] + 1,
                "sig": _sig(self._text(sig_node or node)),
            }
        )
        return name

    def walk(self, root: Any) -> dict[str, list]:
        self._visit(root, None, None, ())
        refs = sorted(self.refs, key=lambda r: (r[1] or "", r[0]))[:REFS_PER_FILE]
        return {"defs": self.defs, "refs": [list(r) for r in refs]}

    def _decorators(self, node: Any) -> list[str]:
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            return [self._text(c) for c in parent.children if c.type == "decorator"]
        return []

    def _visit(self, node: Any, inside: str | None, cls: str | None, scopes: tuple) -> None:
        kind = node.type
        if kind in self._FUNCTIONS:
            inside = self._function(node, cls, scopes) or inside
            scopes = (*scopes, "function")
        elif kind in self._CLASSES:
            name = self._text(node.child_by_field_name("name"))
            if name:
                inside = cls = self._add(name, _named_kind("class", name), node)
            scopes = (*scopes, "class")
        elif kind in self._VARIABLES and not scopes:
            name = self._variable(node)
            if name:
                inside = name
                if self.defs[-1]["kind"] == "function":
                    scopes = (*scopes, "function")
        elif kind in self._CALLS:
            self._call(node, inside, scopes)
        elif kind in ("identifier", "type_identifier") and inside:
            name = self._text(node)
            if name[:1].isupper() and name != inside:
                self.refs.add((name, inside))
        for child in node.children:
            self._visit(child, inside, cls, scopes)

    def _function(self, node: Any, cls: str | None, scopes: tuple) -> str | None:
        name = self._text(node.child_by_field_name("name"))
        if not name or "function" in scopes:
            return None
        decorators = self._decorators(node)
        said = " ".join(decorators)
        if any(_ROUTE_DECORATOR.search(d) for d in decorators):
            kind = "route"
        elif _TOOLISH.search(said):
            kind = "tool"
        else:
            kind = (
                "method"
                if cls or node.type in ("method_definition", "method_declaration")
                else "function"
            )
        start = node.parent if decorators else node
        return self._add(name, kind, start, sig_node=node)

    def _variable(self, node: Any) -> str | None:
        target = node.child_by_field_name("name") or node.child_by_field_name("left")
        value = node.child_by_field_name("value") or node.child_by_field_name("right")
        name = self._text(target)
        if not name or not re.fullmatch(r"[A-Za-z_]\w*", name):
            return None
        if value is not None and value.type == "expression_list" and value.named_children:
            value = value.named_children[0]
        shape = value.type if value is not None else ""
        if shape in ("arrow_function", "function_expression", "function", "lambda"):
            return self._add(name, "function", node)
        if shape in self._STRINGS or shape == "concatenated_string":
            long = len(self._text(value)) >= PROMPT_CHARS
            if long or _PROMPTISH.search(name):
                return self._add(name, "prompt", node)
        if shape in self._CALLS:
            called = self._text(value.child_by_field_name("function"))
            if _TOOLISH.search(called.rsplit(".", 1)[-1]):
                return self._add(name, "tool", node)
            if _AGENTISH.search(called):
                return self._add(name, "agent", node)
        if name.isupper() or _named_kind("variable", name) != "constant":
            return self._add(name, _named_kind("variable", name), node)
        return None

    def _call(self, node: Any, inside: str | None, scopes: tuple) -> None:
        function = node.child_by_field_name("function")
        if function is None:
            return
        last = (
            function.child_by_field_name("property")
            or function.child_by_field_name("attribute")
            or function.child_by_field_name("field")
        )
        called = self._text(last if last is not None else function)
        decorated = node.parent is not None and node.parent.type == "decorator"
        if not scopes and inside is None and not decorated and _ROUTE_CALL.match(called):
            arguments = node.child_by_field_name("arguments")
            first = arguments.named_children[0] if arguments and arguments.named_children else None
            if first is not None and first.type in self._STRINGS:
                path = self._text(first).strip("'\"`")
                if path.startswith("/"):
                    self._add(f"{called.upper()} {path}", "route", node)
                    for child in arguments.named_children[1:]:
                        if child.type == "identifier":
                            self.refs.add((self._text(child), f"{called.upper()} {path}"))
                    return
        if inside and re.fullmatch(r"[A-Za-z_]\w*", called):
            self.refs.add((called, inside))


def _markdown(path: str, text: str) -> dict[str, list]:
    """Headings as definitions: a section of an agent's instructions is a prompt."""
    kind = "prompt" if _AGENT_FILE.search(path) else "section"
    lines = text.splitlines()
    heads = [(n + 1, m[2]) for n, line in enumerate(lines) if (m := _HEADING.match(line))]
    defs = [
        {
            "name": title[:80],
            "kind": kind,
            "line": line,
            "end": (heads[i + 1][0] - 1) if i + 1 < len(heads) else len(lines),
            "sig": f"# {title}"[:SIG_CHARS],
        }
        for i, (line, title) in enumerate(heads)
    ]
    return {"defs": defs, "refs": []}


def _keys(path: str, text: str, keys: list[str]) -> dict[str, list]:
    """Top-level keys of a settings file; those that hold an agent's instructions, tools or agents
    are those kinds."""
    lines = text.splitlines()
    agentish = _AGENT_FILE.search(path) is not None
    defs = []
    for key in keys[:200]:
        at = next(
            (
                n + 1
                for n, line in enumerate(lines)
                if re.match(rf"\s*\"?{re.escape(key)}\"?\s*:", line)
            ),
            1,
        )
        low = key.lower()
        if _PROMPTISH.search(low):
            kind = "prompt"
        elif low.startswith("tool"):
            kind = "tool"
        elif _AGENTISH.search(low) or (agentish and low in ("name", "model")):
            kind = "agent"
        else:
            kind = "config"
        defs.append(
            {
                "name": key,
                "kind": kind,
                "line": at,
                "end": at,
                "sig": lines[at - 1].strip()[:SIG_CHARS] if lines else key,
            }
        )
    return {"defs": defs, "refs": []}


def _fallback(text: str) -> dict[str, list]:
    defs = []
    for n, line in enumerate(text.splitlines(), 1):
        if m := _FALLBACK.match(line):
            base = (
                "class"
                if m[1] in ("class", "struct", "interface", "trait", "module")
                else "function"
            )
            defs.append(
                {
                    "name": m[2],
                    "kind": _named_kind(base, m[2]),
                    "line": n,
                    "end": n,
                    "sig": line.strip()[:SIG_CHARS],
                }
            )
    return {"defs": defs, "refs": []}
