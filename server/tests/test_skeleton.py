"""The skeleton (ADR 0012, phase 1): what the app is built from, read off the code map with no
model, and how often real use touched each part. Offline: the made-up app, the code map's tiny
app, spans made here."""

import asyncio
import json
import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from test_agent_approval import APP

from evalution.api.app import create_app
from evalution.config import load
from evalution.project import skeleton_view
from evalution.project.codemap.facts import static_facts
from evalution.project.codemap.graph import CodeMap
from evalution.project.codemap.parse import parse
from evalution.project.skeleton import Skeleton, match, skeleton
from evalution.targets import Targets
from evalution.traces.base import Span, nest
from evalution.traces.profile import digest

DEMO = Path(__file__).resolve().parents[1] / "evalution" / "demo" / "app.py"


def _map(files: dict[str, str]) -> CodeMap:
    parsed = {}
    for path, text in files.items():
        found = parse(path, text)
        parsed[path] = {**found, "facts": static_facts(path, text, found)}
    return CodeMap(parsed)


def _found(files: dict[str, str]) -> Skeleton:
    return skeleton(_map(files), files.__getitem__)


def _rows(found: Skeleton, kind: str) -> set[tuple[str, str, int]]:
    return {(e.name, e.path, e.line) for e in found.of(kind)}


