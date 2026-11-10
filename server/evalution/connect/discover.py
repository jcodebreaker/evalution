"""Connecting a target by its project folder (docs/architecture.md, "Connecting a target").

:class:`Discoveries` looks at a folder in the background and keeps a list of plain-language
steps the page shows as they go. In order:

1. **Known apps**: each adapter's own ``detect``, highest ``rank`` first, says whether the folder
   is its kind of app and what that tells.
2. **Any other app** gets :data:`FALLBACK`: its README, compose files, ``.env.example``,
   package.json / pyproject and route definitions (FastAPI, Flask, Express, Next API routes) give
   the chat endpoint, its body and reply field, the port, and the tracing setup (Phoenix URL and
   project). When that is not enough, the evals model reads a compact digest of those files
   (never ``.env``).
3. **Send traces here**: the lines that point the app at evalution
   (:mod:`evalution.traces.setup`), and whether spans arrive yet.
   An app given by its address instead skips the reading: its first guess is put right by the
   test message (:mod:`evalution.connect.probe`).
4. **Verify**, without sending a chat message: the app answers, and its traces hold
   conversations (:func:`evalution.connect.connect.check`).
5. **Code**: the folder itself; everything in it that is source is watched. When its skeleton
   names more than :data:`focus.ASK_OVER` agents (a platform that hosts many), one more question
   asks which one is tested, with the agents as ``choices`` (:func:`focus.choices`); the answer
   is the target's ``code.focus``.

What it could not tell becomes at most a couple of plain questions. ``.env`` is only ever read
for the keys named here and by the adapters; a secret found there stays in this process until the
target is saved as a stored secret, and is never returned, logged or sent to the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from evalution.adapters import infos
from evalution.adapters.base import (
    COMPOSE_PORT,
    NOT_APP_PORTS,
    AdapterInfo,
    Detection,
    Folder,
    read_env,
)
from evalution.code import SKIP_DIRS, is_secret
from evalution.connect import connect, repository
from evalution.connect.scrub import scrub_text
from evalution.core import net
from evalution.core.case import SLUG
from evalution.llm import LLM, LLMError, cut
from evalution.project import focus
from evalution.project.skeleton import Skeleton, match
from evalution.traces import setup
from evalution.traces.base import is_ai
from evalution.traces.builtin import TARGET_PREFIX, Builtin, SpanStore
from evalution.traces.builtin import project as project_of
from evalution.traces.profile import digest as trace_digest
from evalution.traces.profile import recent_sessions

_logger = logging.getLogger(__name__)

#: Discoveries kept in memory; the oldest go first.
KEEP = 20
SCAN_FILES = 400
FILE_CAP = 100_000
DEPTH = 6
DIGEST_CAP = 16_000
#: In a route, these say another address to something along the way.
_NOT_IN_A_ROUTE = re.compile(r"[\\\s\x00-\x1f]")
WINDOW = 3_000

ENV_FILE = ".env"
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template")
#: The only ``.env`` keys read here; an adapter names its own.
GENERIC_KEYS = ("PORT", "PHOENIX_COLLECTOR_ENDPOINT", "PHOENIX_PROJECT_NAME", "PHOENIX_API_KEY")
#: The adapter for an app no other adapter claims; its ``propose`` takes what ``_generic`` reads.
FALLBACK = "http"

_SOURCE = re.compile(r"\.(py|[mc]?[jt]sx?)$")
_CONFIG = re.compile(
    r"^(readme.*|.*compose.*\.ya?ml|package\.json|pyproject\.toml|requirements.*\.txt|"
    r"dockerfile.*|procfile|makefile)$",
    re.IGNORECASE,
)
_PY_ROUTE = re.compile(r"@\w+\.(post|get|put|route|api_route)\(\s*[\"']([^\"']+)[\"']([^\n]*)")
_JS_ROUTE = re.compile(r"\b(?:app|router|server|api)\.(post|get|put)\(\s*[\"'`]([^\"'`]+)[\"'`]")
_NEXT_ROUTE = re.compile(r"(?:^|/)(?:app/(api/.+)/route|pages/(api/.+))\.[mc]?[jt]sx?$")
_NEXT_POST = re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+POST\b")
_PORTS = (
    re.compile(r"--port[ =](\d{2,5})"),
    re.compile(r"\bport\s*=\s*(\d{2,5})"),
    re.compile(r"\.listen\(\s*(\d{2,5})"),
    re.compile(r"\s-p\s*(\d{2,5})\b"),
)
_CHAT_WORDS = (
    ("chat", 3),
    ("message", 2),
    ("ask", 2),
    ("convers", 2),
    ("agent", 2),
    ("invoke", 2),
    ("completion", 2),
    ("query", 1),
    ("prompt", 1),
    ("run", 1),
)
_HEALTH = ("/health", "/healthz", "/api/health", "/ping")
_MESSAGE = ("message", "messages", "text", "input", "prompt", "query", "question", "content")
_SESSION = (
    "session_id",
    "sessionId",
    "session",
    "conversation_id",
    "conversationId",
    "thread_id",
    "threadId",
    "chat_id",
    "chatId",
)
_REPLY = ("reply", "response", "answer", "output", "message", "text", "content", "result")
_DEFAULT_PORTS = (
    ("fastapi", 8000),
    ("uvicorn", 8000),
    ("flask", 5000),
    ("express", 3000),
    ("next", 3000),
)

_URL = re.compile(r"^https?://", re.IGNORECASE)
#: The step that says whether traces reach evalution yet, before and after they do.
SEND_STEPS = ("Send traces to evalution", "Traces arrive in evalution")
ARRIVAL_WINDOW = 3600.0

QUESTIONS = {
    "port": ("Which port does it run on?", "8000"),
    "chat": ("Which address takes chat messages?", "/chat"),
    "focus": (focus.QUESTION, focus.ALL_LABEL),
}
#: Questions with a default: left unanswered, the default holds ("All of them").
OPTIONAL = frozenset({"focus"})
#: Conversations read to say how often each agent was used, and the longest that may take.
USE_SESSIONS = 50
USE_SECONDS = 8.0


# Reading the folder


def example_env(folder: Path) -> dict[str, str]:
    """The template env file (``.env.example``), when there is one: names and example values."""
    for name in ENV_EXAMPLES:
        file = folder / name
        if file.is_file():
            found: dict[str, str] = {}
            for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.strip().removeprefix("export ").partition("=")
                if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                    found[key.strip()] = value.split(" #", 1)[0].strip().strip("\"'")
            return found
    return {}


def scan(folder: Path) -> dict[str, str]:
    """``relative path -> text`` of the files that tell how the app runs: sources, READMEs,
    docs, compose and package files. Never a secrets file."""
    files: dict[str, str] = {}
    for current, dirs, names in os.walk(folder):
        here = Path(current)
        depth = len(here.relative_to(folder).parts)
        dirs[:] = sorted(
            d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and depth < DEPTH
        )
        for name in sorted(names):
            rel = (here / name).relative_to(folder).as_posix()
            wanted = _SOURCE.search(name) or _CONFIG.match(name) or rel.startswith("docs/")
            if not wanted or is_secret(rel) or not (here / name).resolve().is_relative_to(folder):
                continue
            try:
                with (here / name).open("rb") as handle:
                    files[rel] = handle.read(FILE_CAP).decode("utf-8", errors="replace")
            except OSError:
                continue
            if len(files) >= SCAN_FILES:
                return files
    return files


@dataclass
class Route:
    method: str
    path: str
    file: str
    at: int


def routes(files: dict[str, str]) -> list[Route]:
    """The HTTP routes defined in *files* (FastAPI / Flask decorators, Express, Next API)."""
    out: list[Route] = []
    for rel, text in files.items():
        if rel.endswith(".py"):
            for found in _PY_ROUTE.finditer(text):
                method = found[1].upper()
                if method in ("ROUTE", "API_ROUTE"):
                    method = "POST" if "POST" in found[3] else "GET"
                out.append(Route(method, found[2], rel, found.end()))
        elif _SOURCE.search(rel):
            out += [Route(m[1].upper(), m[2], rel, m.end()) for m in _JS_ROUTE.finditer(text)]
            if found := _NEXT_ROUTE.search(rel):
                path = "/" + (found[1] or found[2]).removesuffix("/index")
                method = "POST" if found[2] or _NEXT_POST.search(text) else "GET"
                out.append(Route(method, path, rel, 0))
    return out


def chat_route(found: list[Route]) -> Route | None:
    """The route that most looks like the chat endpoint."""

    def score(route: Route) -> int:
        path = route.path.lower()
        return max((points for word, points in _CHAT_WORDS if word in path), default=0)

    posts = [r for r in found if r.method == "POST" and score(r)]
    return max(posts, key=lambda r: (score(r), -len(r.path)), default=None)


def _window(files: dict[str, str], route: Route) -> str:
    text = files[route.file][route.at : route.at + WINDOW]
    if route.file.endswith(".py"):
        text = text.split("\n@", 1)[0]
    return text


def _model_fields(files: dict[str, str], model: str) -> list[str]:
    pattern = re.compile(rf"class {re.escape(model)}\([^)]*\):\n((?:[ \t]+.*\n?|\n)+)")
    for rel, text in files.items():
        if rel.endswith(".py") and (found := pattern.search(text)):
            return re.findall(r"^[ \t]+(\w+)\s*:", found[1], re.MULTILINE)
    return []


def body_fields(files: dict[str, str], route: Route) -> list[str]:
    """The field names the chat handler reads from its request body."""
    window = _window(files, route)
    names = re.findall(r"(?:body|json|data|payload)(?:\(\))?\s*(?:\.|\[\s*[\"'])(\w+)", window)
    names += re.findall(r"\.get\(\s*[\"'](\w+)[\"']", window)
    for group in re.findall(r"\{([^{}]+)\}\s*=\s*(?:await\s+)?\w+\.(?:body|json\(\))", window):
        names += re.findall(r"\w+", group)
    signature = window.split(":\n", 1)[0] if route.file.endswith(".py") else ""
    for model in re.findall(r"\w+\s*:\s*([A-Z]\w*)", signature):
        names += _model_fields(files, model)
    return names


def reply_field(files: dict[str, str], route: Route) -> str:
    """A JSON path to the reply in the chat handler's answer, ``$`` when it is not clear."""
    window = _window(files, route)
    names = re.findall(r"return\s*\{\s*[\"'](\w+)[\"']\s*:", window)
    names += re.findall(r"(?:json|jsonify|send)\(\s*\{\s*[\"']?(\w+)[\"']?\s*[:,}]", window)
    decorator = files[route.file][max(0, route.at - 300) : route.at + 200]
    for model in re.findall(r"response_model\s*=\s*([A-Z]\w*)", decorator):
        names += _model_fields(files, model)
    for model in re.findall(r"->\s*([A-Z]\w*)\s*:", window[:400]):
        names += _model_fields(files, model)
    name = _pick(names, _REPLY) or (names[0] if names else None)
    return f"$.{name}" if name else "$"


