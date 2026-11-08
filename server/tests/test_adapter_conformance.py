"""Every adapter does what it declares, and nothing it doesn't. Offline: port 1 refuses at once, so
"it tries" shows as an HTTP error rather than an answer. The contract kit, what anyone's adapter can
run over a real target, plays here against scripted ones and a local stand-in app."""

import asyncio
import dataclasses
import http.server
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from evalution import adapters, doctor
from evalution.adapters import build, contract, infos, own_traffic
from evalution.adapters.base import (
    NO_FILES,
    NO_STOP,
    AdapterInfo,
    Capabilities,
    Isolation,
    Session,
    StateResult,
    Target,
    Turn,
    part,
)
from evalution.api.app import create_app
from evalution.cli import main
from evalution.config import TargetConfig, load, page_targets
from evalution.core.case import CLEAN_KINDS
from evalution.core.evidence import Failure
from evalution.core.trace import Section
from evalution.project.understanding.computed import stack
from evalution.targets import Targets
from evalution.traces.base import Span
from evalution.traces.profile import is_eval
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionStore

#: What every adapter's config and session need, side by side.
APP = "http://127.0.0.1:1"
OPTIONS = {"url": APP, "assistant_id": "agent", "http": {"send": {"url": f"{APP}/chat"}}}
EXTRA = {"email": "eval+r@shop.example", "user": "eval-r", "run": "r"}


@pytest.fixture(params=list(infos()))
def target(request, monkeypatch) -> Target:
    monkeypatch.setattr("evalution.adapters._built", {})
    return build(TargetConfig("conformance", request.param, None, None, OPTIONS))


@pytest.fixture
def session() -> Session:
    return Session("s", "session.id", "s", dict(EXTRA))


def test_a_method_is_replaced_exactly_when_declared(target: Target) -> None:
    declared = target.capabilities
    assert isinstance(target, Target)
    assert (type(target).reset is not Target.reset) == declared.reset
    assert (type(target).break_conversation is not Target.break_conversation) == (
        declared.conversation_break
    )
    assert (type(target).sections is not Target.sections) == declared.sections
    assert (type(target).state is not Target.state) == bool(declared.state_checks)
    assert (type(target).probe is not Target.probe) == declared.probe
    assert not declared.probe or declared.state_checks, "a probe reads state checks"
    # Declared by what a target sets, like cancel: undeclared, it raises (see below).
    assert not declared.watch or type(target).watch is not Target.watch


async def test_files_and_stop_are_refused_exactly_when_not_declared(
    target: Target, session: Session, tmp_path
) -> None:
    file = tmp_path / "note.txt"
    file.write_text("hi")
    deadline = time.time() + 5
    with_file = await target.send(session, "hi", files=[file], deadline=deadline)
    assert (with_file.error == NO_FILES) == (not target.capabilities.files)
    stopped = await target.send(session, "hi", stop_after=0.01, deadline=deadline)
    assert (stopped.error == NO_STOP) == (not target.capabilities.stop)


async def test_state_checks_are_answered_exactly_when_declared(
    target: Target, session: Session
) -> None:
    for key in target.capabilities.state_checks:
        with pytest.raises(httpx.HTTPError):  # it went to the app
            await target.state(session, {key: "x"})
        with pytest.raises(httpx.HTTPError if target.capabilities.probe else NotImplementedError):
            await target.probe(session, {key: "x"})
    found = await target.state(session, {"no_such_check": "x"})
    assert not found.ok and "has no" in found.reason and not found.observed


async def test_cancel_is_answered_exactly_when_declared(target: Target, session: Session) -> None:
    if target.capabilities.cancel:
        with pytest.raises(httpx.HTTPError):  # it went to the app
            await target.cancel(session)
    else:
        with pytest.raises(NotImplementedError):
            await target.cancel(session)
    # The http adapter declares it by what its target sets, and honours that the same way.
    http = {"http": {**OPTIONS["http"], "cancel": {"url": f"{APP}/stop/{{session}}"}}}
    told = build(TargetConfig("cancels", "http", None, None, http))
    assert told.capabilities.cancel
    with pytest.raises(httpx.HTTPError):
        await told.cancel(session)


