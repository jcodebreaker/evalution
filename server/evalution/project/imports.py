"""Which watched file reaches which (docs/architecture.md, "Keeping what it knows current").

A best-effort import graph, cheap and tolerant: Python through :mod:`ast`; JS, TS, Go and Markdown
links through careful patterns, resolved against the files that exist. Another language has no
edges, and a file that does not parse has none either: never an error. What a file imports is
cached by its hash, so only a file that moved is read again.
"""

from __future__ import annotations

import ast
import posixpath
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from evalution.project.profile import read_doc, write_doc
from evalution.targets import Targets

#: The graph of a target's code, kept between reads (:func:`kept_graph`).
DOC = "imports.json"
#: Steps through imports that still count as resting on a file.
HOPS = 2
#: Bumped when what is read out of a file changes, so old cache entries are not used.
_RULES = "2"
_PARSE_CAP = 300_000
_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_MD = (".md", ".mdx")
_JS_FROM = re.compile(
    r"""^\s*(?:import|export)\b(?:[^'"`;]{0,1000}?\bfrom\s*)?\s*['"]([^'"\n]{1,300})['"]""", re.M
)
_JS_CALL = re.compile(r"""\b(?:require|import)\(\s*['"]([^'"\n]{1,300})['"]\s*\)""")
_GO_ONE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"\n]{1,300})"', re.M)
_GO_BLOCK = re.compile(r"^\s*import\s*\(([^)]{0,20000})\)", re.M)
_GO_PATH = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"\n]{1,300})"', re.M)
_GO_MODULE = re.compile(r"^\s*module\s+(\S+)", re.M)
_MD_LINK = re.compile(r"\]\(([^)\s#]{1,300})")
_MD_CODE = re.compile(r"`([^`\s]{1,300}\.\w{1,8})`")
#: A module loaded by name, not imported (a harness or executor in a registry, an
#: ``importlib.import_module`` argument): a string that is a whole dotted module path, perhaps
#: with ``:attribute``, or a whole relative path to a Python file. It is an edge only when it
#: names one file of the repository, by two parts of its path or more (:meth:`_Files.named`).
DYNAMIC = "named:"
_NAMED_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?::[A-Za-z_][\w.]*)?")
_NAMED_FILE = re.compile(r"(?:[\w.-]+/)+[\w-]+\.py")
_NAMED_CAP = 100


def _no_js_comments(text: str) -> str:
    """Without comment lines, so an import commented out is not one. Only whole lines: ``/*``
    inside a string (a glob) must not hide what follows."""
    kept, block = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if block or stripped.startswith("/*"):
            block = "*/" not in stripped
        elif not stripped.startswith("//"):
            kept.append(line)
    return "\n".join(kept)


def _named(value: object) -> bool:
    return isinstance(value, str) and bool(
        _NAMED_MODULE.fullmatch(value) or _NAMED_FILE.fullmatch(value)
    )