def _pick(names: list[str], preferred: tuple[str, ...]) -> str | None:
    return next((p for p in preferred if p in names), None)


def find_port(files: dict[str, str], env: dict[str, str]) -> int | None:
    """The app's port: ``PORT`` in the env, else a published compose port, else one in the
    code or scripts."""
    if env.get("PORT", "").isdigit():
        return int(env["PORT"])
    compose = [t for rel, t in files.items() if "compose" in rel.lower()]
    others = [t for rel, t in files.items() if "compose" not in rel.lower()]
    for texts, patterns in ((compose, (COMPOSE_PORT,)), (others, _PORTS)):
        for text in texts:
            for pattern in patterns:
                for found in pattern.findall(text):
                    if int(found) not in NOT_APP_PORTS:
                        return int(found)
    return None


def _local(url: str) -> str | None:
    """*url*'s origin as seen from this computer (a container host name becomes 127.0.0.1)."""
    parts = urlsplit(url if "//" in url else f"http://{url}")
    if not parts.hostname:
        return None
    host = parts.hostname
    if host not in ("localhost", "127.0.0.1") and not re.fullmatch(r"[\d.]+", host):
        host = "127.0.0.1"
    if host == "0.0.0.0":  # noqa: S104 - read from a config, not bound
        host = "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme or 'http'}://{host}{port}"


