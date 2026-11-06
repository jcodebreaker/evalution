"""What an app that isn't one of the built-in ones needs from evalution: a corporate proxy that
can't reach localhost, a gateway's URL however written, its own adapter outside this repo, messages
nested in lists, sessions it makes itself."""

import asyncio
import http.server
import json
import plistlib
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evalution import adapters, service
from evalution.adapters.base import Capabilities, Isolation, Session, Target, Turn
from evalution.adapters.http import HttpTarget, json_path
from evalution.config import MODEL_SECRET, ModelConfig, TargetConfig, load
from evalution.core import net
from evalution.runs.fit import why_not
from evalution.runs.preflight import check_isolation
from evalution.settings import _api_root, draft
from evalution.suite.cases import CaseError, parse_case
from evalution.traces.base import Span
from evalution.traces.view import tool_output


@pytest.fixture
def local_server():
    class Answers(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"direct")

        def log_message(self, *_):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Answers)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()


async def test_a_proxy_in_the_environment_is_not_used_for_this_machine(
    local_server, monkeypatch
) -> None:
    # Nothing listens on this proxy: going through it fails.
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    async with net.client(timeout=5.0) as client:
        answer = await client.get(f"http://127.0.0.1:{local_server}/")
    assert answer.text == "direct"
    # Control: the client evalution used to make goes to the proxy, and fails.
    async with httpx.AsyncClient(timeout=5.0) as client:
        with pytest.raises(httpx.HTTPError):
            await client.get(f"http://127.0.0.1:{local_server}/")


@pytest.mark.parametrize(
    ("given", "root"),
    [
        ("http://gateway:4000", "http://gateway:4000/v1"),
        ("http://gateway:4000/v1", "http://gateway:4000/v1"),
        ("http://gateway:4000/v1/chat/completions", "http://gateway:4000/v1"),
        ("https://corp.test/ai/openai", "https://corp.test/ai/openai"),
    ],
)
def test_a_gateways_url_works_however_it_was_written(given, root) -> None:
    assert _api_root(given) == root


def test_your_own_adapter_can_live_outside_the_repo(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "adapters"
    folder.mkdir()
    (folder / "made_elsewhere.py").write_text(
        "from evalution.adapters.base import AdapterInfo\n"
        "INFO = AdapterInfo('made_elsewhere', 'Made elsewhere', (), (), (), None)\n"
    )
    (folder / "has_a_bug.py").write_text("raise TypeError('a bug')\n")
    monkeypatch.setattr(adapters, "__path__", list(adapters.__path__))
    monkeypatch.delitem(sys.modules, "evalution.adapters.made_elsewhere", raising=False)
    adapters.infos.cache_clear()
    assert "made_elsewhere" not in adapters.infos()
    adapters.add_folder(folder)
    try:
        found = adapters.infos()
        assert "made_elsewhere" in found and "http" in found, "a bug in one breaks only that one"
        assert "has_a_bug" not in found
    finally:
        monkeypatch.undo()
        sys.modules.pop("evalution.adapters.made_elsewhere", None)
        sys.modules.pop("evalution.adapters.has_a_bug", None)
        adapters.infos.cache_clear()
        adapters.own_traffic.cache_clear()


def test_a_reply_can_be_found_where_an_app_really_puts_it() -> None:
    data = {
        "items": [
            {"data": {"role": "user", "content": "Hi"}},
            {"data": {"role": "assistant", "content": "First"}},
            {"data": {"role": "assistant", "content": "Last"}},
            {"data": {"role": "tool", "content": "x"}},
        ]
    }
    path = "$.items[?data.role=='assistant'][-1].data.content"
    assert json_path(data, path) == "Last"
    assert json_path(data, "$.items[-1].data.role") == "tool"
    assert json_path(data, "$.items[-9]") is None
    assert json_path({"items": []}, "$.items[?role=='x'][-1].text") is None


async def test_an_app_can_make_its_own_sessions() -> None:
    seen = []

    def app(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"session": {"id": "made-by-app"}})
        return httpx.Response(200, json={"reply": "Hello"})

    conf = TargetConfig(
        "t",
        "http",
        None,
        None,
        {
            "http": {
                "open": {"url": "http://app/sessions", "id": "$.session.id"},
                "send": {
                    "url": "http://app/sessions/{session}/messages",
                    "body": {"m": "{message}"},
                },
                "reply": "$.reply",
            }
        },
    )
    target = HttpTarget(conf, transport=httpx.MockTransport(app))
    session = await target.open("run")
    assert session.id == session.trace_value == "made-by-app"
    turn = await target.send(session, "Hi", deadline=asyncio.get_running_loop().time() + 1e10)
    assert turn.reply == "Hello"
    assert seen == ["POST /sessions", "POST /sessions/made-by-app/messages"]


