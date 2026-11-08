"""The page target in a real browser (ADR 0010, phase 3), against a tiny chat page served here: the
page sends when the inner adapter can watch and watches when it can't, ``shown`` says what the page
really showed, a broken map is the map's failure, headers reach only the copy's origin, nothing
carries from one try's context to the next, and close and cancel need only ``Session`` fields.

Skipped without the page extra or a browser: ``uv sync --extra page``, and Google Chrome (or
``EVALUTION_CHROME``) or ``playwright install chromium``."""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip(
    "playwright", reason="page mode's tests need the page extra: uv sync --extra page"
)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from evalution.adapters.base import (
    Capabilities,
    Isolation,
    Person,
    Session,
    Target,
    Turn,
)
from evalution.core.evidence import Failure
from evalution.isolation.launch import free_port
from evalution.runs.page.map import PageMap
from evalution.runs.page.target import PageTarget, system_chrome


def _a_browser() -> bool:
    if system_chrome() is not None:
        return True
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).is_file()
    except Exception:
        return False


if not _a_browser():
    pytest.skip(
        "no browser for page mode: install Google Chrome, set EVALUTION_CHROME, or run "
        "`playwright install chromium`",
        allow_module_level=True,
    )

SECRET_ENV = "EVALUTION_PAGE_TEST_SECRET"
EMAIL = "eval+page@chat.local"
MAP = {
    "url": "{copy_page}/chat/",
    "ready": {"role": "textbox", "name": "Message"},
    "sign_in": {
        "headers": {
            "X-Forwarded-Email": "{person.email}",
            "X-Proxy-Secret": f"{{secret:{SECRET_ENV}}}",
        }
    },
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {"in": {"role": "log", "name": "Messages"}},
    "busy": [{"role": "button", "name": "Stop"}],
    "catch_up_seconds": 1.5,
}
#: Headers the page target adds, which only the copy's origin may see.
ADDED = ("x-forwarded-email", "x-proxy-secret", "x-evalution-run", "x-evalution-try")

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Chat</title></head><body><main>
<div role="log" aria-label="Messages" id="log"></div>
<form id="f"><input aria-label="Message" id="box">
<button type="submit" id="send">LABEL</button></form>
</main><script>
const session = decodeURIComponent(location.pathname.split("/").pop());
const drop = DROP, label = "LABEL";
fetch("OTHER/ping", {mode: "no-cors"}).catch(() => {});
function html(text) {
  const safe = text.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  return safe.replace(/\\*\\*(.+?)\\*\\*/g, "<b>$1</b>");
}
async function render() {
  const data = await (await fetch("/api/messages/" + encodeURIComponent(session))).json();
  const log = document.getElementById("log");
  log.innerHTML = data.messages.map(m =>
    "<p>" + html(m.role === "assistant" && drop ? "Couldn't load this reply" : m.text) + "</p>"
  ).join("");
  document.getElementById("send").textContent = data.busy ? "Stop" : label;
}
setInterval(render, 100); render();
document.getElementById("f").addEventListener("submit", async (event) => {
  event.preventDefault();
  const box = document.getElementById("box"), message = box.value;
  box.value = "";
  document.getElementById("send").textContent = "Stop";
  await fetch("/api/send", {method: "POST", headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({session, message})});
});
</script></body></html>"""


class Served:
    """A FastAPI app on a free port of this computer, in a thread of its own."""

    def __init__(self, app: FastAPI) -> None:
        self.port = free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Served":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            assert time.time() < deadline, "the test server didn't start"
            time.sleep(0.02)
        return self

    def __exit__(self, *_: object) -> None:
        self.server.should_exit = True
        self.thread.join(5)


class ChatApp:
    """The made-up app: a chat page, an API that answers after a moment, and switches that break
    the page."""

    def __init__(self, other: str) -> None:
        self.drop = False
        self.rename = False
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.busy: set[str] = set()
        #: ``(path, headers)`` of every request a browser made.
        self.seen: list[tuple[str, dict[str, str]]] = []
        self.app = FastAPI()

        @self.app.middleware("http")
        async def keep(request: Request, call_next):
            if "mozilla" in request.headers.get("user-agent", "").lower():
                self.seen.append((request.url.path, dict(request.headers)))
            return await call_next(request)

        @self.app.get("/chat/{session:path}")
        async def page(session: str) -> HTMLResponse:
            text = PAGE.replace("LABEL", "Post" if self.rename else "Send")
            return HTMLResponse(text.replace("DROP", json.dumps(self.drop)).replace("OTHER", other))

        @self.app.get("/api/messages/{session:path}")
        async def messages(session: str) -> dict[str, Any]:
            return {"messages": self.messages.get(session, []), "busy": session in self.busy}

        @self.app.post("/api/send")
        async def send(request: Request) -> dict[str, bool]:
            body = await request.json()
            session, message = body["session"], body["message"]
            said = self.messages.setdefault(session, [])
            said.append({"role": "user", "text": message, "at": time.time()})
            self.busy.add(session)

            async def answer() -> None:
                await asyncio.sleep(0.3)
                said.append(
                    {"role": "assistant", "text": f"Echo: **{message}**", "at": time.time()}
                )
                self.busy.discard(session)

            asyncio.get_running_loop().create_task(answer())
            return {"ok": True}


class Inner(Target):
    """An API adapter for the made-up app that sends, and can't watch."""

    capabilities = Capabilities(Isolation.SESSION, "A new session id.", cancel=True)

    def __init__(self, url: str) -> None:
        self.name = "chat"
        self.url = url
        self.count = 0
        self.sent: list[str] = []
        self.watched: list[float] = []
        self.closed: list[str] = []
        self.cancelled: list[str] = []
        self.client = httpx.AsyncClient()

    async def open(self, run: str) -> Session:
        self.count += 1
        # A space and a slash, so the page's URL must quote it.
        name = f"evalution-{run}-t{self.count} a/b"
        return Session(name, "session.id", name, {"run": run, "try": self.count, "email": EMAIL})

    async def _reply(self, session: Session, since: float, deadline: float) -> Turn:
        while time.time() < deadline:
            got = (await self.client.get(f"{self.url}/api/messages/{session.id}")).json()
            mine = [m for m in got["messages"] if m["role"] == "assistant" and m["at"] > since]
            if mine and not got["busy"]:
                return Turn(mine[-1]["text"], since, time.time())
            await asyncio.sleep(0.05)
        return Turn("", since, time.time(), error="no reply in time", failure=Failure.TIMED_OUT)

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
        self.sent.append(message)
        started = time.time()
        body = {"session": session.id, "message": message}
        await self.client.post(f"{self.url}/api/send", json=body)
        return await self._reply(session, started, deadline)

    async def close(self, session: Session) -> None:
        self.closed.append(session.id)

    async def cancel(self, session: Session) -> None:
        self.cancelled.append(session.id)

    async def dispose(self) -> None:
        await self.client.aclose()