def _python_specs(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return []
    found = []
    named = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            found += [f"{module}:{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Constant) and _named(node.value):
            named.append(DYNAMIC + node.value)
    return found + named[:_NAMED_CAP]


def specs(rel: str, text: str) -> list[str]:
    """What a file names as its imports, as written; nothing for a language not read here."""
    suffix = PurePosixPath(rel).suffix.lower()
    if suffix == ".py":
        found = _python_specs(text)
    elif suffix in _JS:
        clean = _no_js_comments(text[:_PARSE_CAP])
        found = _JS_FROM.findall(clean) + _JS_CALL.findall(clean)
    elif suffix == ".go":
        clean = text[:_PARSE_CAP]
        found = _GO_ONE.findall(clean)
        for block in _GO_BLOCK.findall(clean):
            found += _GO_PATH.findall(block)
    elif suffix in _MD:
        clean = text[:_PARSE_CAP]
        found = _MD_LINK.findall(clean) + _MD_CODE.findall(clean)
    else:
        found = []
    return list(dict.fromkeys(found))


class _Files:
    """The files imports resolve against."""

    def __init__(self, known: Iterable[str], modules: Mapping[str, str]) -> None:
        self.known = set(known)
        self.modules = modules
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.by_folder: dict[str, list[str]] = defaultdict(list)
        self._python_seen: dict[tuple[str, str], list[str]] = {}
        for rel in self.known:
            path = PurePosixPath(rel)
            self.by_name[path.name].append(rel)
            self.by_folder[str(path.parent)].append(rel)

    def _python_at(self, base: PurePosixPath, parts: list[str], name: str) -> list[str]:
        module = base.joinpath(*parts)
        if name and name != "*":
            for found in (module / f"{name}.py", module / name / "__init__.py"):
                if str(found) in self.known:
                    return [str(found)]
        candidates = [module / "__init__.py"]
        if parts:
            candidates.insert(0, module.with_name(f"{parts[-1]}.py"))
        return [str(c) for c in candidates if str(c) in self.known][:1]

    def python(self, rel: str, spec: str) -> list[str]:
        # What a spec means depends on the importing file's folder alone: asked once per folder.
        key = (posixpath.dirname(rel), spec)
        if key not in self._python_seen:
            self._python_seen[key] = self._python(rel, spec)
        return self._python_seen[key]

    def _python(self, rel: str, spec: str) -> list[str]:
        module, _, name = spec.partition(":")
        bare = module.lstrip(".")
        level = len(module) - len(bare)
        parts = bare.split(".") if bare else []
        here = PurePosixPath(rel).parent
        if level:
            for _ in range(level - 1):
                if str(here) == ".":
                    return []
                here = here.parent
            return self._python_at(here, parts, name)
        for base in (here, *here.parents):
            if found := self._python_at(base, parts, name):
                return found
        if len(parts) < 2:
            return []
        # A package under a folder the importing file is not in: only when it can be nothing else.
        ending = "/" + "/".join(parts) + ".py"
        found = [f for f in self.by_name[f"{parts[-1]}.py"] if f.endswith(ending)]
        return found if len(found) == 1 else []

    def relative(self, rel: str, spec: str) -> str | None:
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
        return None if joined == ".." or joined.startswith("../") else joined

    def js(self, rel: str, spec: str) -> list[str]:
        if not spec.startswith("."):
            return []
        base = self.relative(rel, spec)
        if base is None:
            return []
        stem, suffix = posixpath.splitext(base)
        candidates = [base, *(base + ext for ext in _JS), *(f"{base}/index{ext}" for ext in _JS)]
        if suffix in _JS:
            # TypeScript imports `./a.js` for what is `a.ts` on disk.
            candidates += [stem + ext for ext in _JS]
        return [c for c in candidates if c in self.known][:1]

    def go(self, rel: str, spec: str) -> list[str]:
        folder = None
        if spec.startswith("."):
            folder = self.relative(rel, spec)
        else:
            for root, module in self.modules.items():
                if spec == module or spec.startswith(module + "/"):
                    folder = posixpath.normpath(posixpath.join(root, spec[len(module) + 1 :]))
                    break
        if folder is None:
            return []
        return [f for f in self.by_folder.get(folder, []) if f.endswith(".go")]

    def markdown(self, rel: str, spec: str) -> list[str]:
        if "://" in spec or spec.startswith("mailto:"):
            return []
        near = self.relative(rel, spec)
        rooted = posixpath.normpath(spec.lstrip("/"))
        return [c for c in (near, rooted) if c in self.known][:1]

    def named(self, spec: str) -> list[str]:
        """The one file a module path or a file path written in a string is, if it is one."""
        if "/" in spec:
            path = posixpath.normpath(spec)
            return [path] if path in self.known else []
        parts = spec.partition(":")[0].split(".")
        module = "/".join(parts)
        found = [
            f
            for f in [*self.by_name[f"{parts[-1]}.py"], *self.by_name["__init__.py"]]
            for end in (f"{module}.py", f"{module}/__init__.py")
            if f == end or f.endswith("/" + end)
        ]
        return found if len(found) == 1 else []

    def resolve(self, rel: str, spec: str) -> list[str]:
        suffix = PurePosixPath(rel).suffix.lower()
        found: list[str] = []
        if spec.startswith(DYNAMIC):
            found = self.named(spec.removeprefix(DYNAMIC))
        elif suffix == ".py":
            found = self.python(rel, spec)
        elif suffix in _JS:
            found = self.js(rel, spec)
        elif suffix == ".go":
            found = self.go(rel, spec)
        elif suffix in _MD:
            found = self.markdown(rel, spec)
        return [f for f in found if f != rel]


def _read(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def graph(
    root: Path, files: Mapping[str, str], cached: Mapping[str, Any], removed: Iterable[str] = ()
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """``(file -> the files it imports, the cache to keep)`` over *files* (``path -> hash``, the
    watched ones, so each is known to be readable source). *cached* is what an earlier call gave;
    *removed* files still count as something to import, so a claim reaches a deleted file."""
    old = cached.get("specs") or {} if cached.get("rules") == _RULES else {}
    named: dict[str, list[str]] = {}
    kept: dict[str, list[str]] = {}
    for rel, digest in files.items():
        key = f"{PurePosixPath(rel).suffix.lower()}:{digest}"
        if key in old:
            found = old[key]
        else:
            text = _read(root, rel)
            found = specs(rel, text) if text is not None else []
        named[rel] = kept[key] = found
    modules = {}
    for rel in files:
        if PurePosixPath(rel).name == "go.mod":
            match = _GO_MODULE.search(_read(root, rel) or "")
            if match:
                modules[posixpath.dirname(rel) or "."] = match.group(1)
    known = _Files([*files, *removed], modules)
    edges = {
        rel: sorted({hit for spec in found for hit in known.resolve(rel, spec)})
        for rel, found in named.items()
    }
    return {rel: hits for rel, hits in edges.items() if hits}, {"rules": _RULES, "specs": kept}


def reach(edges: Mapping[str, Iterable[str]], start: Iterable[str], hops: int = HOPS) -> set[str]:
    """The files *start* imports within *hops* steps, *start* itself left out."""
    begin = set(start)
    seen, front = set(begin), begin
    for _ in range(hops):
        front = {hit for rel in front for hit in edges.get(rel, ())} - seen
        if not front:
            break
        seen |= front
    return seen - begin


def kept_graph(
    targets: Targets, name: str, root: Path, files: Mapping[str, str], removed: Iterable[str] = ()
) -> dict[str, list[str]]:
    """:func:`graph` of target *name*, its cache kept in :data:`DOC`, so a file is read again only
    when it moved, whoever asks: the knowledge refresh and the code map."""
    cached = read_doc(targets, name, DOC)
    edges, keep = graph(root, files, cached if isinstance(cached, dict) else {}, removed)
    write_doc(targets, name, DOC, keep)
    return edges