class _Remembers(Target):
    capabilities = Capabilities(Isolation.SESSION, "Sessions only.")

    def __init__(self, shares: bool) -> None:
        self.shares, self.said = shares, ""
        self.opened = 0

    async def open(self, run: str) -> Session:
        self.opened += 1
        return Session(run, "session.id", run, {})

    async def send(self, session, message, *, files=(), stop_after=None, deadline):
        if "remember this word" in message:
            self.said = message
            return Turn("Noted.", 0.0, 0.0)
        return Turn(self.said if self.shares else "No.", 0.0, 0.0)

    async def close(self, session) -> None:
        return None


async def test_the_isolation_check_sees_a_leak_and_only_a_leak() -> None:
    assert "knew a word" in await check_isolation(_Remembers(shares=True))
    assert await check_isolation(_Remembers(shares=False)) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="a launchd file")
def test_the_service_file_says_what_to_run(tmp_path) -> None:
    made = plistlib.loads(service.launch_agent(tmp_path / "e&e.yaml", 8099).encode())
    assert made["ProgramArguments"][-5:] == [
        "--config",
        str(tmp_path / "e&e.yaml"),
        "serve",
        "--port",
        "8099",
    ]
    assert made["KeepAlive"] == {"SuccessfulExit": False}
    assert json.dumps(made["RunAtLoad"]) == "true"


def test_a_new_session_is_not_new_memory() -> None:
    case = parse_case(
        {"name": "n", "turns": [{"message": "hi"}], "needs_clean": ["memory"]}, "c", "c"
    )
    session_only = Capabilities(Isolation.SESSION, "New session only.")
    assert "no earlier memory" in why_not(case, session_only, True)
    assert why_not(case, Capabilities(Isolation.IDENTITY, "A new person."), True) is None
    told = Capabilities(Isolation.SESSION, "Memory too.", clean=frozenset({"memory"}))
    assert why_not(case, told, True) is None
    with pytest.raises(CaseError, match="needs_clean"):
        parse_case({"name": "n", "turns": [{"message": "hi"}], "needs_clean": ["moods"]}, "c", "c")


def test_a_tool_result_that_is_plain_data_keeps_its_numbers() -> None:
    def span(out: str) -> Span:
        return Span("a", "t", None, "x", "TOOL", 0.0, 1.0, "OK", {"output": {"value": out}})

    assert json.loads(tool_output(span('{"slides": 2}'))) == {"slides": 2}
    assert tool_output(span('{"text": "wrapped"}')) == "wrapped"
    assert tool_output(span("plain")) == "plain"


def test_a_fresh_install_leaves_the_page_its_forms(tmp_path) -> None:
    file = tmp_path / "evalution.yaml"
    shutil.copy(Path(__file__).parents[2] / "evalution.example.yaml", file)
    fresh = load(file)
    assert fresh.model is None and fresh.model_source != "config", "the page's model form works"
    assert fresh.targets == {}, "no sample app to fail against"
    # Control: what it teaches still loads, once uncommented.
    taught = "\n".join(
        line[2:] if line.startswith(("# model:", "#   ", "# targets:")) else line
        for line in file.read_text().splitlines()
    )
    file.write_text(taught)
    uncommented = load(file)
    assert uncommented.model_source == "config" and {"my-bot", "my-agent"} <= set(
        uncommented.targets
    )


def test_a_saved_model_key_goes_only_to_the_host_it_was_saved_for(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EVALUTION_MODEL_KEY", raising=False)
    keys = load(tmp_path / "evalution.yaml").keys
    keys.store.set(MODEL_SECRET, "fake-saved-key")
    saved = ModelConfig(
        kind="openai",
        base_url="https://gateway.example.test/v1",
        api_key_env="EVALUTION_MODEL_KEY",
        models={"strong": "a", "light": "b"},
    )
    config = SimpleNamespace(model=saved, keys=keys)
    form = {"provider": "compatible", "strong": "a", "light": "b"}
    here = draft(config, {**form, "base_url": "https://gateway.example.test/v2"})
    assert here.api_key() == "fake-saved-key", "control: same host"
    away = draft(config, {**form, "base_url": "https://elsewhere.example.test/v1"})
    assert away.api_key() is None
    typed = draft(config, {**form, "base_url": "https://elsewhere.example.test/v1", "key": "typed"})
    assert typed.api_key() == "typed"
    # One in the environment is the owner's own: it goes where it's pointed, as it does once saved.
    monkeypatch.setenv("EVALUTION_MODEL_KEY", "fake-env-key")
    away = draft(config, {**form, "base_url": "https://elsewhere.example.test/v1"})
    assert away.api_key() == "fake-env-key"
    typed = draft(config, {**form, "base_url": "https://elsewhere.example.test/v1", "key": "typed"})
    assert typed.api_key() == "typed", "control: a typed key still comes first"