def _line(text: str, needle: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


def test_every_prompt_tool_and_store_of_the_tiny_app_is_in_its_skeleton_with_its_line() -> None:
    found = _found(APP)
    assert _rows(found, "prompt") == {
        ("SYSTEM_PROMPT", "app.py", 3),
        ("build_instructions", "app.py", 23),
        ("planner.md", "agents/planner.md", 1),
    }
    system = next(e for e in found.of("prompt") if e.name == "SYSTEM_PROMPT")
    assert system.text == "You greet people."
    planner = next(e for e in found.of("prompt") if e.name == "planner.md")
    assert planner.text == "# Planner\nPlans the day."
    assert _rows(found, "tool") == {
        ("greet_people", "app.py", 6),
        ("search", "agents/worker.yaml", 4),
    }
    assert next(e for e in found.of("tool") if e.name == "greet_people").args == ("name",)
    assert _rows(found, "state") == {("MemoryStore", "lib/memory.py", 1)}
    assert _rows(found, "route") == {("POST /chat", "app.py", 11)}
    assert next(iter(found.of("route"))).inside == "chat"
    worker = found.of("agent")
    assert [(e.name, e.path, e.model) for e in worker] == [
        ("worker", "agents/worker.yaml", "small")
    ]
    # A plain function, a constant and a note are not parts of the skeleton.
    names = {e.name for e in found.entries}
    assert not names & {"_wrap", "PREFIX_BUDGET_SECONDS", "sendChat", "MODE"}


def test_the_made_up_apps_tools_store_routes_and_entry_are_in_its_skeleton() -> None:
    text = DEMO.read_text()
    found = _found({"app.py": text})
    # Its tools are used by name where its turn runs them.
    assert _rows(found, "tool") == {
        ("recall", "app.py", _line(text, 'trace.tool("recall"')),
        ("write_file", "app.py", _line(text, 'child.tool("write_file"')),
    }
    assert {e.inside for e in found.of("tool")} == {"_turn", "_delegated"}
    # Its store: every conversation and its memory.
    assert _rows(found, "state") == {("sessions", "app.py", _line(text, "sessions: dict"))}
    routes = {e.name for e in found.of("route")}
    assert {"POST /api/sessions/{sid}/messages", "GET /chat/{sid}", "POST /login"} <= routes
    assert "POST /llm/v1/chat/completions" in routes
    entries = {(e.name, e.what) for e in found.of("entry")}
    assert {("app", "FastAPI"), ("app.py", "script"), ("uvicorn.run", "serves")} <= entries
    # Long strings that are its page's style and script are not prompts.
    assert not found.of("prompt")


def test_the_skeleton_finds_model_calls_schemas_and_stores_across_languages() -> None:
    files = {
        "bot/agent.py": (
            "import sqlite3\n"
            'MODEL = "claude-sonnet-4-5"\n'
            'SYSTEM_PROMPT = """You are a support agent for a made-up shop.\n'
            'Answer briefly and cite the order number."""\n'
            "TOOLS = [\n"
            '    {"name": "search_docs", "input_schema": {"type": "object",'
            ' "properties": {"query": {}, "k": {}}}},\n'
            "]\n"
            'db = sqlite3.connect("bot.db")\n\n'
            "def answer(question):\n"
            "    # client.messages.create(model='an-old-one')\n"
            "    return client.messages.create(\n"
            "        model=MODEL, system=SYSTEM_PROMPT, tools=TOOLS, messages=[]\n"
            "    )\n"
        ),
        "web/app/api/chat/route.ts": (
            'import { streamText, tool } from "ai";\n'
            "const getWeather = tool({\n"
            "  parameters: z.object({ city: z.string(), days: z.number() }),\n"
            "});\n"
            "export async function POST(req: Request) {\n"
            '  return streamText({ model: openai("gpt-4o-mini"), tools: { getWeather } });\n'
            "}\n"
        ),
        "svc/main.go": (
            'package main\nfunc main() {\n    http.HandleFunc("POST /v1/chat", chat)\n}\n'
        ),
    }
    found = _found(files)
    tools = {e.name: e for e in found.of("tool")}
    assert tools["search_docs"].args == ("query", "k") and tools["search_docs"].line == 6
    assert tools["getWeather"].args == ("city", "days")
    models = {(e.name, e.path, e.line, e.model, e.inside) for e in found.of("model")}
    assert models == {
        ("messages.create", "bot/agent.py", 12, "claude-sonnet-4-5", "answer"),
        ("streamText", "web/app/api/chat/route.ts", 6, "gpt-4o-mini", "POST"),
    }
    assert {e.name for e in found.of("route")} == {"POST /api/chat", "POST /v1/chat"}
    assert [(e.name, e.what) for e in found.of("state")] == [("db", "database")]
    assert ("main", "svc/main.go", 2) in _rows(found, "entry")


def test_the_skeleton_is_the_same_each_time_and_reuses_what_did_not_move() -> None:
    first = _found(APP)
    shuffled = dict(reversed(list(APP.items())))
    assert _found(shuffled) == first
    assert Skeleton.of_dict(json.loads(json.dumps(first.as_dict()))) == first
    # A file that moved is read again; the rest come from what was kept, unread.
    moved = {**APP, "lib/memory.py": "class ChatHistory:\n    pass\n"}
    read: list[str] = []

    def reading(path: str) -> str:
        read.append(path)
        return moved[path]

    again = skeleton(_map(moved), reading, first)
    assert read == ["lib/memory.py"]
    assert again == _found(moved) and again.fingerprint != first.fingerprint
    assert _rows(again, "state") == {("ChatHistory", "lib/memory.py", 1)}


def _span(sid: str, kind: str, **attrs) -> Span:
    return Span(sid, "t1", None, sid, kind, 1.0, 2.0, "OK", nest({"session.id": "s1", **attrs}))


def test_real_use_joins_the_skeleton_by_name_and_what_it_cannot_place_is_listed() -> None:
    found = _found(APP)
    system = "You greet people."
    spans = [
        _span("a", "AGENT"),
        *(_span(f"t{n}", "TOOL", **{"tool.name": "greet_people"}) for n in range(3)),
        _span("u", "TOOL", **{"tool.name": "fetch_weather"}),
        _span(
            "m",
            "LLM",
            **{
                "llm.model_name": "made-up-model-2",
                "input.value": json.dumps({"messages": [{"role": "system", "content": system}]}),
            },
        ),
    ]
    hits = match(found, digest([spans]))
    greet = next(e for e in found.of("tool") if e.name == "greet_people")
    prompt = next(e for e in found.of("prompt") if e.name == "SYSTEM_PROMPT")
    assert hits.uses[greet.id] == 3
    assert hits.uses[prompt.id] == 1
    assert list(hits.unseen) == [
        {"kind": "tool", "name": "fetch_weather", "calls": 1},
        {"kind": "model", "name": "made-up-model-2", "calls": 1},
    ]
    assert (hits.sessions, hits.asks) == (1, 1)
    assert match(found, None).uses == {}


def test_a_prompt_with_what_the_code_fills_in_matches_by_its_first_words() -> None:
    text = (
        "SYSTEM_PROMPT = f\"You are the made-up shop's helper and you answer in short lines. "
        'The person is {name}."\n'
    )
    found = _found({"bot.py": text})
    sent = "You are the made-up shop's helper and you answer in short lines. The person is Ann."
    hits = match(
        found,
        {
            "sessions": 1,
            "asks": 2,
            "prompts": [{"hash": "nope", "calls": 2, "head": sent, "sections": []}],
        },
    )
    assert hits.uses == {found.of("prompt")[0].id: 2} and not hits.unseen


def _repo(tmp_path: Path, code: bool) -> Path:
    repo = tmp_path / "app"
    for rel, text in APP.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    block = {"adapter": "http", "http": {"send": {"url": "x"}}}
    if code:
        block["code"] = {"path": "app", "include": ["**/*.py", "**/*.ts", "agents/**"]}
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"web_dir": "none", "targets": {"bot": block}})
    )
    return tmp_path / "evalution.yaml"


