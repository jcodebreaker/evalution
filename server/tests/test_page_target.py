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
from fastapi.responses import HTMLResponse, JSONResponse

from evalution.adapters.base import (
    Capabilities,
    Isolation,
    Person,
    Session,
    Target,
    Turn,
)
from evalution.adapters.http import HttpTarget
from evalution.config import TargetConfig
from evalution.core.evidence import VIDEO, Failure
from evalution.isolation.launch import free_port
from evalution.runs.page import target as page_target
from evalution.runs.page.map import PageMap
from evalution.runs.page.target import NO_VIDEO, PageFiles, PageTarget, system_chrome
from evalution.storage.files import LocalFiles


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
const label = "LABEL";
fetch("OTHER/ping", {mode: "no-cors"}).catch(() => {});
if (NOISY) {
  console.error("token s3cret leaked");
  console.warn("slow");
  fetch("/api/missing?token=abc").catch(() => {});
  setTimeout(() => { throw new TypeError("bad shape"); });
}
function html(text) {
  const safe = text.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  return safe.replace(/\\*\\*(.+?)\\*\\*/g, "<b>$1</b>");
}
async function render() {
  const data = await (await fetch("/api/messages/" + encodeURIComponent(session))).json();
  const log = document.getElementById("log");
  let answers = 0;
  log.innerHTML = data.messages.map(m => {
    const mine = m.role === "assistant";
    const dropped = mine && data.drop_from !== null && answers++ >= data.drop_from;
    return "<p>" + html(dropped ? "Couldn't load this reply" : m.text) + "</p>";
  }).join("");
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
        #: The page drops the app's replies from this one on (0 is the first).
        self.drop_from: int | None = None
        self.rename = False
        #: The page writes an error, a warning and an uncaught error, and asks for what isn't there.
        self.noisy = False
        #: Seconds after a message the API still says what it said before it: idle, old reply.
        self.lag = 0.0
        #: What the API answers a message with instead of taking it; seconds it takes first.
        self.send_status: int | None = None
        self.send_seconds = 0.0
        self.frozen: dict[str, tuple[float, dict[str, Any]]] = {}
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
            text = text.replace("NOISY", json.dumps(self.noisy))
            return HTMLResponse(text.replace("OTHER", other))

        @self.app.get("/api/messages/{session:path}")
        async def messages(session: str) -> dict[str, Any]:
            said = self.messages.get(session, [])
            return {"messages": said, "busy": session in self.busy, "drop_from": self.drop_from}

        @self.app.get("/api/state/{session:path}")
        async def state(session: str) -> dict[str, Any]:
            until, before = self.frozen.get(session, (0.0, {}))
            if time.time() < until:
                return before
            return self._state(session)

        @self.app.post("/api/send")
        async def send(request: Request) -> dict[str, bool]:
            await asyncio.sleep(self.send_seconds)
            if self.send_status is not None:
                return JSONResponse({"ok": False}, status_code=self.send_status)
            body = await request.json()
            session, message = body["session"], body["message"]
            self.frozen[session] = (time.time() + self.lag, self._state(session))
            said = self.messages.setdefault(session, [])
            said.append({"role": "user", "text": message, "at": time.time()})
            self.busy.add(session)

            async def answer() -> None:
                await asyncio.sleep(0.3 + self.lag)
                said.append(
                    {"role": "assistant", "text": f"Echo: **{message}**", "at": time.time()}
                )
                self.busy.discard(session)

            asyncio.get_running_loop().create_task(answer())
            return {"ok": True}

    def _state(self, session: str) -> dict[str, Any]:
        replies = [m["text"] for m in self.messages.get(session, []) if m["role"] == "assistant"]
        status = "busy" if session in self.busy else "idle"
        return {"status": status, "reply": replies[-1] if replies else ""}


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
        assert turn.shown == "Echo: hello", "what the page showed, where the reply showed"
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
            "Echo: hi *there",
        )
        assert inner.sent == ["hi *there*"] and inner.watched == []
        assert not [p for p, _ in chat.seen if p == "/api/send"], "the page only watched"
    finally:
        await target.dispose()


async def test_a_reply_the_page_drops_is_shown_none_and_what_showed_is_kept(chat) -> None:
    chat.drop_from = 0
    target = _page_target(chat, Watching(chat.url))
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert turn.error is None and turn.reply == "Echo: **hello**", "the page never changes it"
        assert turn.shown is None
        assert "Couldn't load this reply" in session.extra["page"]["last_shown"]
    finally:
        await target.dispose()