def tracing(text: str, env: dict[str, str]) -> tuple[str | None, str | None]:
    """``(phoenix url, project)`` from the code, configs and env."""
    project = env.get("PHOENIX_PROJECT_NAME")
    if not project and (
        found := re.search(r"project_name\s*=\s*[\"']([\w.-]+)[\"']", text)
        or re.search(r"PHOENIX_PROJECT_NAME[\"']?\s*[=:,]\s*[\"']?([\w.-]+)", text)
    ):
        project = found[1]
    url = env.get("PHOENIX_COLLECTOR_ENDPOINT")
    if not url:
        for key in ("PHOENIX_COLLECTOR_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
            found = re.search(rf"{key}[\"']?\s*[=:,]\s*[\"']?(https?://[^\s\"'$]+)", text)
            if found:
                url = found[1]
                break
    if not url and (found := re.search(r"endpoint\s*=\s*[\"'](https?://[^\"']+)[\"']", text)):
        url = found[1]
    return (_local(url) if url else None), project


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _app_name(host: str) -> str:
    """A target name from an address: its first label, unless that only says "this computer"."""
    first = host.split(".")[0]
    return "app" if host in ("localhost", "0.0.0.0") or first.isdigit() else first  # noqa: S104


def slug(name: str) -> str:
    """A target name from a folder name."""
    made = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:60]
    return made if SLUG.match(made) else "agent"


