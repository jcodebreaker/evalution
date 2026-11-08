"""Looking at a page and trying its map in a real browser, as the test writer's page tools and a
run's check before it plays do (ADR 0010, after v1), against a tiny chat page served here whose
message box saves a draft as it's typed in and whose Send posts the message: the page is looked at
with nothing clicked, an entry is found or not, the box takes a harmless text and is cleared, Send
and New chat are found and never clicked, and not one request but a read leaves the page.

Skipped without the page extra or a browser: ``uv sync --extra page``, and Google Chrome (or
``EVALUTION_CHROME``) or ``playwright install chromium``."""

import threading
import time
from pathlib import Path

import pytest

pytest.importorskip(
    "playwright", reason="page mode's tests need the page extra: uv sync --extra page"
)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from evalution.adapters.base import Capabilities, Isolation, Session, Target, Turn
from evalution.isolation.launch import free_port
from evalution.runs.page.learn import scaffold
from evalution.runs.page.map import PageMap
from evalution.runs.page.target import HARMLESS, PageTarget, system_chrome


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

MAP = {
    "url": "{copy_page}/chat/new",
    "ready": {"role": "textbox", "name": "Message"},
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
        "new_chat": {"click": {"role": "button", "name": "New chat"}},
    },
    "replies": {"in": {"role": "log", "name": "Messages"}},
    "busy": [{"role": "button", "name": "Stop"}],
}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Chat</title></head><body>
<nav><button type="button" id="new">New chat</button></nav>
<main><div role="log" aria-label="Messages"><p>Hello, how can I help?</p></div>
<form id="f"><textarea aria-label="Message" id="box"></textarea>
<button type="submit">LABEL</button></form></main>
<script>
const post = (path, body) => fetch(path, {method: "POST", body: JSON.stringify(body)});
const box = document.getElementById("box");
box.addEventListener("input", (e) => post("/api/draft", {text: e.target.value}));
document.getElementById("new").addEventListener("click", () => post("/api/sessions", {}));
document.getElementById("f").addEventListener("submit", (e) => {
  e.preventDefault();
  post("/api/send", {message: document.getElementById("box").value});
});
</script></body></html>"""


class ChatPage:
    """The page and what reached it: every request that wasn't a read."""

    def __init__(self) -> None:
        self.rename = False
        self.writes: list[str] = []
        self.app = FastAPI()

        @self.app.middleware("http")
        async def keep(request: Request, call_next):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                self.writes.append(f"{request.method} {request.url.path}")
            return await call_next(request)

        @self.app.get("/chat/{session}")
        async def page(session: str) -> HTMLResponse:
            return HTMLResponse(PAGE.replace("LABEL", "Ask" if self.rename else "Send"))

        @self.app.post("/api/{what}")
        async def wrote(what: str) -> dict[str, bool]:
            return {"ok": True}


@pytest.fixture
def chat():
    made = ChatPage()
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(made.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "the test server didn't start"
        time.sleep(0.02)
    made.url = f"http://127.0.0.1:{port}"  # type: ignore[attr-defined]
    yield made
    server.should_exit = True
    thread.join(5)


class Inner(Target):
    """An API adapter that only opens and closes sessions: nothing here sends a message."""

    capabilities = Capabilities(Isolation.SESSION, "A new session id.")

    def __init__(self) -> None:
        self.name = "chat"
        self.opened: list[str] = []
        self.closed: list[str] = []

    async def open(self, run: str) -> Session:
        self.opened.append(run)
        return Session("s1", "session.id", "s1", {"run": run})

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
        raise AssertionError("nothing here sends a message")

    async def close(self, session: Session) -> None:
        self.closed.append(session.id)


def _target(chat: ChatPage, page: PageMap | None = None) -> PageTarget:
    made = PageTarget(Inner(), page or PageMap.model_validate(MAP), chat.url, element_seconds=1.5)  # type: ignore[attr-defined]
    return made


async def test_looking_shows_roles_and_names_and_clicks_nothing(chat) -> None:
    target = _target(chat, scaffold("{copy_page}/chat/s1", None))
    session = await target.inner.open("look")
    try:
        found = await target.look(session)
    finally:
        await target.dispose_browser()
    assert found["at"] == "/chat/s1"
    assert 'textbox "Message"' in found["snapshot"] and 'button "Send"' in found["snapshot"]
    assert 'log "Messages"' in found["snapshot"]
    assert found["blocked"] == [] and chat.writes == []


async def test_trying_an_entry_never_sends_anything(chat) -> None:
    target = _target(chat)
    session = await target.inner.open("try")
    try:
        typed = await target.try_entry(session, "actions.type")
        send = await target.try_entry(session, "actions.send")
        new = await target.try_entry(session, "actions.new_chat")
        region = await target.try_entry(session, "replies.in")
        busy = await target.try_entry(session, "busy")
    finally:
        await target.dispose_browser()
    assert typed["found"] is True and typed["took_text"] is True
    # Typing made the page save a draft: refused before it left, and said.
    assert typed["blocked"] and all(b == "POST /api/draft" for b in typed["blocked"])
    assert send == {
        "entry": "actions.send",
        "found": True,
        "enabled": True,
        "clicked": False,
        "blocked": [],
        "changed": "(nothing changed)",
    }
    assert new["found"] is True and new["clicked"] is False
    assert region["found"] is True and "how can I help" in region["shows"]
    assert busy["found"] is True and busy["showing_now"] == []
    # Not one POST reached the app: no draft, no new chat, no message.
    assert chat.writes == []
    assert HARMLESS not in str(chat.writes)


async def test_an_entry_the_page_lacks_is_not_found_and_says_why(chat) -> None:
    chat.rename = True
    target = _target(chat)
    session = await target.inner.open("try")
    try:
        send = await target.try_entry(session, "actions.send")
    finally:
        await target.dispose_browser()
    assert send["found"] is False and send["problem"] == "the page has no button 'Send'"
    assert chat.writes == []


async def test_the_check_before_a_run_finds_everything_or_says_what_is_missing(chat) -> None:
    target = _target(chat)
    try:
        assert await target.check("page-map-check") == []
        chat.rename = True
        assert await target.check("page-map-check") == ["button 'Send'"]
    finally:
        await target.dispose_browser()
    inner = target.inner
    assert isinstance(inner, Inner) and inner.closed == ["s1", "s1"]
    assert chat.writes == []