def test_the_api_shows_the_skeleton_with_no_learn_and_refuses_with_no_code(tmp_path) -> None:
    with TestClient(create_app(load(_repo(tmp_path, code=True)))) as client:
        found = client.get("/v1/targets/bot/skeleton")
        assert found.status_code == 200, found.text
        body = found.json()
        rows = {(e["kind"], e["name"], e["path"], e["line"]) for e in body["entries"]}
        assert ("tool", "greet_people", "app.py", 6) in rows
        assert ("prompt", "SYSTEM_PROMPT", "app.py", 3) in rows
        # No traces yet: nothing to count, and nothing said about use.
        assert body["window"] is None and body["unseen"] == []
        assert {e["uses"] for e in body["entries"]} == {None}
        # Asked again with nothing moved: the same, kept.
        assert client.get("/v1/targets/bot/skeleton").json() == body
    with TestClient(create_app(load(_repo(tmp_path / "bare", code=False)))) as client:
        refused = client.get("/v1/targets/bot/skeleton")
        assert refused.status_code == 409
        assert "no code folder" in refused.json()["detail"]


def test_the_api_counts_use_from_what_it_kept(tmp_path, monkeypatch) -> None:
    async def used(targets, name, now=None):
        return {
            "sessions": 4,
            "asks": 9,
            "tools": {"greet_people": {"calls": 140}, "mystery": {"calls": 2}},
            "since": 1.0,
            "until": 2.0,
            "taken_at": 3.0,
        }

    monkeypatch.setattr(skeleton_view, "uses", used)
    with TestClient(create_app(load(_repo(tmp_path, code=True)))) as client:
        body = client.get("/v1/targets/bot/skeleton").json()
    greet = next(e for e in body["entries"] if e["name"] == "greet_people")
    search = next(e for e in body["entries"] if e["name"] == "search")
    assert (greet["uses"], search["uses"]) == (140, 0)
    # A prompt built in code has no text to match: nothing is said of its use.
    assert next(e for e in body["entries"] if e["name"] == "build_instructions")["uses"] is None
    assert body["unseen"] == [{"kind": "tool", "name": "mystery", "calls": 2}]
    assert body["window"]["sessions"] == 4 and body["window"]["since"] == 1.0


