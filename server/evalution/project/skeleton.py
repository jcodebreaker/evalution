"""The app's skeleton (ADR 0012, docs/plans/learn-on-demand.md "Phase 1"): what it is built from,
gathered from the code map with no model. Its entry points, its prompts (with their text when a
string holds it), its tools (with their arguments when the definition shows them), where it calls
a model (and which, when the code names it), its routes, where it keeps state, and its agents;
each with the file and line it is at.

:func:`skeleton` reads the code map's definitions and facts first, and a file's text only for
what the map doesn't keep (a prompt's whole string, a tool's arguments over several lines, model
calls, stores and entry points, which are calls rather than definitions). A file's entries depend
on that file alone, so a skeleton made before is reused file by file, by fingerprint.

:func:`match` joins real use to it: a tool span by its name, a system prompt by its hash, its
sections' or its first words, a model by its name, a route by its path. What real use names and
the code doesn't is listed apart: seen in use, not found in the code.

Both are pure: the code map and the text in, the skeleton out, in a stable order.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from evalution.project.codemap.graph import CodeMap, Definition
from evalution.project.codemap.parse import VERSION as PARSE_VERSION

#: Bumped when what a skeleton holds changes, so kept ones are made again.
VERSION = 2
#: The kinds of entry, in the order they are shown.
KINDS = ("entry", "prompt", "tool", "model", "route", "state", "agent")
#: How much of a prompt's text an entry keeps.
TEXT_CHARS = 600
#: How far past a call's opening the arguments are read for a model's name.
CALL_CHARS = 2_000
#: The first words of a prompt matched to the start of one seen in use.
HEAD_WORDS = 8
#: Fewer characters than this at a prompt's start say too little to match on.
HEAD_MIN = 24
#: A string this dense in code punctuation is markup or code, not a prompt.
CODE_DENSITY = 0.03

_CODE = frozenset({".py", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".go"})
_JS = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})
_SETTINGS = frozenset({".yaml", ".yml", ".json"})
_MARKDOWN = frozenset({".md", ".mdx"})
_PROMPTISH = re.compile(r"prompt|instruction|system|persona|template", re.I)
_AGENT_FILE = re.compile(r"agent|prompt|skill|instruction|system|claude|persona", re.I)
#: Folders whose Markdown is what a model is told: a prompt, an agent's or skill's instructions.
_PROMPT_FOLDERS = frozenset(
    {
        "prompt",
        "prompts",
        "agent",
        "agents",
        "template",
        "templates",
        "instruction",
        "instructions",
        "persona",
        "personas",
        "skill",
        "skills",
        "system",
    }
)
#: Markdown that is a prompt by its own name, wherever it is.
_PROMPT_FILE = re.compile(
    r"^(agents?|claude|gemini|skill|system|persona)\.mdx?$|prompt|instruction", re.I
)
#: Markdown that is never a prompt: written for people.
_PEOPLE_FOLDERS = frozenset(
    {"docs", "doc", "adr", "adrs", "decisions", "plans", "rfcs", "changelog", "changes"}
)
_PEOPLE_FILE = re.compile(
    r"^(readme|changelog|changes|history|license|contributing|code_of_conduct|security)\b", re.I
)
#: A file a string names, which may hold a prompt: ``prompts/system.md``, ``brief.j2``.
_MENTION = re.compile(r"""["'`]([\w./-]*?([\w-]+\.(?:md|mdx|txt|j2|jinja2?|prompt|tmpl)))["'`]""")
#: Where a tool is defined, best first: its definition, then a use by name, then an agent's
#: settings listing it.
_TOOL_RANK = {"call": 1, "setting": 2}
#: Keys that hold a model's id in a settings file.
_MODEL_KEY = re.compile(
    r"""^\s*["']?(?P<key>(?:\w+_)?model(?:_name|_id)?|(?:\w+)?Model(?:Name|Id)?)["']?\s*[:=]\s*"""
    r"""["']?(?P<id>[A-Za-z][\w.:/@-]*)["']?\s*,?\s*(?:#.*)?$"""
)
_MODEL_ID = re.compile(
    r"\d|gpt|claude|gemini|llama|mistral|qwen|sonnet|opus|haiku|o1|o3|deepseek|grok", re.I
)
#: Under these keys an agent's tool settings list what isn't a tool it uses: tools it may not
#: use, and the agents it may start.
_DENIED = re.compile(
    r"^\s*(deny|denied|block|blocked|disallow\w*|exclude\w*|agents|sub_?agents|helpers|"
    r"delegates)\s*:",
    re.I,
)
#: A Dockerfile's command.
_DOCKER_CMD = re.compile(r"^\s*(CMD|ENTRYPOINT)\s+(.+?)\s*$", re.M)
_APP_RETURN = re.compile(
    r"""return\s+(?:new\s+)?(?P<call>FastAPI|Flask|Quart|Starlette|Sanic|Litestar|express|Hono|"""
    r"""Fastify|fastify|Koa)\("""
)
_UVICORN_TARGET = re.compile(r"""uvicorn\.run\(\s*["']([\w.]+:[\w.()]+)["']""")
_TEST_FILE = re.compile(
    r"(^|/)(tests?|testing|__tests__)/|(^|/)(test_[^/]*|[^/]*_test\.\w+|conftest\.py)$"
)
_TYPE_SIG = re.compile(r"^\s*(export\s+)?(declare\s+)?(interface|type)\s")
_ENV_READ = re.compile(r"os\.environ|getenv|process\.env|Getenv")
_SCALAR = re.compile(r"=\s*(-?[\d_.]+|True|False|None|true|false|null|\"[^\"]*\"|'[^']*')\s*$")
_NAME_IN = re.compile(r"""\bname\s*(?::\s*\w+\s*)?[=:]\s*["']([\w.\-/ ]+)["']""")
_SCHEMA_NAME = re.compile(r"""["']?name["']?\s*:\s*["']([\w.\-]+)["']""")
_LIST_ITEM = re.compile(r"""^\s*-\s*(?:name\s*:\s*)?["']?([\w.\-]+)["']?\s*$""")
_LIST_NAME = re.compile(r"""^\s*-?\s*name\s*:\s*["']?([\w.\-]+)""")
#: A tool class's own name: ``def name(cls): return "list_files"``, or ``name = "list_files"``.
_CLASS_TOOL_NAME = (
    re.compile(
        r"""def\s+(?:get_)?name\s*\([^)]*\)[^:]*:\s*(?:(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?''')\s*)?"""
        r"""return\s+["']([\w.\-]+)["']"""
    ),
    re.compile(
        r"""^[ \t]+(?:name|NAME|tool_name)\s*(?::\s*[\w\[\]]+\s*)?=\s*["']([\w.\-]+)["']""", re.M
    ),
)
_ZOD_KEYS = re.compile(r"\b(\w+)\s*:\s*z\.(?!object\b)")
_STATE_WORDS = (
    ("memory", "memory"),
    ("session", "sessions"),
    ("cache", "cache"),
    ("history", "history"),
    ("checkpoint", "checkpoints"),
    ("repo", "database"),
    ("db", "database"),
    ("store", "store"),
    ("state", "state"),
)

#: Calls on a model client, by the call's own name (the receiver's dots included).
_MODEL_CALLS = re.compile(
    r"""(?x)
    (?P<call>
        \.chat\.completions\.(?:create|parse|stream)
      | \.(?:beta\.)?messages\.(?:create|stream|parse)
      | \.responses\.(?:create|parse|stream)
      | (?<!chat)\.completions\.create
      | \.models\.generate_content(?:_stream)?
      | \.generate_content(?:_async)?
      | \.generateContent(?:Stream)?
      | \blitellm\.a?completion
      | \b(?:generateText|streamText|generateObject|streamObject)
      | \.Chat\.Completions\.New(?:Streaming)?
      | \.Messages\.New(?:Streaming)?
      | \.CreateChatCompletion(?:Stream)?
      | \b\w*(?:llm|model|chat)\w*\.a?(?:invoke|stream)
    )\s*\(""",
    re.I,
)
#: Where a model client is set up, the model often named there.
_MODEL_CLIENTS = re.compile(
    r"\b(?P<call>Chat(?:OpenAI|Anthropic|GoogleGenerativeAI|VertexAI|Bedrock|Ollama|Mistral\w*|"
    r"Groq)|AzureChatOpenAI|init_chat_model|LiteLLMModel|OpenAIChatCompletionsModel)\s*\("
)
_MODEL_NAMED = (
    re.compile(
        r"""\b(?:model|model_name|modelName|model_id|modelId|Model)\s*[=:]\s*["']([^"'\s]+)["']"""
    ),
    re.compile(r"""\bmodel\s*:\s*\w+(?:\.\w+)?\(\s*["']([^"'\s]+)["']"""),
    re.compile(r"""^\s*["']([^"'\s]+)["']"""),
)
#: litellm's ``completion(...)``, imported bare.
_BARE_COMPLETION = re.compile(r"(?<![\w.])(?P<call>a?completion)\s*\(")
#: What a call's model comes from when it isn't written there: ``model=settings.chat_model``.
_MODEL_FROM = re.compile(
    r"""\bmodel(?:_name)?\s*[=:]\s*([A-Za-z_][\w.]*(?:\[["'][\w.-]+["']\])?)"""
)
_MODEL_CONST = re.compile(
    r"""\b(?:model|model_name|modelName|Model)\s*[=:]\s*([A-Z][A-Z0-9_]*)\b"""
)

#: Stores, by the call that opens one: what kind of state it is.
_STORES = (
    (
        "database",
        re.compile(
            r"\b(?P<call>create_(?:async_)?engine|sqlite3\.connect|aiosqlite\.connect|"
            r"psycopg2?\.connect|asyncpg\.(?:connect|create_pool)|(?:AsyncIOMotor|Mongo)Client|"
            r"PrismaClient|drizzle|sql\.Open|pgxpool\.New|gorm\.Open|new Sequelize|"
            r"mysql\.create(?:Connection|Pool)|duckdb\.connect)\s*\("
        ),
    ),
    (
        "cache",
        re.compile(r"\b(?P<call>redis\.(?:Redis|StrictRedis|from_url)|new Redis|Redis)\s*\("),
    ),
    (
        "vector store",
        re.compile(
            r"\b(?P<call>chromadb\.\w*Client|Chroma|Pinecone|QdrantClient|FAISS\.\w+|PGVector|"
            r"lancedb\.connect|weaviate\.\w*connect\w*)\s*\("
        ),
    ),
    (
        "checkpoints",
        re.compile(r"\b(?P<call>(?:Async)?(?:Sqlite|Postgres|Redis)Saver\S*|MemorySaver)\s*\("),
    ),
    ("memory", re.compile(r"\b(?P<call>Conversation\w*Memory|MemoryClient|Mem0\w*)\s*\(")),
)
#: Calls that use a tool by name, or register one (an MCP server's ``tool("name", ...)``).
_TOOL_CALLS = re.compile(
    r"""\.(?P<call>tool|registerTool|call_tool|callTool|run_tool|runTool|invoke_tool|use_tool|"""
    r"""execute_tool)\(\s*["'`](?P<name>[\w.\-]+)["'`]"""
)
_SERVER_APP = re.compile(
    r"""^\s*(?:export\s+)?(?:const\s+|let\s+|var\s+)?(?P<name>\w+)\s*(?::\s*[\w.]+\s*)?:?=\s*"""
    r"""(?:new\s+)?(?P<call>FastAPI|Flask|Quart|Starlette|Sanic|Litestar|express|Hono|Elysia|"""
    r"""Fastify|fastify|Koa|gin\.(?:Default|New)|echo\.New|http\.NewServeMux|chi\.NewRouter|"""
    r"""mux\.NewRouter)\(""",
    re.M,
)
_SERVER_START = re.compile(
    r"\b(?P<call>uvicorn\.run|http\.ListenAndServe(?:TLS)?|\w+\.listen|Bun\.serve)\("
)
_MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*["']__main__["']\s*:""", re.M)
_ROUTE_DECORATOR = re.compile(
    r"""\.(?P<verb>get|post|put|patch|delete|route|api_route|websocket)\(\s*["'](?P<path>[^"']*)["']"""
    r"""(?P<rest>[^\n]*)"""
)
_ROUTE_METHODS = re.compile(r"""methods\s*=\s*\[\s*["'](\w+)["']""")
_JS_ROUTE = re.compile(
    r"""\b(?:app|router|server|api|routes?)\.(?P<verb>get|post|put|patch|delete|all)\(\s*"""
    r"""["'`](?P<path>/[^"'`]*)["'`]"""
)
_GO_ROUTE = re.compile(
    r"""\.(?P<verb>HandleFunc|Handle|GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete)\(\s*"""
    r""""(?P<path>(?:[A-Z]+\s+)?/[^"]*)\""""
)
_FILE_ROUTE = re.compile(r"(?:^|/)app/(?P<dirs>(?:[^/]+/)*)route\.(?:ts|js|tsx|jsx|mts|mjs)$")
_VERBS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_LITERAL = re.compile(
    r"""(?:\b[rRbBfFuU]{1,2})?(\"\"\"|'''|"|'|`)((?:\\.|(?!\1)[\s\S])*?)\1""",
)


@dataclass(frozen=True)
class Entry:
    """One part of the app, at *path*:*line*. *what* says how it was found or what it is (a
    store's kind, the client a model is called through, ``call`` for a tool used by name);
    *inside* the definition it sits in; *text* a prompt's text, capped; *args* a tool's
    arguments; *model* the model named, or *model_from* the setting or name it comes from;
    *hashes* a prompt's, to match it to one sent. Folded entries (one tool listed by many agents,
    one prompt kept in many copies) hold the other *places* (``path:line``) and, for a tool, how
    many agents' settings list it (*agents*)."""

    kind: str
    name: str
    path: str
    line: int
    what: str = ""
    inside: str = ""
    text: str = ""
    args: tuple[str, ...] = ()
    model: str = ""
    hashes: tuple[str, ...] = ()
    model_from: str = ""
    places: tuple[str, ...] = ()
    agents: int = 0

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.path}:{self.line}:{self.name}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            **asdict(self),
            "args": list(self.args),
            "hashes": list(self.hashes),
            "places": list(self.places),
        }

    @classmethod
    def of(cls, body: Mapping[str, Any]) -> Entry:
        return cls(
            kind=str(body["kind"]),
            name=str(body["name"]),
            path=str(body["path"]),
            line=int(body["line"]),
            what=str(body.get("what") or ""),
            inside=str(body.get("inside") or ""),
            text=str(body.get("text") or ""),
            args=tuple(body.get("args") or ()),
            model=str(body.get("model") or ""),
            hashes=tuple(body.get("hashes") or ()),
            model_from=str(body.get("model_from") or ""),
            places=tuple(body.get("places") or ()),
            agents=int(body.get("agents") or 0),
        )