class Watching(Inner):
    """The same, and it can wait on a turn the page sent."""

    capabilities = Capabilities(Isolation.SESSION, "A new session id.", cancel=True, watch=True)

    async def watch(self, session: Session, since: float, *, deadline: float) -> Turn:
        self.watched.append(since)
        return await self._reply(session, since, deadline)


@pytest.fixture
def other():
    """Another origin the page reaches, which must get none of the added headers."""
    app = FastAPI()
    app.state.seen = []

    @app.get("/ping")
    async def ping(request: Request) -> dict[str, bool]:
        app.state.seen.append(dict(request.headers))
        return {"ok": True}

    with Served(app) as served:
        served.seen = app.state.seen  # type: ignore[attr-defined]
        yield served


@pytest.fixture
def chat(other):
    made = ChatApp(other.url)
    with Served(made.app) as served:
        made.url = served.url  # type: ignore[attr-defined]
        yield made


@pytest.fixture(autouse=True)
def secret(monkeypatch) -> None:
    monkeypatch.setenv(SECRET_ENV, "s3cret")


def _page_target(chat: ChatApp, inner: Inner) -> PageTarget:
    copy = chat.url  # type: ignore[attr-defined]
    return PageTarget(inner, PageMap.model_validate(MAP), copy, element_seconds=1.5)


def _page(target: PageTarget, session: Session) -> Any:
    return target._open[session.extra["page"]["context"]][1]