# One discovery


@dataclass
class Job:
    """One folder being looked at, and what was found so far."""

    id: str
    #: ``None`` when the app was given by its address: then there's no code to read.
    folder: Path | None
    name: str
    status: str = "working"  # working | ready | failed
    steps: list[dict[str, str]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    adapter: str = FALLBACK
    base: str = ""
    #: The base URL is a framework's usual port, not read from the project.
    guessed: bool = False
    options: dict[str, Any] = field(default_factory=dict)
    #: The app's own Phoenix, when it has one; evalution's own traces are read either way.
    phoenix: dict[str, str] | None = None
    #: Secret values found in the project: kept here, never shown.
    secrets: dict[str, str] = field(default_factory=dict)
    #: How to send traces to evalution (:func:`setup.advice`), and ``arriving``.
    traces: dict[str, Any] | None = None
    #: The repository the app was given by, cloned into *folder* before it is read.
    clone: repository.Address | None = None
    #: A question's choices, by its key: ``[{value, label, hint, ...}]``.
    choices: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def step(self, text: str) -> dict[str, str]:
        made = {"text": text, "state": "doing"}
        self.steps.append(made)
        return made

    def ask(self, key: str) -> None:
        if key not in self.questions:
            self.questions.append(key)

    def body(self, answers: dict[str, str] | None = None) -> dict[str, Any]:
        """The target as ``POST /v1/targets`` takes it, with *answers* to the questions."""
        answers = answers or {}
        base = self.base
        if (port := str(answers.get("port") or "").strip()).isdigit():
            base = f"http://127.0.0.1:{port}"
        options = infos()[self.adapter].propose(base, self.options, answers)
        code: dict[str, Any] | None = {"path": str(self.folder)} if self.folder else None
        picked = str(answers.get("focus") or "").strip()
        named = {c["value"] for c in self.choices.get("focus", ()) if c["value"] != focus.ALL}
        if code is not None and picked in named:
            code["focus"] = picked
        return {
            "name": self.name,
            "adapter": self.adapter,
            "options": options,
            "phoenix": dict(self.phoenix) if self.phoenix else None,
            "code": code,
            "secrets": dict(self.secrets),
        }

    def view(self) -> dict[str, Any]:
        """What the page shows: steps, questions and the proposed target, without secrets."""
        target = None
        if self.status == "ready":
            body = self.body()
            target = {**body, "secrets": sorted(body["secrets"]), "managed": False}
        return {
            "id": self.id,
            "folder": str(self.folder) if self.folder else self.base,
            "status": self.status,
            "steps": self.steps,
            "questions": [self._question(k) for k in self.questions],
            "target": target,
            "traces": self.traces,
            "repository": self.clone.as_dict() if self.clone else None,
        }

    def _question(self, key: str) -> dict[str, Any]:
        made: dict[str, Any] = {"key": key, "text": QUESTIONS[key][0], "hint": QUESTIONS[key][1]}
        if key in self.choices:
            made["choices"] = self.choices[key]
        return made

    def missing(self, answers: dict[str, Any]) -> list[str]:
        """The questions *answers* leaves open that must be answered."""
        return [
            k for k in self.questions if k not in OPTIONAL and not str(answers.get(k) or "").strip()
        ]


def folder_of(path: str) -> Path:
    """The folder a person typed: ``~`` expanded, relative to their home."""
    found = Path(path.strip()).expanduser()
    return (found if found.is_absolute() else Path.home() / found).resolve()


def _detected(folder: Folder) -> tuple[AdapterInfo, Detection] | None:
    """The adapter that claims *folder*, and what it read there."""
    for info in sorted(infos().values(), key=lambda i: -i.rank):
        if (found := info.detect(folder)) is not None:
            return info, found
    return None


def _tracing(
    job: Job, files: dict[str, str], known: dict[str, str], project: str | None = None
) -> None:
    """The app's own Phoenix, read too when the project names one: the history of real
    traffic."""
    text = "\n".join(files.values()) + "\n" + "\n".join(f"{k}={v}" for k, v in known.items())
    url, found = tracing(text, known)
    job.phoenix = {"url": url, "project": found or project or "default"} if url else None


def digest(files: dict[str, str], found: list[Route], example: dict[str, str]) -> str:
    """A compact view of the project for the model: file list, README, package files, route
    code and the template env's key names. Never ``.env``, never a value from a template."""
    parts = ["Files:\n" + "\n".join(list(files)[:150])]
    for rel, text in files.items():
        name = rel.rsplit("/", 1)[-1].lower()
        if "/" not in rel and (
            name.startswith("readme") or name in ("package.json", "pyproject.toml")
        ):
            parts.append(f"--- {rel}\n{text[:3000]}")
    for route in found[:12]:
        parts.append(
            f"--- {route.file} ({route.method} {route.path})\n{_window(files, route)[:1500]}"
        )
    if example:
        parts.append("Env keys: " + ", ".join(example))
    return "\n\n".join(parts)[:DIGEST_CAP]


ASK_MODEL = """You read a summary of a software project and say how to chat with it over HTTP.
Answer with one JSON object and nothing else:
{"path": "/chat", "body": {"message": "{message}", "session_id": "{session}"}, "reply": "$.reply",
 "port": 8000, "health": "/health", "phoenix_project": null}
"path" is the route that takes one user message and answers. In "body", put {message} where the
user's text goes and {session} where a conversation id goes (leave it out when there is none).
"reply" is a JSON path to the answer text in the response ("$" when it is plain text). It may
index from either end and pick by a field: $.items[-1].text, or
$.items[?data.role=='assistant'][-1].data.content when messages nest. Use null for anything you
cannot tell.
Add these only when the code shows them:
"open": {"path": "/sessions", "id": "$.id", "body": {}} when the app must make a session first and
gives its id. Then use {session} for that id in the path and body.
"poll": {"path": "/sessions/{session}", "until": "$.status == 'idle'"} when sending only starts the
work and another route says when it is done; "reply" then reads that route's answer.
"background": true, only with "poll", when the app can answer first and go on working (a queued
task); "until" must
then hold only once that work has ended."""


def route_problem(plan: dict[str, Any]) -> str | None:
    """Why a *plan* for how to chat with an app can't be used: its routes must stay on the app's
    address, and the message must go somewhere."""
    routes = [plan.get("path"), plan.get("health")]
    routes += [p.get("path") for p in (plan.get("open"), plan.get("poll")) if isinstance(p, dict)]
    for route in routes:
        if route is not None and (
            not isinstance(route, str)
            or not route.startswith("/")
            or route.startswith("//")
            or _NOT_IN_A_ROUTE.search(route)
        ):
            return f"It named a route off the app's address: {cut(str(route), 80)}"
    if not isinstance(plan.get("path"), str):
        return "It named no route."
    if "{message}" not in json.dumps(plan.get("body")) and "{message}" not in plan["path"]:
        return "Its settings don't send the message."
    return None


async def ask_model(llm: LLM, text: str, instructions: str = ASK_MODEL) -> dict[str, Any]:
    said = [{"role": "user", "content": text}]
    reply = await llm.chat("light", instructions, said, max_tokens=800)
    found = re.search(r"\{.*\}", reply.text, re.DOTALL)
    try:
        data = json.loads(found[0]) if found else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _files(folder: Folder) -> dict[str, str]:
    return folder.files


class Discoveries:
    """Discoveries running and done, by id. *names* gives the target names already taken."""

    def __init__(
        self,
        root: Path,
        names: Any,
        llm: LLM | None,
        traces_url: str,
        store: SpanStore,
        data_dir: Path | None = None,
    ) -> None:
        self.root = root
        #: Where a repository given by its address is cloned: ``<this>/code/<target>``. The
        #: config's own default when not given.
        self.data_dir = Path(data_dir) if data_dir is not None else root / "data"
        self.names = names
        self.llm = llm
        #: Where apps send traces to evalution, as this computer reaches it.
        self.traces_url = traces_url
        #: The workspace's spans.
        self.store = store
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def _free_name(self, wanted: str) -> str:
        taken = set(self.names())
        name, n = slug(wanted), 2
        while name in taken:
            name, n = f"{slug(wanted)[:56]}-{n}", n + 1
        return name

    def start(self, path: str) -> Job:
        """Look at a project folder, at a repository on the code host (``owner/repo``,
        ``github.com/owner/repo``, ``owner/repo#412``, ``owner/repo@ref``: cloned first, see
        :mod:`evalution.connect.repository`), or at an app given by its address. A folder that is
        there wins over a repository of the same name."""
        text = path.strip()
        repo = repository.parse(text)
        if repo is not None and folder_of(text).is_dir():
            repo = None
        address = urlsplit(text) if repo is None and _URL.match(text) else None
        if repo is not None:
            name = self._free_name(repo.repo)
            job = Job(secrets.token_hex(6), repository.folder(self.data_dir, name), name)
            job.clone = repo
            job.step(f"Cloning {repo.said}")
        elif address:
            host = address.hostname or "app"
            job = Job(secrets.token_hex(6), None, self._free_name(_app_name(host)))
            job.base = f"{address.scheme}://{address.netloc}"
            job.options = {"path": path.strip().removeprefix(job.base)}
            job.step(f"Looking at {address.netloc}")
        else:
            folder = folder_of(path)
            job = Job(secrets.token_hex(6), folder, self._free_name(folder.name))
            job.step(f"Reading {folder.name or folder}")
        self.jobs[job.id] = job
        while len(self.jobs) > KEEP:
            self.jobs.pop(next(iter(self.jobs)))
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id: str) -> Job:
        """:raises LookupError: No such discovery."""
        found = self.jobs.get(job_id)
        if found is None:
            raise LookupError(job_id)
        return found

    def forget(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)

    async def _run(self, job: Job) -> None:
        try:
            await self._discover(job)
        except Exception:
            _logger.warning("discover: %s failed", job.id, exc_info=True)
            job.step("Something went wrong while reading the folder.")["state"] = "problem"
            job.status = "failed"

    async def _discover(self, job: Job) -> None:
        reading = job.steps[0]
        if job.folder is None:
            await self._address(job, reading)
            return
        if job.clone is not None:
            if not await self._clone(job, reading):
                return
            reading = job.step(f"Reading {job.clone.said}")
        if not job.folder.is_dir():
            reading["text"] = f"There's no folder at {job.folder}. Check the path and try again."
            reading["state"] = "problem"
            job.status = "failed"
            return
        folder = Folder(job.folder, partial(scan, job.folder))
        detected = await asyncio.to_thread(_detected, folder)
        reading["state"] = "done"
        if detected:
            await self._known(job, folder, *detected)
        else:
            await self._generic(job, await asyncio.to_thread(_files, folder))
        await self._agents(job)
        await self._finish(job, await asyncio.to_thread(_files, folder))

    async def _agents(self, job: Job) -> None:
        """On a folder that hosts more than :data:`focus.ASK_OVER` agents, ask which one is
        tested: the skeleton read with no model, the agents most used by its traces first."""
        assert job.folder is not None
        try:
            found, edges = await asyncio.to_thread(focus.of_folder, job.folder)
        except Exception:
            _logger.warning("discover: %s's agents couldn't be read", job.id, exc_info=True)
            return
        if not focus.asks(found.entries):
            return
        count = len(focus.agents(found.entries))
        job.step(f"Found {count} agents in the code")["state"] = "done"
        uses = await self.agent_uses(job, found)
        job.choices["focus"] = focus.choices(found.entries, edges, uses)
        job.ask("focus")

    async def agent_uses(self, job: Job, found: Skeleton) -> dict[str, int] | None:
        """How often real traces touched each skeleton entry, from the conversations already in
        evalution for this app; ``None`` when there are none."""
        name = infos()[job.adapter].trace_project or job.name
        source = Builtin(self.store, name.removeprefix(TARGET_PREFIX))
        try:
            groups = await asyncio.wait_for(
                recent_sessions(source, "session.id", USE_SESSIONS), USE_SECONDS
            )
        except Exception:
            _logger.warning("discover: %s's traces couldn't be read", job.id, exc_info=True)
            return None
        finally:
            await source.close()
        if not groups:
            return None
        return dict(match(found, trace_digest(groups)).uses)

    async def _clone(self, job: Job, cloning: dict[str, str]) -> bool:
        """Clone the repository *job* was given by into its folder, with the person's own git
        credentials; whether it was."""
        assert job.clone is not None
        try:
            _, commit = await asyncio.to_thread(
                repository.clone, job.clone, self.data_dir, job.name
            )
        except repository.CloneError as exc:
            cloning.update(text=str(exc), state="problem")
            job.status = "failed"
            return False
        cloning.update(text=f"Cloned {job.clone.said} at {commit[:7]}", state="done")
        return True

    async def _address(self, job: Job, looking: dict[str, str]) -> None:
        """An app given by its address: nothing to read, so what its chat route takes is a first
        guess, which the test message puts right (:mod:`evalution.connect.probe`)."""
        looking["state"] = "done"
        job.options["body"] = {"message": "{message}", "session_id": "{session}"}
        job.options["reply"] = "$.reply"
        if job.options["path"] in ("", "/"):
            job.ask("chat")
        await self._finish(job, {})

    async def _finish(self, job: Job, files: dict[str, str]) -> None:
        job.step(f"Using the {infos()[job.adapter].label}")["state"] = "done"
        for note in net.notes(job.base):
            job.step(note)["state"] = "problem"
        job.traces = infos()[job.adapter].trace_setup(self.traces_url) or setup.advice(
            setup.detect(files), self.traces_url, project_of(job.name)
        )
        job.traces["arriving"] = False
        job.step(SEND_STEPS[0])
        await self.check_traces(job)
        await self._verify(job)
        job.status = "ready"

    async def check_traces(self, job: Job) -> None:
        """Whether spans from the app reach evalution (in the last hour)."""
        if job.traces is None:
            return
        name = infos()[job.adapter].trace_project or job.name
        source = Builtin(self.store, name.removeprefix(TARGET_PREFIX))
        now = time.time()
        try:
            spans = await source.spans(now - ARRIVAL_WINDOW, now, max_spans=1)
            arriving = any(map(is_ai, spans))
        except httpx.HTTPError:
            arriving = False
        finally:
            await source.close()
        job.traces["arriving"] = arriving
        step = next(s for s in job.steps if s["text"] in SEND_STEPS)
        step.update(
            text=SEND_STEPS[1] if arriving else SEND_STEPS[0],
            state="done" if arriving else "problem",
        )

    async def _generic(self, job: Job, files: dict[str, str]) -> None:
        env = await asyncio.to_thread(read_env, job.folder / ENV_FILE, GENERIC_KEYS)
        example = await asyncio.to_thread(example_env, job.folder)
        known = {**{k: v for k, v in example.items() if k in GENERIC_KEYS}, **env}
        if env.get("PHOENIX_API_KEY"):
            job.secrets["phoenix_api_key"] = env["PHOENIX_API_KEY"]
        known.pop("PHOENIX_API_KEY", None)

        looking = job.step("Looking for how to chat with it")
        found = routes(files)
        route = chat_route(found)
        fields = body_fields(files, route) if route else []
        message = _pick(fields, _MESSAGE)
        plan: dict[str, Any] = {}
        if route and message:
            body: dict[str, Any] = {message: "{message}"}
            if message == "messages":
                body = {"messages": [{"role": "user", "content": "{message}"}]}
            if session := _pick(fields, _SESSION):
                body[session] = "{session}"
            plan = {"path": route.path, "body": body, "reply": reply_field(files, route)}
        elif self.llm and files:
            looking["text"] = "Reading the code with the evals model"
            said = scrub_text(digest(files, found, example), set(job.secrets.values()))
            try:
                plan = await ask_model(self.llm, said)
            except LLMError:
                _logger.warning("discover: the evals model did not answer")
        # What the project's files say is material, and may be written to steer this.
        if route_problem(plan):
            plan = {}
        if isinstance(plan.get("path"), str) and isinstance(plan.get("body"), dict):
            job.options = {
                "path": plan["path"],
                "body": plan["body"],
                "reply": plan.get("reply") if isinstance(plan.get("reply"), str) else "$",
            }
            looking["text"] = f"Found where it takes chat messages ({plan['path']})"
            looking["state"] = "done"
        else:
            looking["text"] = "Couldn't tell where it takes chat messages"
            looking["state"] = "problem"
            job.options = {"body": {"message": "{message}", "session_id": "{session}"}}
            job.ask("chat")
        health = next((r.path for r in found if r.method == "GET" and r.path in _HEALTH), None)
        if health or isinstance(plan.get("health"), str):
            job.options["health"] = health or plan["health"]

        port = find_port(files, known)
        if port is None and isinstance(plan.get("port"), int):
            port = plan["port"]
        if port is None:
            deps = " ".join(
                t
                for r, t in files.items()
                if r.rsplit("/", 1)[-1] in ("package.json", "pyproject.toml", "requirements.txt")
            )
            port = next((p for word, p in _DEFAULT_PORTS if word in deps.lower()), 8000)
            job.guessed = True
        job.base = f"http://127.0.0.1:{port}"
        project = plan.get("phoenix_project")
        _tracing(job, files, known, project if isinstance(project, str) else None)

    async def _known(self, job: Job, folder: Folder, info: AdapterInfo, found: Detection) -> None:
        """Fill *job* with what *info*'s adapter read from the folder."""
        job.adapter, job.base, job.options = info.name, found.base, found.options
        job.secrets.update(found.secrets)
        for text, state in found.steps:
            job.step(text)["state"] = state
        if found.reads_tracing:
            env = await asyncio.to_thread(read_env, job.folder / ENV_FILE, GENERIC_KEYS)
            if env.get("PHOENIX_API_KEY"):
                job.secrets["phoenix_api_key"] = env.pop("PHOENIX_API_KEY")
            _tracing(job, await asyncio.to_thread(_files, folder), env)
        else:
            job.phoenix = found.phoenix

    async def _verify(self, job: Job) -> None:
        body = job.body()
        try:
            block, changes = connect.parse(job.name, body, set())
            conf = connect.draft(job.name, block, self.root)
        except connect.TargetError:
            _logger.warning("discover: %s gave a target that does not load", job.id)
            return
        app = job.step("Checking that your app is running")
        traced = job.step("Looking for conversations")
        code = job.step("Letting the agent read your code") if job.folder else None
        overlay = {k: v for k, v in changes.items() if v}
        found = await connect.check(conf, overlay, self.store)

        host = urlsplit(job.base).netloc
        if found["adapter"]["ok"]:
            app.update(text="Your app is running", state="done")
        elif found["adapter"].get("refused"):
            app.update(text=infos()[job.adapter].refused, state="problem")
        else:
            app.update(
                text=f"Your app isn't answering at {host}. Start it; evalution uses it once "
                "it runs.",
                state="problem",
            )
            if job.guessed:
                job.ask("port")

        seen = found["phoenix"]
        if seen["ok"]:
            traced.update(
                text=f"Found {_count(seen['sessions_seen'], 'recent conversation')}",
                state="done",
            )
        else:
            # The step that sends traces to evalution says what to do.
            job.steps.remove(traced)

        files = found["code"]["files_matched"]
        if code is None:
            return
        if files:
            code.update(
                text=f"The agent can read {_count(files, 'file')} of your code", state="done"
            )
        else:
            code.update(text="No code files to read; the agent learns from traces only")
            code["state"] = "problem"