async def test_watch_is_answered_exactly_when_declared(target: Target, session: Session) -> None:
    if target.capabilities.watch:
        assert type(target).watch is not Target.watch
        turn = await target.watch(session, time.time(), deadline=time.time() + 5)
        assert turn.error and turn.heard  # it went to the app, which had the page's message
    else:
        with pytest.raises(NotImplementedError):
            await target.watch(session, time.time(), deadline=time.time() + 5)
    # The http adapter declares it by what its target sets: a poll it waits on.
    poll = {"poll": {"url": f"{APP}/state/{{session}}", "until": "$.done == true"}}
    told = build(
        TargetConfig("watches", "http", None, None, {"http": {**OPTIONS["http"], "done": poll}})
    )
    assert told.capabilities.watch
    turn = await told.watch(session, time.time(), deadline=time.time() + 5)
    assert turn.failure is Failure.UNREACHABLE and turn.heard


def test_nothing_is_deleted_unless_the_target_asks(target: Target) -> None:
    assert not target.capabilities.deletes


def test_the_registry_finds_each_module_under_its_own_name() -> None:
    assert list(infos()) == ["http", "langgraph", "letta"]
    for name, info in infos().items():
        assert info.name == name and set(info.required) <= set(info.options)


def test_what_the_api_says_each_can_do(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_built", {})
    said = {
        name: build(TargetConfig(name, name, None, None, OPTIONS)).capabilities.names()
        for name in infos()
    }
    assert said == {
        "http": ["isolation", "parallel"],
        "langgraph": [
            "blocks",
            "cancel",
            "conversation_break",
            "isolation",
            "parallel",
            "reset",
            "state",
        ],
        "letta": [
            "blocks",
            "cancel",
            "conversation_break",
            "isolation",
            "parallel",
            "reset",
            "state",
        ],
    }


def test_isolation_is_said_in_one_sentence(target: Target) -> None:
    declared = target.capabilities
    assert declared.isolation is not Isolation.NONE
    assert declared.isolation_note.endswith(".") and declared.isolation_note.count(". ") == 0
    assert declared.names() == sorted(declared.names())


@pytest.fixture
def fourth_adapter(monkeypatch):
    """``fixtures/adapters/echo.py`` in the adapters' folder, as if someone had dropped it there."""
    fixtures = Path(__file__).parent / "fixtures" / "adapters"
    monkeypatch.setattr(adapters, "__path__", [*adapters.__path__, str(fixtures)])
    monkeypatch.setattr(adapters, "_built", {})
    infos.cache_clear()
    own_traffic.cache_clear()
    try:
        yield
    finally:
        monkeypatch.undo()
        sys.modules.pop("evalution.adapters.echo", None)
        infos.cache_clear()
        own_traffic.cache_clear()


def test_a_fourth_adapter_is_one_module(fourth_adapter, tmp_path) -> None:
    assert "echo" in infos()
    assert stack(None, "echo", None) == ["Echo adapter"]
    span = Span("s", "t", None, "n", "LLM", 0.0, 1.0, "OK", {"echo": {"eval": {"run": "r"}}})
    assert is_eval("real", [span])

    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {}}))
    app = tmp_path / "app"
    app.mkdir()
    (app / "echo.toml").write_text("")
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        # Discovery proposes it, and connecting saves it.
        job = client.post("/v1/targets/discover", json={"path": str(app)}).json()["id"]
        deadline = time.time() + 10
        while (found := client.get(f"/v1/targets/discover/{job}").json())["status"] == "working":
            assert time.time() < deadline
            time.sleep(0.02)
        assert found["target"]["adapter"] == "echo", found
        assert found["target"]["options"] == {"url": "http://127.0.0.1:7777"}
        assert "It's an echo app" in [s["text"] for s in found["steps"]]
        saved = client.post(f"/v1/targets/discover/{job}/accept", json={})
        assert saved.status_code == 201, saved.text
        assert saved.json()["options"] == {"url": "http://127.0.0.1:7777"}

        # The check asks the adapter, and its options and secrets are its own.
        checked = client.post("/v1/targets/app/reach").json()
        assert checked["adapter"] == {"ok": True, "detail": "It echoes"}
        body = {"adapter": "echo", "options": {"url": "u"}, "secrets": {"api_key": "k"}}
        assert client.put("/v1/targets/app", json=body).status_code == 200
        saved = page_targets(client.app.state.runs.targets.docs)["app"]
        assert saved["api_key_env"] == "targets/app/api_key"
        bad = client.put("/v1/targets/app", json={"adapter": "echo", "options": {"x": "u"}})
        assert bad.json()["detail"] == "options.x: not an option of the echo adapter"

        # The list and the test writer see what it can do.
        rows = client.get("/v1/targets").json()["data"]
        assert rows[0]["capabilities"] == ["isolation", "parallel"] and not rows[0]["error"]
        runs = client.app.state.runs
        writer = Agent(runs, SessionStore(runs.targets.docs), None)
        session = {"target": "app", "set": "main"}
        said = writer._instructions(session)
        assert "adapter 'echo', capabilities: isolation, parallel" in said