async def test_the_page_sends_and_the_inner_adapter_watches(chat) -> None:
    inner = Watching(chat.url)
    target = _page_target(chat, inner)
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert turn.error is None, turn.error
        assert turn.reply == "Echo: **hello**", "the reply is the inner adapter's, as sent"
        assert turn.shown == "echo: hello", "what the page showed, normalised"
        assert inner.sent == [] and len(inner.watched) == 1
        sent = [p for p, _ in chat.seen if p == "/api/send"]
        assert sent == ["/api/send"], "the browser sent the message"
        assert json.loads(json.dumps(session.as_dict()))["extra"]["page"] == session.extra["page"]
    finally:
        await target.dispose()


async def test_without_watch_the_inner_adapter_sends_and_the_page_watches(chat) -> None:
    inner = Inner(chat.url)
    target = _page_target(chat, inner)
    try:
        session = await target.open("r")
        turn = await target.send(session, "hi *there*", deadline=time.time() + 20)
        assert (turn.error, turn.reply, turn.shown) == (
            None,
            "Echo: **hi *there***",
            "echo: hi there",
        )
        assert inner.sent == ["hi *there*"] and inner.watched == []
        assert not [p for p, _ in chat.seen if p == "/api/send"], "the page only watched"
    finally:
        await target.dispose()


async def test_a_reply_the_page_drops_is_shown_none_and_what_showed_is_kept(chat) -> None:
    chat.drop = True
    target = _page_target(chat, Watching(chat.url))
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert turn.error is None and turn.reply == "Echo: **hello**", "the page never changes it"
        assert turn.shown is None
        assert "Couldn't load this reply" in session.extra["page"]["last_shown"]
    finally:
        await target.dispose()


async def test_a_renamed_send_is_the_page_maps_failure(chat) -> None:
    chat.rename = True
    target = _page_target(chat, Watching(chat.url))
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert turn.failure is Failure.PAGE_MAP and "button 'Send'" in (turn.error or "")
        assert not [p for p, _ in chat.seen if p == "/api/send"]
    finally:
        await target.dispose()


async def test_headers_reach_the_copy_and_never_another_origin(chat, other) -> None:
    target = _page_target(chat, Watching(chat.url))
    try:
        session = await target.open("r")
        await target.send(session, "hello", deadline=time.time() + 20)
        deadline = time.time() + 5
        while not other.seen:
            assert time.time() < deadline, "the page never reached the other origin"
            await asyncio.sleep(0.05)
    finally:
        await target.dispose()
    assert chat.seen
    for path, headers in chat.seen:
        assert headers.get("x-forwarded-email") == EMAIL, path
        assert headers.get("x-proxy-secret") == "s3cret", path
        assert headers.get("x-evalution-run") == "r" and headers.get("x-evalution-try") == "1"
    for headers in other.seen:
        assert not set(ADDED) & set(headers), headers


async def test_nothing_carries_from_one_try_to_the_next(chat) -> None:
    target = _page_target(chat, Inner(chat.url))
    target.persona(Person(language="Portuguese", timezone="Europe/Lisbon"))
    try:
        first = await target.open("r")
        page = _page(target, first)
        await page.evaluate("localStorage.setItem('kept', 'yes')")
        assert await page.evaluate("localStorage.getItem('kept')") == "yes"
        assert await page.evaluate("navigator.language") == "pt"
        zone = "Intl.DateTimeFormat().resolvedOptions().timeZone"
        assert await page.evaluate(zone) == "Europe/Lisbon"
        await target.close(first)
        second = await target.open("r")
        assert await _page(target, second).evaluate("localStorage.getItem('kept')") is None
        assert first.extra["page"]["context"] != second.extra["page"]["context"]
    finally:
        await target.dispose()


async def test_close_and_cancel_need_only_the_sessions_fields(chat) -> None:
    target = _page_target(chat, Inner(chat.url))
    try:
        session = await target.open("r")
    finally:
        await target.dispose()
    rebuilt = Session(**json.loads(json.dumps(session.as_dict())))
    inner = Inner(chat.url)
    later = _page_target(chat, inner)  # a later server process: no browser, no contexts
    await later.cancel(rebuilt)
    await later.close(rebuilt)
    assert inner.cancelled == inner.closed == [session.id]
    assert later._browser is None, "nothing was launched to close a session"
    await later.dispose()