async def test_the_same_reply_again_isnt_taken_for_the_one_the_page_dropped(chat) -> None:
    target = _page_target(chat, Watching(chat.url))
    try:
        session = await target.open("r")
        first = await target.send(session, "hello", deadline=time.time() + 20)
        assert first.shown == "Echo: hello"
        chat.drop_from = 1  # the page shows the first reply still, and drops the second
        again = await target.send(session, "hello", deadline=time.time() + 20)
        assert again.error is None and again.reply == "Echo: **hello**"
        assert again.shown is None, "the first turn's words aren't the second's"
        assert "Couldn't load this reply" in session.extra["page"]["last_shown"]
    finally:
        await target.dispose()


async def test_an_http_adapter_never_reads_the_reply_from_before_the_page_sent(chat) -> None:
    """The API says idle, with the reply before, for a moment after the page's message: the
    page target waits for the message to be taken, and the http adapter for the session to
    move, so each turn reads its own reply."""
    chat.lag = 0.3
    poll = {"url": f"{chat.url}/api/state/{{session}}", "until": "$.status == 'idle'"}
    http = {
        "send": {"url": f"{chat.url}/api/send"},
        "done": {"poll": {**poll, "reply": "$.reply", "interval": 0.05}},
    }
    inner = HttpTarget(TargetConfig("chat", "http", None, None, {"http": http}))
    assert inner.capabilities.watch
    plain = {key: value for key, value in MAP.items() if key != "sign_in"}
    target = PageTarget(inner, PageMap.model_validate(plain), chat.url, element_seconds=1.5)
    try:
        session = await target.open("r")
        for message in ("hello", "again"):
            turn = await target.send(session, message, deadline=time.time() + 20)
            assert (turn.error, turn.reply) == (None, f"Echo: **{message}**")
            assert turn.shown == f"Echo: {message}"
    finally:
        await target.dispose()


@pytest.mark.parametrize(
    ("status", "failure", "heard"),
    [
        (503, Failure.TRANSIENT, False),
        (502, Failure.TRANSIENT, True),
        (401, Failure.REFUSED, False),
    ],
)
async def test_an_error_answer_to_the_pages_message_ends_the_turn(
    chat, status, failure, heard
) -> None:
    chat.send_status = status
    inner = Watching(chat.url)
    target = _page_target(chat, inner)
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert (turn.failure, turn.heard) == (failure, heard) and str(status) in (turn.error or "")
        assert inner.watched == [], "nothing to wait on"
    finally:
        await target.dispose()


async def test_no_answer_to_the_pages_message_is_out_of_time_and_heard(chat) -> None:
    chat.send_seconds = 3.0
    inner = Watching(chat.url)
    target = _page_target(chat, inner)
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 1.5)
        assert (turn.failure, turn.heard) == (Failure.TIMED_OUT, True)
        assert inner.watched == []
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


async def test_a_try_records_what_the_page_did(chat, tmp_path) -> None:
    chat.noisy = True
    files = LocalFiles(tmp_path)
    target = _page_target(chat, Watching(chat.url))
    try:
        session = await target.open("r")
        target.begin(session, PageFiles(files, "run-1", "a-case", 1))
        t0 = time.time()
        await target.send(session, "hello", deadline=time.time() + 20)
        chat.drop_from = 1
        await target.send(session, "again", deadline=time.time() + 20)
        found = await target.record(session, t0)
        video = target._ffmpeg
    finally:
        await target.dispose()
    first, second = found.turns
    assert first.shown_at is not None and first.shown_at > 0 and first.instead.text == ""
    assert second.shown_at is None and "Couldn't load this reply" in second.instead.text
    assert (first.shot, second.shot) == ("turn-1.png", "turn-2.png")
    kept = ("turn-1.png", "turn-2.png", *((VIDEO,) if video else ()))
    assert found.files == kept
    assert files.list("run-1", "a-case", 1) == sorted(kept)
    with files.open("run-1", "a-case", 1, "turn-1.png") as shot:
        assert shot.read(8) == b"\x89PNG\r\n\x1a\n"
    said = [(c.level, c.text.text) for c in found.console]
    assert ("error", "token *** leaked") in said, "the sign-in secret is scrubbed"
    assert ("warning", "slow") in said
    assert ("error", "TypeError: bad shape") in said
    assert all(c.at < 0 for c in found.console), "written as the page opened, before the try"
    missing = [(r.method, r.path, r.status) for r in found.failed_requests]
    assert missing == [("GET", "/api/missing", 404)], "the copy's only, and never the query"
    assert not found.interrupted
    assert found.video_missing == (None if video else NO_VIDEO)
    again = await target.record(session, t0)
    assert again.turns == () and again.console == (), "each try's record is its own"