# The contract kit


@dataclasses.dataclass(frozen=True)
class WithCancel(Capabilities):
    """As a later evalution might have it: a capability this kit has never heard of."""

    cancel: bool = True


class Scripted(Target):
    """Keeps the whole contract, until a test breaks one part of it."""

    capabilities = WithCancel(
        isolation=Isolation.IDENTITY,
        isolation_note="A new user.",
        reset=True,
        conversation_break=True,
        sections=True,
        files=True,
        stop=True,
        parallel=True,
        state_checks=frozenset({"memory_contains"}),
        probe=True,
    )

    def __init__(self) -> None:
        self.name = "scripted"
        self.sent: list[str] = []
        self.closed: list[Session] = []
        self.spoken: set[str] = set()
        self.count = 0

    async def open(self, run: str) -> Session:
        self.count += 1
        return Session(f"s{self.count}", "session.id", f"s{self.count}", {"run": run})

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
        self.sent.append(message)
        self.spoken.add(session.id)
        return Turn("OK", 1.0, 2.0)

    async def close(self, session) -> None:
        self.closed.append(session)

    async def reset(self, session) -> None:
        return None

    async def break_conversation(self, session) -> None:
        session.trace_value += "-next"

    async def state(self, session, check) -> StateResult:
        return StateResult(False, "none", "nothing holds it")

    async def probe(self, session, check) -> StateResult:
        assert session.id not in self.spoken, "a probe reads a session yet to send anything"
        return StateResult(False, "none", "nothing holds it")

    def sections(self, request) -> list[Section]:
        return [Section("turns", "turns", "hi")]


def _not_found() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://app.local/v1/memory?key=fake-secret")
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError("404", request=request, response=response)


def _fails_with(exc: BaseException):
    async def method(self, *args, **kwargs):
        raise exc

    return method


async def _same_ids(self, run: str) -> Session:
    return Session("same", "session.id", "same")


async def _same_trace(self, run: str) -> Session:
    self.count += 1
    return Session(f"s{self.count}", "session.id", "same")