def _order(entry: Entry) -> tuple:
    return (KINDS.index(entry.kind), entry.path, entry.line, entry.name, entry.what)


@dataclass(frozen=True)
class Skeleton:
    """The entries, by kind then file and line, folded (a name in many places is one entry);
    *raw*: each file's own entries before folding, and *mentions*: the files each file's strings
    name, so a file that didn't move is reused; *prints*: each file's fingerprint;
    *fingerprint*: of them all."""

    fingerprint: str
    entries: tuple[Entry, ...]
    prints: Mapping[str, str] = field(default_factory=dict)
    raw: tuple[Entry, ...] = ()
    mentions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def of(self, kind: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == kind]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "fingerprint": self.fingerprint,
            "raw": [e.as_dict() for e in self.raw],
            "mentions": {p: list(m) for p, m in self.mentions.items()},
            "prints": dict(self.prints),
        }

    @classmethod
    def of_dict(cls, body: Any) -> Skeleton | None:
        """A kept skeleton; ``None`` for one kept by another version, or nothing."""
        if not isinstance(body, dict) or body.get("version") != VERSION:
            return None
        raw = tuple(Entry.of(e) for e in body.get("raw") or ())
        mentions = {str(p): tuple(m) for p, m in (body.get("mentions") or {}).items()}
        return cls(
            str(body.get("fingerprint") or ""),
            _assemble(raw, mentions),
            dict(body.get("prints") or {}),
            raw,
            mentions,
        )