def test_what_real_use_touched_is_kept_a_while_and_read_again_after(tmp_path, monkeypatch) -> None:
    reads: list[int] = []

    async def recent(source, attribute, sessions):
        reads.append(sessions)
        return [[_span("a", "AGENT"), _span("t", "TOOL", **{"tool.name": "greet_people"})]]

    monkeypatch.setattr(skeleton_view, "recent_sessions", recent)
    targets = Targets(load(_repo(tmp_path, code=True)))
    first = asyncio.run(skeleton_view.uses(targets, "bot", now=1_000.0))
    assert first["tools"] == {"greet_people": {"calls": 1, "offered": False}}
    assert (first["since"], first["until"]) == (1.0, 2.0)
    assert asyncio.run(skeleton_view.uses(targets, "bot", now=1_060.0)) == first
    later = 1_000.0 + skeleton_view.USES_SECONDS + 1
    assert asyncio.run(skeleton_view.uses(targets, "bot", now=later))["taken_at"] == later
    assert len(reads) == 2
    # Nothing seen yet: read again within the minute, so the first traces show soon.
    monkeypatch.setattr(skeleton_view, "recent_sessions", lambda *a: _none(reads))
    empty = asyncio.run(skeleton_view.uses(targets, "bot", now=later + skeleton_view.USES_SECONDS))
    assert empty["sessions"] == 0
    soon = later + skeleton_view.USES_SECONDS + skeleton_view.NONE_SECONDS + 1
    asyncio.run(skeleton_view.uses(targets, "bot", now=soon))
    assert len(reads) == 4


async def _none(reads: list[int]) -> list:
    reads.append(0)
    return []


def _agent_config(name: str, prompt: str = "You help with files.") -> str:
    return (
        f"name: {name}\n"
        f'prompt: "{prompt}"\n'
        "tools:\n"
        "  allow:\n"
        "    - list_files\n"
        "    - session_list\n"
        "    - web_fetch\n"
        "  deny:\n"
        "    - shell\n"
        "  agents:\n"
        "    - b\n"
    )


#: A made-up app shaped like a big one: tool classes, many agents listing the same tools, copies of
#: an agent's settings, docs and decision records beside its prompts, entry points in manifests.
BIGGER = {
    "engine/tools/base.py": "class Tool:\n    def name(self):\n        raise NotImplementedError\n",
    "engine/tools/listing.py": (
        "class ListFilesTool(Tool):\n"
        "    @classmethod\n"
        "    def name(cls) -> str:\n"
        '        """The name."""\n'
        '        return "list_files"\n\n'
        "    def run(self, folder, limit=10):\n"
        "        return []\n\n\n"
        "class SessionListTool(Tool):\n"
        '    name = "session_list"\n\n\n'
        "class _HelperTool(Tool):\n"
        "    pass\n\n\n"
        "BRIEF = open('notes/brief.md').read()\n"
        "SESSION_HISTORY_LIMIT = 40\n"
        'AGENT_NOTICE = "When a helper finishes, say so in one line and nothing more than that."\n'
    ),
    "agents/a/config.yaml": _agent_config("a"),
    "agents/b/config.yaml": _agent_config("b", "You keep notes."),
    "agents/c/config.yaml": _agent_config("c", "You plan."),
    "bundles/x/agents/a/config.yaml": _agent_config("a"),
    "prompts/system.md": "# Who you are\nA helper.\n\n## Rules\nBe brief.\n\n## Tools\nUse them.\n",
    "notes/brief.md": "# Brief\nSay what changed.\n",
    "notes/other.md": "# Other\nNot loaded.\n",
    "docs/adr/0001-agents-decide.md": (
        "# Agents decide\n## Decision\nYes.\n## Consequences\nMore.\n"
    ),
    "README.md": "# The app\n## Prompts\nSee prompts/.\n",
    "server/main.py": (
        "import uvicorn\n"
        "from fastapi import FastAPI\n\n\n"
        "def create_app():\n"
        "    app = FastAPI()\n"
        "    return app\n\n\n"
        'if __name__ == "__main__":\n'
        '    uvicorn.run("server.main:create_app", factory=True)\n'
    ),
    "tests/test_main.py": 'if __name__ == "__main__":\n    pass\n',
    "pyproject.toml": (
        '[project]\nname = "made-up"\n\n[project.scripts]\nmade-up = "server.cli:main"\n'
    ),
    "package.json": json.dumps(
        {
            "name": "made-up-web",
            "main": "dist/index.js",
            "bin": {"made-up-web": "bin/cli.js"},
            "scripts": {"start": "node dist/index.js"},
        },
        indent=2,
    ),
    "Dockerfile": 'FROM python:3.12\nCMD ["uvicorn", "server.main:app", "--port", "8000"]\n',
    "server/llm.py": (
        "from litellm import completion\n\n\n"
        "def ask(client, settings, text):\n"
        "    client.chat.completions.create(model=settings.chat_model, messages=[])\n"
        '    anthropic.messages.create(model="claude-sonnet-4-5", messages=[])\n'
        '    completion(model="gpt-4o-mini", messages=[])\n'
        '    return ChatOpenAI(model="gpt-4o")\n'
    ),
    "config/models.yaml": 'chat_model: gpt-4.1-mini\nlight_model: "{{LIGHT}}"\nmax_steps: 4\n',
}