async def _no_reply(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
    return Turn("  ", 1.0, 2.0)


async def _hangs_on_long(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
    if stop_after is None and message == contract.LONG_MESSAGE:
        await asyncio.sleep(60)
    return Turn("OK", 1.0, 2.0)


async def _late_with_no_error(
    self, session, message, *, files=(), stop_after=None, deadline
) -> Turn:
    if stop_after is None and message == contract.LONG_MESSAGE:
        await asyncio.sleep(max(deadline - time.time(), 0) + 1.2)
    return Turn("OK", 1.0, 2.0)


async def _refuses_files(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
    return Turn("", 1.0, 1.0, error=NO_FILES) if files else Turn("OK", 1.0, 2.0)


async def _unobserved(self, session, check) -> StateResult:
    return StateResult(False, None, "the store didn't answer", observed=False)


BROKEN = {
    "same session ids": ("open", _same_ids, "open gives unique", "session ids repeat"),
    "same trace values": ("open", _same_trace, "open gives unique", "trace values repeat"),
    "open raises": (
        "open",
        _fails_with(httpx.ConnectError("refused")),
        "open gives unique",
        "couldn't reach the app",
    ),
    "empty reply": ("send", _no_reply, "send returns", "empty reply"),
    "send raises": (
        "send",
        _fails_with(RuntimeError("boom")),
        "send returns",
        "an app's failure is Turn.error",
    ),
    "past the deadline": ("send", _hangs_on_long, "the deadline holds", "still out"),
    "late, no error": ("send", _late_with_no_error, "the deadline holds", "no Turn.error"),
    "close raises": ("close", _fails_with(KeyError("host")), "close doesn't", "KeyError"),
    "reset missing": (
        "reset",
        _fails_with(NotImplementedError("no reset")),
        "reset",
        "doesn't implement it",
    ),
    "break has no endpoint": (
        "break_conversation",
        _fails_with(_not_found()),
        "conversation_break",
        "not supported by this app: GET http://app.local/v1/memory answered 404",
    ),
    "state has no endpoint": (
        "state",
        _fails_with(_not_found()),
        "state check memory_contains",
        "not supported by this app",
    ),
    "state not read": ("state", _unobserved, "state check memory_contains", "couldn't read it"),
    "probe not read": ("probe", _unobserved, "probe memory_contains", "couldn't read it"),
    "files refused": ("send", _refuses_files, "files", "NO_FILES"),
    "no sections": ("sections", lambda self, request: [], "sections", "no sections"),
}


async def _play(target: Target, **given) -> contract.Report:
    info = AdapterInfo("scripted", "Scripted", (), (), (), None)
    return await contract.run(target, info, short=0.1, grace=1.5, settle=0.05, **given)


async def test_the_kit_passes_what_keeps_the_contract_and_spends_what_it_said() -> None:
    target = Scripted()
    steps: list[contract.Step] = []

    async def spans(session, start, end) -> int:
        assert session.trace_value == "s1"
        return 3

    found = await _play(target, spans=spans, on_step=steps.append)
    assert found.ok, found.steps
    assert found.steps == steps
    assert found.messages == len(target.sent) == contract.messages_needed(target.capabilities) == 6
    assert {s.id for s in target.closed} == {"s1", "s2"}
    assert {s.name for s in found.steps if s.status == "skipped"} == {
        "parallel",
        "cancel",
    }, "declared and never tried is said, a capability it has never heard of included"
    assert next(s for s in found.steps if s.name == "the trace value finds spans").status == "ok"


@pytest.mark.parametrize("name", list(BROKEN))
async def test_the_kit_finds_each_broken_promise_in_words(name: str, monkeypatch) -> None:
    method, broken, step, words = BROKEN[name]
    monkeypatch.setattr(Scripted, method, broken)
    found = await _play(Scripted())
    assert not found.ok
    failed = [s for s in found.steps if s.status == "failed"]
    assert any(s.name.startswith(step) and words in s.detail for s in failed), found.steps
    assert "fake-secret" not in repr(found.steps), "a URL's query can hold a key"
    assert "Traceback" not in repr(found.steps)


async def test_the_kit_says_when_the_trace_value_finds_nothing() -> None:
    async def none(session, start, end) -> int:
        return 0

    found = await _play(Scripted(), spans=none)
    (step,) = [s for s in found.steps if s.status == "failed"]
    assert step.name == "the trace value finds spans"
    assert "session.id = s1" in step.detail
    no_source = await _play(Scripted())
    assert next(s for s in no_source.steps if "trace" in s.name).status == "skipped"


async def test_a_session_that_only_memory_could_rebuild_is_found(monkeypatch) -> None:
    async def holds_a_live_thing(self, run) -> Session:
        self.count += 1
        return Session(f"s{self.count}", "session.id", f"s{self.count}", {"live": object()})

    monkeypatch.setattr(Scripted, "open", holds_a_live_thing)
    found = await _play(Scripted())
    (closed,) = [s for s in found.steps if s.name == "close doesn't raise"]
    assert closed.status == "failed" and "TypeError" in closed.detail


def test_declaring_what_isnt_there_shows_without_spending() -> None:
    class Claims(Target):
        capabilities = Capabilities(Isolation.SESSION, "New id.", reset=True)
        name = "claims"
        open = Scripted.open
        send = Scripted.send
        close = Scripted.close

    steps = contract._Sequence(Claims(), None, 1, 1, 1)
    steps.shape(None)
    assert [s.status for s in steps.report.steps] == ["failed", "failed"]
    assert "reset" in steps.report.steps[0].detail
    assert "INFO" in steps.report.steps[1].detail


def test_declaring_watch_without_the_method_shows() -> None:
    class Claims(Target):
        capabilities = Capabilities(Isolation.SESSION, "New id.", watch=True)
        name = "claims"
        open = Scripted.open
        send = Scripted.send
        close = Scripted.close

    steps = contract._Sequence(Claims(), None, 1, 1, 1)
    steps.shape(None)
    assert steps.report.steps[0].status == "failed" and "watch" in steps.report.steps[0].detail

    class Watches(Claims):
        async def watch(self, session, since, *, deadline) -> Turn:
            return Turn("OK", since, time.time())

    steps = contract._Sequence(Watches(), None, 1, 1, 1)
    steps.shape(None)
    assert steps.report.steps[0].status == "ok"


# A new adapter, and which versions an app must be


class ChatApp(http.server.BaseHTTPRequestHandler):
    """An app of the shape the starter says, on this computer."""

    def _answer(self, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._answer({"ok": True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.said.append(body)
        self._answer({"reply": "OK"})

    def log_message(self, *_):
        return


@pytest.fixture
def chat_app():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ChatApp)
    server.said = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def own_folder(tmp_path, monkeypatch):
    """The adapters' folder as it was, once a test has added to it."""
    monkeypatch.setattr(adapters, "__path__", list(adapters.__path__))
    monkeypatch.setattr(adapters, "_built", {})
    before = set(sys.modules)
    try:
        yield tmp_path
    finally:
        monkeypatch.undo()
        for name in set(sys.modules) - before:
            if name.startswith("evalution.adapters."):
                del sys.modules[name]
        infos.cache_clear()
        own_traffic.cache_clear()


def _config(root: Path, adapter: str, url: str) -> str:
    block = {"adapter": adapter, "url": url}
    file = root / "evalution.yaml"
    file.write_text(yaml.safe_dump({"targets": {"t": block}}))
    return str(file)


async def test_the_starter_keeps_the_contract_as_written(own_folder, chat_app, capsys) -> None:
    config = _config(own_folder, "my_app", f"http://127.0.0.1:{chat_app.server_port}")
    new = ["--config", config, "adapter", "new", "my_app"]
    assert await asyncio.to_thread(main, new) == 0
    path = own_folder / "data" / "adapters" / "my_app.py"
    assert path.is_file()
    text = path.read_text()

    # Never over a file that is there, or under a name that can't load.
    path.write_text(text + "# mine\n")
    assert await asyncio.to_thread(main, new) == 2
    assert path.read_text().endswith("# mine\n")
    path.write_text(text)
    for bad in ("http", "contract", "Not-A-Name"):
        args = ["--config", config, "adapter", "new", bad]
        assert await asyncio.to_thread(main, args) == 2
        assert not (path.parent / f"{bad}.py").exists()
    capsys.readouterr()

    # The offline conformance tests hold for it.
    Targets(load(Path(config)))
    target = build(TargetConfig("conformance", "my_app", None, None, OPTIONS))
    test_a_method_is_replaced_exactly_when_declared(target)
    session = Session("s", "session.id", "s", dict(EXTRA))
    await test_files_and_stop_are_refused_exactly_when_not_declared(target, session, own_folder)
    test_isolation_is_said_in_one_sentence(target)
    assert "my_app" in infos()

    # Asking first, and saying how many: a no spends nothing.
    test = ["--config", config, "adapter", "test", "my_app", "--target", "t"]
    adapters._built.clear()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("builtins.input", lambda _: "n")
        assert await asyncio.to_thread(main, test) == 0
    out = capsys.readouterr().out
    assert "this sends 2 messages to t" in out and "not started" in out
    assert chat_app.said == []

    # The whole kit over a real server, with what it said it would spend.
    adapters._built.clear()
    assert await asyncio.to_thread(main, [*test, "--yes"]) == 0
    out = capsys.readouterr().out
    assert "2 messages sent. The adapter keeps its contract" in out
    assert "--  the trace value finds spans: no traces from this target yet" in out
    assert len(chat_app.said) == 2
    assert len({said["session"] for said in chat_app.said}) == 2


async def test_a_setup_problem_is_said_before_anything_is_spent(
    own_folder, chat_app, capsys
) -> None:
    """A wrong version of the app, through the adapter's own check: in the kit and in the doctor."""
    module = own_folder / "data" / "adapters" / "old_app.py"
    config = _config(own_folder, "old_app", f"http://127.0.0.1:{chat_app.server_port}")
    assert await asyncio.to_thread(main, ["--config", config, "adapter", "new", "old_app"]) == 0
    text = module.read_text()
    text = text.replace(
        'return part(True, "It answers")',
        "from evalution.adapters.contract import version_part\n"
        '    return version_part("The app", "2.4.0", INFO.supports)',
    ).replace('# supports=">=1.2,<2",', 'supports=">=1.2,<2",')
    module.write_text(text)
    capsys.readouterr()
    adapters._built.clear()
    args = ["--config", config, "adapter", "test", "old_app", "--target", "t", "--yes"]
    assert await asyncio.to_thread(main, args) == 1
    err = capsys.readouterr().err
    assert "Setup problem: The app is version 2.4.0, and this adapter supports >=1.2,<2" in err
    assert "update the adapter" in err
    assert chat_app.said == []

    loaded = load(Path(config))
    findings = await doctor.examine(loaded, Targets(loaded))
    wrong = next(f for f in findings if "version 2.4.0" in f.what)
    assert not wrong.ok and "update the adapter" in wrong.fix


def test_a_module_that_doesnt_load_is_said_without_a_stack_trace(own_folder, capsys) -> None:
    folder = own_folder / "data" / "adapters"
    folder.mkdir(parents=True)
    (folder / "has_a_bug.py").write_text("raise TypeError('a bug')\n")
    config = _config(own_folder, "has_a_bug", "http://127.0.0.1:1")
    args = ["--config", config, "adapter", "test", "has_a_bug", "--target", "t", "--yes"]
    assert main(args) == 2
    assert "doesn't load: TypeError: a bug" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("found", "supports", "fits"),
    [
        ("1.4.2", ">=1.2,<2", True),
        ("v1.2", ">=1.2,<2", True),
        ("2", ">=1.2,<2", False),
        ("1.1.9", ">=1.2,<2", False),
        ("1.0", "==1", True),
        ("nightly", ">=1.2", False),
    ],
)
def test_a_version_fits_or_doesnt(found, supports, fits) -> None:
    assert contract.version_fits(found, supports) is fits
    assert contract.version_part("App", found, supports)["ok"] is fits
    assert contract.version_part("App", found, None) == part(True, f"App {found}")


def test_a_supports_that_cant_be_read_is_said() -> None:
    with pytest.raises(ValueError, match="isn't like"):
        contract.version_fits("1.0", "about one")


# The compatibility table in docs/adapters.md is this, and fails when it drifts


DOC = Path(__file__).parents[2] / "docs" / "adapters.md"
_MATRIX = re.compile(r"(<!-- matrix:start.*?-->\n)(.*?)(<!-- matrix:end -->)", re.DOTALL)


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _matrix() -> str:
    """One row per built-in adapter, one column per thing :class:`Capabilities` can promise. A new
    field without a column here fails :func:`test_the_compatibility_table_is_what_adapters_declare`
    as well as a changed adapter does."""
    columns = {
        "Isolation": lambda c: c.isolation.value,
        **{
            f"Clean of {kind}": (lambda c, k=kind: _yes(k in c.starts_clean()))
            for kind in CLEAN_KINDS
        },
    }
    plain = [
        f.name
        for f in dataclasses.fields(Capabilities)
        if f.name not in ("isolation", "isolation_note", "clean", "state_checks", "person")
    ]
    columns |= {f"`{name}`": (lambda c, n=name: _yes(getattr(c, n))) for name in plain}
    columns["State checks"] = lambda c: (
        ", ".join(f"`{key}`" for key in sorted(c.state_checks)) or "none"
    )
    columns["Test person"] = lambda c: ", ".join(f"`{key}`" for key in sorted(c.person)) or "none"
    head = ["Adapter", *columns, "App versions"]
    rows = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for name, info in infos().items():
        declared = build(TargetConfig(name, name, None, None, OPTIONS)).capabilities
        said = [f(declared) for f in columns.values()]
        rows.append("| " + " | ".join([f"`{name}`", *said, info.supports or "any"]) + " |")
    return "\n".join(rows) + "\n"


def test_the_compatibility_table_is_what_adapters_declare(monkeypatch) -> None:
    """To change it on purpose: ``UPDATE_DOCS=1 uv run pytest``, on this file."""
    monkeypatch.setattr(adapters, "_built", {})
    text = DOC.read_text(encoding="utf-8")
    found = _MATRIX.search(text)
    assert found, "docs/adapters.md lost its table's markers"
    table = _matrix()
    if os.environ.get("UPDATE_DOCS"):
        DOC.write_text(text[: found.start(2)] + table + text[found.end(2) :], encoding="utf-8")
        return
    assert found.group(2) == table