def fingerprint_of(prints: Mapping[str, str]) -> str:
    """The fingerprint of a skeleton of files with these *prints*."""
    body = json.dumps([VERSION, PARSE_VERSION, sorted(prints.items())])
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _print(parsed: Mapping[str, Any]) -> str:
    found = parsed.get("fingerprint")
    if isinstance(found, str) and found:
        return found
    body = json.dumps({k: parsed.get(k) for k in ("defs", "facts")}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def skeleton(
    cmap: CodeMap,
    read: Callable[[str], str] | None = None,
    kept: Skeleton | None = None,
) -> Skeleton:
    """The skeleton of the app *cmap* maps. *read* gives a file's text for what the map doesn't
    keep (without it, only what the map holds); a file whose fingerprint is as in *kept* keeps
    its entries from there, unread."""
    by_path: dict[str, list[Definition]] = {}
    for d in cmap.defs.values():
        by_path.setdefault(d.path, []).append(d)
    reused: dict[str, list[Entry]] = {}
    if kept is not None:
        for e in kept.raw:
            reused.setdefault(e.path, []).append(e)
    prints: dict[str, str] = {}
    raw: list[Entry] = []
    mentions: dict[str, tuple[str, ...]] = {}
    for path in sorted(cmap.files):
        parsed = cmap.files[path]
        prints[path] = _print(parsed)
        if kept is not None and kept.prints.get(path) == prints[path]:
            raw += reused.get(path, [])
            if kept.mentions.get(path):
                mentions[path] = kept.mentions[path]
            continue
        defs = sorted(by_path.get(path, []), key=lambda d: (d.line, d.name))
        text = None
        if read is not None and _worth_reading(path):
            try:
                text = read(path)
            except (OSError, ValueError):
                text = None
        raw += _file(path, defs, cmap.facts(path), text)
        if text and (named := _mentions(text)):
            mentions[path] = named
    unique = {(e.kind, e.path, e.line, e.name): e for e in raw}
    kept_raw = tuple(sorted(unique.values(), key=_order))
    return Skeleton(
        fingerprint_of(prints),
        _assemble(kept_raw, mentions),
        dict(sorted(prints.items())),
        kept_raw,
        mentions,
    )


def _worth_reading(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    suffix = PurePosixPath(path).suffix.lower()
    return (
        suffix in _CODE
        or suffix in _SETTINGS
        or suffix in _MARKDOWN
        or suffix == ".toml"
        or name.startswith("dockerfile")
        or name.endswith(".dockerfile")
    )


def _mentions(text: str) -> tuple[str, ...]:
    """The file names a file's strings hold, which may be prompts it loads."""
    return tuple(sorted({m[2].lower() for m in _MENTION.finditer(text)}))


# Folding: one entry per tool, per copied prompt or agent; Markdown that is a prompt


def _prompt_file(path: str, mentioned: set[str]) -> bool:
    """Whether a Markdown file is a prompt: in a prompt's folder, named as one, or loaded by the
    code; never one written for people (docs, decisions, plans, a README)."""
    parts = PurePosixPath(path).parts
    folders = {p.lower() for p in parts[:-1]}
    name = parts[-1]
    if folders & _PEOPLE_FOLDERS or _PEOPLE_FILE.match(name):
        return False
    return bool(folders & _PROMPT_FOLDERS or _PROMPT_FILE.search(name) or name.lower() in mentioned)


def _rank(entry: Entry) -> int:
    return _TOOL_RANK.get(entry.what, 0)


def _fold(group: Sequence[Entry]) -> Entry:
    """Entries of one name as one: the best place first (a definition before a use by name, a
    use before a listing), the rest as its other places."""
    ordered = sorted(group, key=lambda e: (_rank(e), e.path, e.line))
    first = ordered[0]
    if len(ordered) == 1 and first.what != "setting":
        return first
    places = tuple(dict.fromkeys(f"{e.path}:{e.line}" for e in ordered[1:]))
    listed = {e.path for e in ordered if e.what == "setting"}
    return Entry(
        **{
            **asdict(first),
            "args": first.args or next((e.args for e in ordered if e.args), ()),
            "model": first.model or next((e.model for e in ordered if e.model), ""),
            "text": first.text or next((e.text for e in ordered if e.text), ""),
            "hashes": tuple(dict.fromkeys(h for e in ordered for h in e.hashes)),
            "places": places,
            "agents": len(listed) if first.kind == "tool" else 0,
        }
    )


def _assemble(raw: Sequence[Entry], mentions: Mapping[str, Sequence[str]]) -> tuple[Entry, ...]:
    """The entries shown, from each file's own: Markdown kept only where it is a prompt, a tool
    one entry whatever lists it, copies of one prompt, agent or model setting one entry."""
    mentioned = {m for named in mentions.values() for m in named}
    groups: dict[tuple, list[Entry]] = {}
    for e in raw:
        if e.kind == "prompt" and e.what == "file" and not _prompt_file(e.path, mentioned):
            continue
        if e.kind == "tool":
            key: tuple = ("tool", _key(e.name))
        elif e.kind == "prompt" and e.what in ("file", "setting"):
            key = ("prompt", e.name, e.hashes[:1] or e.text)
        elif e.kind == "agent" and e.what == "definition":
            key = ("agent", e.name)
        elif e.kind == "model" and e.what == "setting":
            key = ("model", e.model)
        else:
            key = (e.kind, e.path, e.line, e.name)
        groups.setdefault(key, []).append(e)
    return tuple(sorted((_fold(g) for g in groups.values()), key=_order))


# One file


def _file(
    path: str, defs: Sequence[Definition], facts: Sequence[Mapping[str, Any]], text: str | None
) -> list[Entry]:
    suffix = PurePosixPath(path).suffix.lower()
    name = PurePosixPath(path).name.lower()
    lines = text.splitlines() if text is not None else []
    out: list[Entry] = []
    if suffix in _MARKDOWN:
        return _markdown(path, defs, text)
    if suffix in _SETTINGS:
        out = _settings(path, defs, lines, text)
        if name == "package.json":
            out += _package(path, text, lines)
        return out + _model_settings(path, lines)
    if suffix == ".toml":
        return _toml(path, lines) + _model_settings(path, lines)
    if name.startswith("dockerfile") or name.endswith(".dockerfile"):
        return _dockerfile(path, text)
    constants = {d.name: _constant_value(d.sig) for d in defs if d.kind == "constant"}
    for d in defs:
        if _tool_class(d):
            out += _tool(path, d, lines)
        elif d.kind == "prompt":
            out += _prompt(path, d, lines, suffix)
        elif d.kind == "prompt-builder":
            if _PROMPTISH.search(d.name) or _PROMPTISH.search(path):
                out.append(Entry("prompt", d.name, path, d.line, what="builder"))
        elif d.kind == "tool":
            out += _tool(path, d, lines)
        elif d.kind == "route":
            out.append(_route(path, d, lines, facts))
        elif d.kind == "state":
            out += _state_def(path, d)
        elif d.kind == "agent":
            out += _agent_def(path, d, lines, constants)
        elif d.name in _VERBS and _FILE_ROUTE.search(path):
            out.append(_file_route(path, d))
        elif d.name == "main" and suffix == ".go" and d.kind == "function":
            out.append(Entry("entry", "main", path, d.line, what="main"))
    if text is not None and suffix in _CODE:
        out += _scan(path, defs, text, constants)
    return out


def _tool_class(d: Definition) -> bool:
    """A class that is a tool, whatever its name says too (``SessionListTool`` is a tool, not
    state): named ``…Tool`` or built on one, and not private or the base itself."""
    if not re.match(r"(export\s+)?(abstract\s+)?class\b", d.sig) or d.name.startswith("_"):
        return False
    if d.name in ("Tool", "BaseTool", "StructuredTool"):
        return False
    return d.name.endswith("Tool") or bool(re.search(r"\(\s*(\w+\.)?(Base)?Tool\b", d.sig))


# Manifests: entry points and models a project's own files declare


def _toml(path: str, lines: Sequence[str]) -> list[Entry]:
    """A ``pyproject.toml``'s commands: ``[project.scripts]`` and Poetry's."""
    out, section = [], ""
    for n, line in enumerate(lines, 1):
        if m := re.match(r"^\s*\[([^\]]+)\]\s*$", line):
            section = m[1].strip()
            continue
        if section in ("project.scripts", "tool.poetry.scripts") and (
            m := re.match(r"""^\s*["']?([\w.-]+)["']?\s*=\s*["']([^"']+)["']""", line)
        ):
            out.append(Entry("entry", m[1], path, n, what="command", text=m[2]))
    return out


def _package(path: str, text: str | None, lines: Sequence[str]) -> list[Entry]:
    """A ``package.json``'s ``main``, ``bin`` and ``scripts.start``."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []

    def at(key: str) -> int:
        return next((n for n, line in enumerate(lines, 1) if f'"{key}"' in line), 1)

    out = []
    if isinstance(data.get("main"), str):
        out.append(Entry("entry", data["main"], path, at("main"), what="main"))
    bins = data.get("bin")
    bins = {data.get("name") or "bin": bins} if isinstance(bins, str) else bins
    for name, target in (bins if isinstance(bins, dict) else {}).items():
        out.append(Entry("entry", str(name), path, at("bin"), what="command", text=str(target)))
    start = (
        (data.get("scripts") or {}).get("start") if isinstance(data.get("scripts"), dict) else None
    )
    if isinstance(start, str):
        out.append(Entry("entry", "npm start", path, at("start"), what="script", text=start))
    return out


def _dockerfile(path: str, text: str | None) -> list[Entry]:
    out = []
    for m in _DOCKER_CMD.finditer(text or ""):
        command = m[2]
        try:
            parsed = json.loads(command)
            command = " ".join(parsed) if isinstance(parsed, list) else command
        except ValueError:
            pass
        out.append(
            Entry("entry", command[:120], path, _line_at(text or "", m.start()), what=m[1].title())
        )
    return out


def _model_settings(path: str, lines: Sequence[str]) -> list[Entry]:
    """Model ids a settings file names under a model key."""
    out = []
    for n, line in enumerate(lines, 1):
        m = _MODEL_KEY.match(line)
        if m and "{{" not in line and _MODEL_ID.search(m["id"]) and not m["id"].isdigit():
            out.append(
                Entry("model", m["id"], path, n, what="setting", model=m["id"], model_from=m["key"])
            )
    return out


def _constant_value(sig: str) -> str | None:
    """A constant's string value, when its map line shows it whole."""
    m = re.match(r"""^\w+\s*=\s*(["'])(.*)\1\s*$""", sig)
    return m[2] if m else None


def _block(lines: Sequence[str], d: Definition) -> str:
    return "\n".join(lines[d.line - 1 : d.end]) if lines else d.sig


def _innermost(defs: Sequence[Definition], line: int) -> str:
    """The definition *line* is in: a function, a method, a route's handler."""
    holding = [
        d
        for d in defs
        if d.line <= line <= d.end and d.kind not in ("constant", "class") and d.line != line
    ]
    return min(holding, key=lambda d: d.end - d.line).name if holding else ""


def _line_at(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _cap(text: str) -> str:
    text = text.strip()
    return text[:TEXT_CHARS].rstrip() + ("…" if len(text) > TEXT_CHARS else "")


# Prompts


def _python_string(block: str) -> str | None:
    """The string a Python assignment holds, as the program sees it; an f-string's parts with
    ``{…}`` for what it fills in."""
    try:
        tree = ast.parse(block.strip())
    except SyntaxError:
        return None
    node = tree.body[0] if tree.body else None
    value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{…}" for v in value.values)
    return None


def _strings(block: str) -> str:
    """The text of the string literals after a definition's ``=`` (or ``:``), joined."""
    at = re.search(r"[=:]", block)
    rest = block[at.end() :] if at else block
    return "".join(m[2] for m in _LITERAL.finditer(rest))


def _codeish(text: str) -> bool:
    if not text:
        return True
    marks = sum(text.count(c) for c in "{};<>=()")
    return marks / len(text) > CODE_DENSITY


def _prompt(path: str, d: Definition, lines: Sequence[str], suffix: str) -> list[Entry]:
    block = _block(lines, d)
    said = _python_string(block) if suffix == ".py" and lines else None
    if said is None:
        said = _strings(block) if lines else ""
    named = bool(_PROMPTISH.search(d.name))
    if not named and _codeish(said):
        return []  # a long string that is markup or code: a page, a script, a style
    hashes = (_hash(said), _hash(said.strip())) if said else ()
    return [Entry("prompt", d.name, path, d.line, what="string", text=_cap(said), hashes=hashes)]


def _markdown(path: str, defs: Sequence[Definition], text: str | None) -> list[Entry]:
    """A Markdown file as one prompt, its headings inside it (whether it is one is decided with
    the rest of the code: :func:`_prompt_file`); an agent its front matter names."""
    out = []
    if text is not None and text.strip():
        lines = text.splitlines()
        body = text
        if text.startswith("---") and "\n---" in text[3:]:
            body = text[3:].split("\n---", 1)[1]
        hashes = [_hash(text), _hash(text.strip()), _hash(body.strip())]
        for d in defs:
            section = "\n".join(lines[d.line : d.end]).strip()
            if section:
                hashes.append(_hash(section))
        out.append(
            Entry(
                "prompt",
                PurePosixPath(path).name,
                path,
                1,
                what="file",
                text=_cap(body),
                hashes=tuple(dict.fromkeys(hashes)),
            )
        )
    folders = {p.lower() for p in PurePosixPath(path).parts[:-1]}
    if text and text.startswith("---") and folders & {"agent", "agents"}:
        head = text.split("\n---", 1)[0]
        if m := re.search(r"^name:\s*(.+)$", head, re.M):
            model = re.search(r"^model:\s*(.+)$", head, re.M)
            out.append(
                Entry(
                    "agent",
                    m[1].strip().strip("\"'"),
                    path,
                    _line_at(text, m.start()),
                    what="definition",
                    model=model[1].strip().strip("\"'") if model else "",
                )
            )
    return out


# Settings files: an agent's prompt, tools and model


def _settings(
    path: str, defs: Sequence[Definition], lines: Sequence[str], text: str | None
) -> list[Entry]:
    out: list[Entry] = []
    suffix = PurePosixPath(path).suffix.lower()
    data: Any = None
    if suffix == ".json" and text:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    agent = next(
        (
            m[1]
            for line in lines
            if (m := re.match(r"""^\s{0,2}"?name"?\s*:\s*"?([^"#,{}]+?)"?\s*,?\s*$""", line))
        ),
        None,
    )
    for d in defs:
        if d.kind == "prompt":
            value = _yaml_value(lines, d.line) if suffix != ".json" else _json_value(data, d.name)
            out.append(
                Entry(
                    "prompt",
                    f"{agent} ({d.name})" if agent else d.name,
                    path,
                    d.line,
                    what="setting",
                    text=_cap(value),
                    hashes=(_hash(value), _hash(value.strip())) if value else (),
                )
            )
        elif d.kind == "tool":
            names = (
                _yaml_list(lines, d.line) if suffix != ".json" else _json_names(data, d.name, lines)
            )
            out += [Entry("tool", n, path, at, what="setting") for n, at in names] or [
                Entry("tool", d.name, path, d.line, what="setting")
            ]
    if _AGENT_FILE.search(path) and any(d.kind == "agent" for d in defs):
        model = name = None
        for n, line in enumerate(lines, 1):
            if m := re.match(r"""^\s*"?model"?\s*:\s*"?([^"\s,#]+)""", line):
                model = model or (m[1], n)
            if m := re.match(r"""^\s{0,2}"?name"?\s*:\s*"?([^"#,]+?)"?\s*,?\s*$""", line):
                name = name or (m[1], n)
        label = name[0] if name else PurePosixPath(path).stem
        out.append(
            Entry(
                "agent",
                label,
                path,
                name[1] if name else 1,
                what="definition",
                model=model[0] if model else "",
            )
        )
    return out


def _yaml_value(lines: Sequence[str], line: int) -> str:
    """A top-level key's value: the rest of its line, or its block below."""
    head = lines[line - 1].split(":", 1)[1].strip() if line <= len(lines) else ""
    if head and not re.fullmatch(r"[|>][-+]?\d*", head):
        return head.strip("\"'")
    body = []
    for raw in lines[line:]:
        if raw and not raw[:1].isspace():
            break
        body.append(raw.strip())
    return "\n".join(body).strip()


def _json_value(data: Any, key: str) -> str:
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value)
    return value if isinstance(value, str) else ""


def _yaml_list(lines: Sequence[str], line: int) -> list[tuple[str, int]]:
    """The names a top-level key lists below it: ``- search`` or ``- name: search``; not those
    under a ``deny:`` (or ``block:``) key, tools it may not use, or an ``agents:`` key."""
    out = []
    denied_at: int | None = None
    for n, raw in enumerate(lines[line:], line + 1):
        if raw and not raw[:1].isspace() and not raw.startswith("-"):
            break
        indent = len(raw) - len(raw.lstrip())
        ends = not raw.lstrip().startswith("-") or (denied_at is not None and indent < denied_at)
        if denied_at is not None and raw.strip() and indent <= denied_at and ends:
            denied_at = None
        if _DENIED.match(raw):
            denied_at = indent
            continue
        m = _LIST_ITEM.match(raw) or _LIST_NAME.match(raw)
        if m and denied_at is None:
            out.append((m[1], n))
    return out


def _json_names(data: Any, key: str, lines: Sequence[str]) -> list[tuple[str, int]]:
    value = data.get(key) if isinstance(data, dict) else None
    names = []
    for item in value if isinstance(value, list) else []:
        name = (
            item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
        )
        if isinstance(name, str):
            at = next((n for n, line in enumerate(lines, 1) if f'"{name}"' in line), 1)
            names.append((name, at))
    return names


# Tools


def _params(text: str, name: str) -> tuple[str, ...]:
    """A function's parameter names, from its text starting at its definition."""
    at = re.search(rf"\b{re.escape(name)}\s*(?:<[^>]*>)?\s*\(", text)
    if at is None:
        return ()
    depth, start, parts, current = 0, at.end(), [], []
    for ch in text[start : start + CALL_CHARS]:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            if depth == 0:
                parts.append("".join(current))
                break
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if part.startswith("{"):
            out += re.findall(r"(\w+)\s*(?=[,}:=])", part.split("}")[0] + "}")
            continue
        m = re.match(r"(?:readonly\s+|\.\.\.)?([A-Za-z_]\w*)", part)
        if m and m[1] not in ("self", "cls", "this", "ctx", "context") and not part.startswith("*"):
            out.append(m[1])
    return tuple(out)


def _python_tool_args(block: str) -> dict[str, tuple[str, ...]]:
    """``tool name -> its parameter names`` from a Python literal of tool schemas."""
    try:
        tree = ast.parse(block.strip())
    except SyntaxError:
        return {}
    node = tree.body[0] if tree.body else None
    value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
    try:
        data = ast.literal_eval(value) if value is not None else None
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return {}
    found: dict[str, tuple[str, ...]] = {}

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            fn = item.get("function") if isinstance(item.get("function"), dict) else item
            name = fn.get("name")
            schema = fn.get("parameters") or fn.get("input_schema") or fn.get("inputSchema")
            if isinstance(name, str) and isinstance(schema, dict):
                props = schema.get("properties")
                found[name] = tuple(props) if isinstance(props, dict) else ()
                return
            for v in item.values():
                walk(v)
        elif isinstance(item, list | tuple):
            for v in item:
                walk(v)

    walk(data)
    return found


def _tool(path: str, d: Definition, lines: Sequence[str]) -> list[Entry]:
    block = _block(lines, d)
    sig = d.sig.strip()
    if re.match(r"(async\s+)?(def|function|func)\b|export\s+(async\s+)?function\b", sig):
        head = lines[d.line - 1] if lines else ""
        named = _NAME_IN.search(head) if head.lstrip().startswith("@") else None
        return [
            Entry(
                "tool",
                named[1] if named else d.name,
                path,
                d.line,
                what="function",
                args=_params(block, d.name),
            )
        ]
    if re.match(r"(export\s+)?(abstract\s+)?class\b", sig):
        if not _tool_class(d):
            return []
        named = _CLASS_TOOL_NAME[0].search(block) or _CLASS_TOOL_NAME[1].search(block)
        runs = re.search(r"\bdef\s+(_?a?run|execute|call|invoke)\s*\(", block)
        return [
            Entry(
                "tool",
                named[1] if named else d.name,
                path,
                d.line,
                what="class",
                args=_params(block[runs.start() :], runs[1]) if runs else (),
            )
        ]
    if _TYPE_SIG.match(sig):
        return []
    # A value: a list of schemas, or a call that makes one tool.
    args = _python_tool_args(block) if path.endswith(".py") and lines else {}
    names = [(m[1], d.line + block.count("\n", 0, m.start())) for m in _SCHEMA_NAME.finditer(block)]
    if names:
        return [
            Entry("tool", n, path, at, what="schema", inside=d.name, args=args.get(n, ()))
            for n, at in names
        ]
    called = re.search(r"[=:]\s*(?:await\s+)?(?:new\s+)?([\w.]+)\s*\(", block)
    if called and "tool" in called[1].rsplit(".", 1)[-1].lower():
        zod = tuple(dict.fromkeys(_ZOD_KEYS.findall(block)))
        return [Entry("tool", d.name, path, d.line, what=called[1], args=zod)]
    return []


# Routes


def _route(path: str, d: Definition, lines: Sequence[str], facts: Sequence[Mapping]) -> Entry:
    if re.match(r"^[A-Z]+ /", d.name):
        return Entry("route", d.name, path, d.line, what="call")
    head = lines[d.line - 1] if d.line <= len(lines) else ""
    if not head:
        head = next((f["text"] for f in facts if f["kind"] == "route" and f["line"] == d.line), "")
    m = _ROUTE_DECORATOR.search(head)
    if m is None:
        return Entry("route", d.name, path, d.line, what="handler", inside=d.name)
    verb = m["verb"].upper()
    if verb in ("ROUTE", "API_ROUTE"):
        methods = _ROUTE_METHODS.search(m["rest"])
        verb = methods[1].upper() if methods else "GET"
    elif verb == "WEBSOCKET":
        verb = "WS"
    return Entry("route", f"{verb} {m['path'] or '/'}", path, d.line, what="handler", inside=d.name)


def _file_route(path: str, d: Definition) -> Entry:
    m = _FILE_ROUTE.search(path)
    dirs = [p for p in (m["dirs"] if m else "").split("/") if p and not p.startswith("(")]
    shown = "/" + "/".join(re.sub(r"^\[(?:\.\.\.)?(\w+)\]$", r"{\1}", p) for p in dirs)
    return Entry("route", f"{d.name} {shown}", path, d.line, what="file", inside=d.name)


# State and agents


def _state_word(name: str) -> str:
    low = name.lower()
    return next((word for key, word in _STATE_WORDS if key in low), "state")


def _not_a_part(d: Definition) -> bool:
    """A name that sounds like state or an agent but isn't one: a type, a setting read from the
    environment, a plain value, a limit or a text held in a constant, a private helper class."""
    if _TYPE_SIG.match(d.sig) or _ENV_READ.search(d.sig) or _SCALAR.search(d.sig):
        return True
    is_class = bool(re.match(r"(export\s+)?(abstract\s+)?class\b", d.sig))
    if is_class:
        return d.name.startswith("_")
    if d.name.lstrip("_").isupper():
        # A constant holds a store only when it is made by a call or is a collection.
        value = d.sig.split("=", 1)[1].strip() if "=" in d.sig else ""
        return not re.match(r"(new\s+)?[\w.]+\(|[{\[]|(dict|set|list)\(", value)
    return False


def _state_def(path: str, d: Definition) -> list[Entry]:
    if _not_a_part(d):
        return []
    return [Entry("state", d.name, path, d.line, what=_state_word(d.name))]


def _agent_def(
    path: str, d: Definition, lines: Sequence[str], constants: Mapping[str, str | None]
) -> list[Entry]:
    if _not_a_part(d):
        return []
    block = _block(lines, d)
    called = re.search(r"[=:]\s*(?:await\s+)?(?:new\s+)?([\w.]+)\s*\(", block.split("\n", 1)[0])
    named = _NAME_IN.search(block) if called else None
    model = _model_in(block, constants) if called else ""
    what = called[1] if called else "class" if re.match(r"(export\s+)?class\b", d.sig) else "value"
    return [
        Entry(
            "agent",
            named[1] if named else d.name,
            path,
            d.line,
            what=what,
            inside=d.name if named and named[1] != d.name else "",
            model=model,
        )
    ]


def _model_in(args: str, constants: Mapping[str, str | None]) -> str:
    for pattern in _MODEL_NAMED[:2]:
        if m := pattern.search(args):
            return m[1]
    if m := _MODEL_CONST.search(args):
        return constants.get(m[1]) or ""
    return ""


def _call_args(text: str, start: int) -> str:
    """The text of a call's arguments, from just past its ``(``, up to the matching ``)``."""
    depth = 0
    for n, ch in enumerate(text[start : start + CALL_CHARS]):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return text[start : start + n]
            depth -= 1
    return text[start : start + CALL_CHARS]


# What the text shows: model calls, stores, tools used by name, entry points, more routes


def _scan(
    path: str, defs: Sequence[Definition], text: str, constants: Mapping[str, str | None]
) -> list[Entry]:
    out: list[Entry] = []
    suffix = PurePosixPath(path).suffix.lower()
    patterns = [(_MODEL_CALLS, "call"), (_MODEL_CLIENTS, "client")]
    if "litellm" in text:
        patterns.append((_BARE_COMPLETION, "call"))
    for pattern, what in patterns:
        for m in pattern.finditer(text):
            if _commented(text, m.start()):
                continue
            args = _call_args(text, m.end())
            model = _model_in(args, constants)
            if not model and what == "client" and (first := _MODEL_NAMED[2].match(args)):
                model = first[1]
            named = _MODEL_FROM.search(args) if not model else None
            line = _line_at(text, m.start())
            call = m["call"].lstrip(".")
            out.append(
                Entry(
                    "model",
                    call,
                    path,
                    line,
                    what=what,
                    inside=_innermost(defs, line),
                    model=model,
                    model_from=named[1] if named else "",
                )
            )
    for word, pattern in _STORES:
        for m in pattern.finditer(text):
            if _commented(text, m.start()):
                continue
            line = _line_at(text, m.start())
            row = text.splitlines()[line - 1]
            assigned = re.match(
                r"\s*(?:self\.)?(?:const\s+|let\s+|var\s+)?(\w+)\s*(?::[^=]+)?:?=", row
            )
            name = assigned[1] if assigned else m["call"]
            out.append(
                Entry("state", name, path, line, what=word, inside=m["call"].replace("new ", ""))
            )
    for m in _TOOL_CALLS.finditer(text):
        if _commented(text, m.start()):
            continue
        line = _line_at(text, m.start())
        what = "registered" if m["call"] in ("tool", "registerTool") and suffix != ".py" else "call"
        out.append(Entry("tool", m["name"], path, line, what=what, inside=_innermost(defs, line)))
    for m in _SERVER_APP.finditer(text):
        line = _line_at(text, m.start("name"))
        factory = _innermost(defs, line)
        if factory:  # made inside a function: the function is the app's factory
            out.append(Entry("entry", factory, path, line, what=f"{m['call']} app factory"))
        else:
            out.append(Entry("entry", m["name"], path, line, what=m["call"]))
    for m in _APP_RETURN.finditer(text):
        line = _line_at(text, m.start())
        if factory := _innermost(defs, line):
            out.append(Entry("entry", factory, path, line, what=f"{m['call']} app factory"))
    for m in _UVICORN_TARGET.finditer(text):
        out.append(Entry("entry", m[1], path, _line_at(text, m.start()), what="uvicorn"))
    if suffix == ".py" and not _TEST_FILE.search(path):
        for m in _MAIN_GUARD.finditer(text):
            out.append(
                Entry(
                    "entry",
                    PurePosixPath(path).name,
                    path,
                    _line_at(text, m.start()),
                    what="script",
                )
            )
    for m in _SERVER_START.finditer(text):
        call = m["call"]
        if call.endswith(".listen") and suffix not in _JS:
            continue
        if _commented(text, m.start()):
            continue
        line = _line_at(text, m.start())
        out.append(Entry("entry", call, path, line, what="serves", inside=_innermost(defs, line)))
    if suffix == ".go":
        for m in _GO_ROUTE.finditer(text):
            verb, route = m["verb"].upper(), m["path"]
            if " " in route:
                verb, route = route.split(" ", 1)
            elif verb in ("HANDLEFUNC", "HANDLE"):
                verb = "ANY"
            line = _line_at(text, m.start())
            out.append(
                Entry(
                    "route",
                    f"{verb} {route}",
                    path,
                    line,
                    what="call",
                    inside=_innermost(defs, line),
                )
            )
    elif suffix in _JS:
        for m in _JS_ROUTE.finditer(text):
            verb = "ANY" if m["verb"] == "all" else m["verb"].upper()
            line = _line_at(text, m.start())
            out.append(
                Entry(
                    "route",
                    f"{verb} {m['path']}",
                    path,
                    line,
                    what="call",
                    inside=_innermost(defs, line),
                )
            )
    return out


def _commented(text: str, offset: int) -> bool:
    start = text.rfind("\n", 0, offset) + 1
    before = text[start:offset].lstrip()
    return before.startswith(("#", "//", "*", "/*"))


# Real use, joined


@dataclass(frozen=True)
class Hits:
    """How often real use touched each entry (*uses*: entry id -> count, over the window the
    digest read: *sessions* conversations, *asks* asks), and what real use named that the code
    doesn't (*unseen*: ``{kind, name, calls}``)."""

    uses: Mapping[str, int]
    unseen: tuple[Mapping[str, Any], ...]
    sessions: int = 0
    asks: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "uses": dict(self.uses),
            "unseen": [dict(u) for u in self.unseen],
            "sessions": self.sessions,
            "asks": self.asks,
        }


def _key(name: str) -> str:
    """A name as both sides may spell it: ``searchDocs``, ``search-docs``, ``search_docs``."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower())


def _words(text: str) -> str:
    return " ".join(text.split()).lower()


def _head(entry: Entry) -> str:
    """A prompt's start, up to what the code fills in, as words to find in one sent."""
    text = re.split(r"\{|\$\{|\$\w|%s|\{\{|<\w", entry.text, maxsplit=1)[0]
    words = _words(text).split()
    head = " ".join(words[:HEAD_WORDS])
    return head if len(head) >= HEAD_MIN else ""


def _route_key(route: str) -> tuple[str, str]:
    """``(method, path)`` of a route, its parameters all one shape: ``/s/{}`` for ``/s/{sid}``,
    ``/s/:sid`` or ``/s/[sid]``; ``ANY`` for no method."""
    verb, _, path = route.strip().partition(" ")
    if not path:
        verb, path = "ANY", verb
    path = re.sub(r"\{[^}]*\}|:\w+|<[^>]*>|\[[^\]]*\]", "{}", path).rstrip("/") or "/"
    return verb.upper(), path


def match(found: Skeleton, digest: Mapping[str, Any] | None) -> Hits:
    """How often the use *digest* holds (:func:`evalution.traces.profile.digest`, or the part
    of it kept) touched each entry of *found*, and what it names that *found* doesn't."""
    if not digest:
        return Hits({}, ())
    uses: dict[str, int] = {}
    unseen: list[dict[str, Any]] = []

    tools = digest.get("tools") or {}
    by_tool: dict[str, list[Entry]] = {}
    for e in found.of("tool"):
        by_tool.setdefault(_key(e.name), []).append(e)
    for name, seen in sorted(tools.items()):
        calls = int(seen.get("calls") or 0)
        hit = by_tool.get(_key(name)) or by_tool.get(_key(name.rsplit(".", 1)[-1])) or []
        for e in hit:
            uses[e.id] = uses.get(e.id, 0) + calls
        if not hit and (calls or seen.get("offered")):
            unseen.append({"kind": "tool", "name": name, "calls": calls})

    prompts = [e for e in found.of("prompt") if e.text or e.hashes]
    for sent in digest.get("prompts") or []:
        calls = int(sent.get("calls") or 0)
        head = _words(str(sent.get("head") or ""))
        sections = set(sent.get("sections") or ())
        hit = [
            e
            for e in prompts
            if sent.get("hash") in e.hashes
            or sections & set(e.hashes)
            or ((h := _head(e)) and h in head)
        ]
        for e in hit:
            uses[e.id] = uses.get(e.id, 0) + calls
        if not hit:
            words = " ".join(str(sent.get("head") or "").split()[:10])
            unseen.append({"kind": "prompt", "name": words or sent.get("hash", ""), "calls": calls})

    models = [e for e in found.of("model") if e.model]
    for name, calls in sorted((digest.get("models") or {}).items()):
        hit = [e for e in models if name == e.model or name.startswith(e.model + "-")]
        for e in hit:
            uses[e.id] = uses.get(e.id, 0) + int(calls)
        if not hit:
            unseen.append({"kind": "model", "name": name, "calls": int(calls)})

    routes: dict[tuple[str, str], list[Entry]] = {}
    for e in found.of("route"):
        routes.setdefault(_route_key(e.name), []).append(e)
    for name, calls in sorted((digest.get("routes") or {}).items()):
        verb, path = _route_key(name)
        hit = routes.get((verb, path)) or routes.get(("ANY", path)) or []
        if verb == "ANY":
            hit = [e for (_, p), found_here in routes.items() if p == path for e in found_here]
        for e in hit:
            uses[e.id] = uses.get(e.id, 0) + int(calls)
        if not hit:
            unseen.append({"kind": "route", "name": name, "calls": int(calls)})

    unseen.sort(key=lambda u: (KINDS.index(u["kind"]), -u["calls"], u["name"]))
    return Hits(
        dict(sorted(uses.items())),
        tuple(unseen),
        int(digest.get("sessions") or 0),
        int(digest.get("asks") or 0),
    )