async def test_a_turn_cut_off_leaves_an_interrupted_record(chat) -> None:
    chat.send_seconds = 3.0
    target = _page_target(chat, Watching(chat.url))
    try:
        session = await target.open("r")
        target.begin(session, None)
        t0 = time.time()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(target.send(session, "hello", deadline=t0 + 20), 0.5)
        found = await target.record(session, t0)
    finally:
        await target.dispose()
    assert found.interrupted and len(found.turns) == 1 and found.turns[0].shot is None
    assert found.video_missing is None and found.files == (), "nowhere to keep files"


#: How a WebM file starts (EBML).
WEBM = b"\x1a\x45\xdf\xa3"


def _video_target(chat: ChatApp, video: str = "failed") -> PageTarget:
    copy = chat.url  # type: ignore[attr-defined]
    page = PageMap.model_validate({**MAP, "video": video})
    return PageTarget(Watching(chat.url), page, copy, element_seconds=1.5)


def _needs_ffmpeg(target: PageTarget) -> None:
    if target._ffmpeg is False:
        pytest.skip("video needs Playwright's ffmpeg: `playwright install ffmpeg`")


async def test_each_try_keeps_a_video_of_its_own(chat, tmp_path) -> None:
    files = LocalFiles(tmp_path)
    target = _video_target(chat)
    try:
        session = await target.open("r")
        _needs_ffmpeg(target)
        target.begin(session, PageFiles(files, "run-1", "a-case", 1))
        t0 = time.time()
        await target.send(session, "hello", deadline=time.time() + 20)
        first = await target.record(session, t0)
        # The next try in the same session plays on a new page, whose video is its own.
        target.begin(session, PageFiles(files, "run-1", "a-case", 2))
        turn = await target.send(session, "again", deadline=time.time() + 20)
        assert turn.error is None and turn.shown == "echo: again", "a new page on the chat"
        second = await target.record(session, time.time())
        recording = target._videos[session.extra["page"]["context"]]
    finally:
        await target.dispose()
    for n, found in ((1, first), (2, second)):
        assert found.files == ("turn-1.png", VIDEO), "each try counts its turns from one"
        assert found.video_missing is None and found.video_policy == "failed"
        with files.open("run-1", "a-case", n, VIDEO) as video:
            assert video.read(4) == WEBM
        assert files.path("run-1", "a-case", n, VIDEO).stat().st_size > 1000
    assert not recording.exists(), "the browser's own copies go with the context"


async def test_never_records_nothing(chat, tmp_path) -> None:
    files = LocalFiles(tmp_path)
    target = _video_target(chat, "never")
    try:
        session = await target.open("r")
        assert _page(target, session).video is None
        target.begin(session, PageFiles(files, "run-1", "a-case", 1))
        await target.send(session, "hello", deadline=time.time() + 20)
        found = await target.record(session, time.time())
    finally:
        await target.dispose()
    assert found.files == ("turn-1.png",) and found.video_missing is None
    assert found.video_policy is None and target._ffmpeg is None, "never tried to record"
    assert files.list("run-1", "a-case", 1) == ["turn-1.png"]


async def test_a_video_too_big_is_dropped_and_says_why(chat, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(page_target, "VIDEO_BYTES", 10)
    files = LocalFiles(tmp_path)
    target = _video_target(chat, "always")
    try:
        session = await target.open("r")
        _needs_ffmpeg(target)
        target.begin(session, PageFiles(files, "run-1", "a-case", 1))
        await target.send(session, "hello", deadline=time.time() + 20)
        found = await target.record(session, time.time())
    finally:
        await target.dispose()
    assert found.files == ("turn-1.png",) and found.video_policy is None
    assert found.video_missing is not None and "more than the 0 MB" in found.video_missing
    assert files.list("run-1", "a-case", 1) == ["turn-1.png"]


async def test_without_ffmpeg_a_try_plays_on_and_says_why_it_has_no_video(
    chat, tmp_path, monkeypatch
) -> None:
    if system_chrome() is None:
        pytest.skip("hiding Playwright's ffmpeg hides its Chromium too: needs the system Chrome")
    # Playwright looks for its ffmpeg (and its browsers) here: an empty folder has none.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "none"))
    files = LocalFiles(tmp_path)
    target = _video_target(chat)
    try:
        session = await target.open("r")
        target.begin(session, PageFiles(files, "run-1", "a-case", 1))
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        found = await target.record(session, time.time())
    finally:
        await target.dispose()
    assert turn.error is None and turn.shown == "echo: hello"
    assert target._ffmpeg is False
    assert found.files == ("turn-1.png",) and found.video_missing == NO_VIDEO