def test_a_tool_is_one_entry_however_many_agents_list_it() -> None:
    found = _found(BIGGER)
    tools = {e.name: e for e in found.of("tool")}
    assert set(tools) == {"list_files", "session_list", "web_fetch"}
    listed = tools["list_files"]
    # Where it is defined, its arguments, and the four agents' settings that list it, folded.
    assert (listed.path, listed.line, listed.what) == ("engine/tools/listing.py", 1, "class")
    assert listed.args == ("folder", "limit")
    assert listed.agents == 4 and len(listed.places) == 4
    assert "agents/b/config.yaml:5" in listed.places
    assert (tools["session_list"].path, tools["session_list"].agents) == (
        "engine/tools/listing.py",
        4,
    )
    # Only in settings: its first listing, the others folded.
    assert (tools["web_fetch"].path, tools["web_fetch"].what) == ("agents/a/config.yaml", "setting")
    # A tool named like a session is not state; a limit or a text in a constant is neither.
    assert not found.of("state")
    assert not found.of("agent") or {e.name for e in found.of("agent")} == {"a", "b", "c"}
    agent_a = next(e for e in found.of("agent") if e.name == "a")
    assert agent_a.places == ("bundles/x/agents/a/config.yaml:1",)


def test_markdown_is_a_prompt_only_where_prompts_live_and_one_per_file() -> None:
    found = _found(BIGGER)
    files = {(e.name, e.path) for e in found.of("prompt") if e.what == "file"}
    assert files == {("system.md", "prompts/system.md"), ("brief.md", "notes/brief.md")}
    system = next(e for e in found.of("prompt") if e.name == "system.md")
    assert system.line == 1 and "## Rules" in system.text
    settings = {e.name: e for e in found.of("prompt") if e.what == "setting"}
    assert set(settings) == {"a (prompt)", "b (prompt)", "c (prompt)"}
    assert settings["a (prompt)"].places == ("bundles/x/agents/a/config.yaml:2",)


def test_entry_points_come_from_factories_runners_and_manifests() -> None:
    found = _found(BIGGER)
    entries = {(e.name, e.path, e.what) for e in found.of("entry")}
    assert entries == {
        ("create_app", "server/main.py", "FastAPI app factory"),
        ("server.main:create_app", "server/main.py", "uvicorn"),
        ("main.py", "server/main.py", "script"),
        ("uvicorn.run", "server/main.py", "serves"),
        ("made-up", "pyproject.toml", "command"),
        ("dist/index.js", "package.json", "main"),
        ("made-up-web", "package.json", "command"),
        ("npm start", "package.json", "script"),
        ("uvicorn server.main:app --port 8000", "Dockerfile", "Cmd"),
    }


def test_model_calls_say_their_model_or_where_it_comes_from() -> None:
    found = _found(BIGGER)
    models = {(e.name, e.model, e.model_from, e.what) for e in found.of("model")}
    assert models == {
        ("chat.completions.create", "", "settings.chat_model", "call"),
        ("messages.create", "claude-sonnet-4-5", "", "call"),
        ("completion", "gpt-4o-mini", "", "call"),
        ("ChatOpenAI", "gpt-4o", "", "client"),
        ("gpt-4.1-mini", "gpt-4.1-mini", "chat_model", "setting"),
    }
