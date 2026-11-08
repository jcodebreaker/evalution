"""The made-up app's page, read without a browser: what a page map would find on it, and that a
message sent from it is the same conversation the API sees. The made-up app runs as a process, as
in the other acceptance tests, sending its spans to a small collector here rather than to
evalution. What the page's code does with what it's told needs a browser, and isn't played here.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).parent
SWITCHES = ["rename_send", "never_clear_busy", "throw_on_reply", "error_after_text", "drop_reply"]


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Spans(BaseHTTPRequestHandler):
    """Takes the app's OTLP posts and keeps their spans."""

    kept: list[dict]

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        for resource in body["resourceSpans"]:
            for scope in resource["scopeSpans"]:
                for span in scope["spans"]:
                    attrs = {a["key"]: a["value"]["stringValue"] for a in span["attributes"]}
                    self.kept.append({"name": span["name"], **attrs})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_args) -> None:
        pass


class App:
    def __init__(self) -> None:
        handler = type("Spans", (_Spans,), {"kept": []})
        self.spans: list[dict] = handler.kept
        self.collector = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.collector.serve_forever, daemon=True).start()
        port = _port()
        self.url = f"http://127.0.0.1:{port}"
        traces = f"http://127.0.0.1:{self.collector.server_address[1]}/v1/traces"
        self.process = subprocess.Popen(
            [sys.executable, str(HERE / "made_up_app.py"), str(port)],
            env={**os.environ, "TRACES": traces, "WORK_SECONDS": "0.5"},
            stdout=subprocess.DEVNULL,
        )
        self.client = httpx.Client(base_url=self.url, timeout=30, trust_env=False)
        for _ in range(120):
            try:
                if self.client.get("/admin").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise AssertionError("the made-up app never came up")

    def person(self, slot: int = 0) -> httpx.Client:
        """A browser's worth of cookies, signed in as the slot's test account."""
        signed = httpx.Client(base_url=self.url, timeout=30, trust_env=False)
        found = signed.post(
            "/login",
            data={"email": f"person{slot}@made-up.test", "password": f"made-up-{slot}"},
        )
        assert found.status_code == 303, found.text
        return signed

    def switch(self, **flags: bool) -> dict:
        return self.client.post("/admin/page", json=flags).json()

    def done(self, sid: str) -> dict:
        stop = time.time() + 30
        while time.time() < stop:
            found = self.client.get(f"/api/sessions/{sid}").json()
            if found["work"]["status"] != "running":
                return found
            time.sleep(0.1)
        raise AssertionError(f"{sid} still running")

    def close(self) -> None:
        self.process.kill()
        self.process.wait()
        self.collector.shutdown()


@pytest.fixture
def app():
    made = App()
    try:
        yield made
    finally:
        made.close()


class _Elements(HTMLParser):
    """Each element's tag, attributes and the text directly inside it."""

    def __init__(self, markup: str) -> None:
        super().__init__()
        self.found: list[dict] = []
        self.open: list[dict] = []
        self.feed(markup)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        element = {"tag": tag, "text": "", **{k: v or "" for k, v in attrs}}
        self.found.append(element)
        if tag not in ("input", "meta", "br"):  # nothing inside these, and no end tag
            self.open.append(element)

    def handle_endtag(self, tag: str) -> None:
        if self.open and self.open[-1]["tag"] == tag:
            self.open.pop()

    def handle_data(self, data: str) -> None:
        if self.open:
            self.open[-1]["text"] += data


def _find(markup: str, tag: str, **want: str) -> list[dict]:
    want = {key.replace("_", "-"): value for key, value in want.items()}
    return [
        e
        for e in _Elements(markup).found
        if e["tag"] == tag and all(e.get(k) == v for k, v in want.items())
    ]


def _labelled(markup: str, name: str) -> dict:
    """The field a <label> names, as a browser gives it its accessible name."""
    (label,) = [e for e in _find(markup, "label") if e["text"].strip() == name]
    (field,) = [e for e in _Elements(markup).found if e.get("id") == label["for"]]
    return field


def _told(markup: str) -> dict:
    """What the page's code is told: the JSON in #page-data."""
    (data,) = _find(markup, "script", id="page-data")
    return json.loads(data["text"])


def _events(stream: str) -> list[tuple[str, dict]]:
    found = []
    for block in stream.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        found.append((lines["event"], json.loads(lines["data"])))
    return found


def _said(events: list[tuple[str, dict]]) -> list[str]:
    """The replies a stream carried, each its deltas put together, and checked against its end."""
    replies, words = [], []
    for name, data in events:
        if name == "start":
            words = []
        elif name == "delta":
            words.append(data["text"])
        elif name == "end":
            assert "".join(words).strip() == data["content"]
            replies.append(data["content"])
    return replies


def test_any_test_account_signs_in_and_gets_a_cookie_and_a_wrong_password_doesnt(app) -> None:
    for slot in range(3):
        person = app.person(slot)
        assert person.cookies.get("made_up_person"), f"slot {slot} has no cookie"
    browser = httpx.Client(base_url=app.url, trust_env=False)
    found = browser.post("/login", data={"email": "person0@made-up.test", "password": "nope"})
    assert found.status_code == 401
    (said,) = _find(found.text, "p", role="alert")
    assert "don't match a test account" in said["text"]
    assert not browser.cookies.get("made_up_person")
    # Signing in lands on /chat, or the chat asked for, and never anywhere else.
    back = browser.post(
        "/login",
        data={"email": "person1@made-up.test", "password": "made-up-1", "next": "/chat/made-1"},
    )
    assert back.headers["location"] == "/chat/made-1"
    away = browser.post(
        "/login",
        data={"email": "person1@made-up.test", "password": "made-up-1", "next": "https://x.test"},
    )
    assert away.headers["location"] == "/chat"


def test_the_chat_needs_signing_in(app) -> None:
    found = app.client.get("/chat")
    assert found.status_code == 303 and found.headers["location"] == "/login"
    found = app.client.get("/chat/a-real-persons-chat")
    assert found.status_code == 303
    assert found.headers["location"] == "/login?next=%2Fchat%2Fa-real-persons-chat"
    assert app.client.post("/page/sessions").status_code == 401
    said = app.client.post("/page/sessions/a-real-persons-chat/messages", json={"message": "Hi"})
    assert said.status_code == 401
    assert app.client.get("/admin").json()["real"] == [
        {"data": {"role": "user", "content": "Something of my own"}}
    ]
    assert app.person().get("/chat/no-such-chat").status_code == 404


def test_the_pages_have_the_roles_and_names_the_page_map_uses(app) -> None:
    login = app.client.get("/login").text
    assert _labelled(login, "Email")["type"] == "email"  # a textbox
    assert _labelled(login, "Password")["type"] == "password"
    assert [b["text"] for b in _find(login, "button", type="submit")] == ["Sign in"]

    chat = app.person().get("/chat").text
    assert [b["text"] for b in _find(chat, "button", type="button")] == ["New chat"]
    (log,) = _find(chat, "div", role="log", aria_label="Messages")
    assert log["aria-busy"] == "false"
    assert _find(chat, "textarea", aria_label="Message")
    assert [b["text"] for b in _find(chat, "button", type="submit")] == ["Send"]
    # The messages are drawn by the page's code: articles with their data-role, in the log.
    (script,) = [e["text"] for e in _find(chat, "script") if "id" not in e]
    assert 'document.createElement("article")' in script
    assert "item.dataset.role = role" in script
    assert 'button.textContent = on ? "Stop" : idle' in script
    assert 'log.setAttribute("aria-busy", on ? "true" : "false")' in script


def test_a_message_from_the_page_is_the_same_conversation_the_api_sees(app) -> None:
    person = app.person()
    sid = person.post("/page/sessions").json()["session"]["id"]
    headers = {"X-Evalution-Run": "run-1", "X-Evalution-Try": "try-1"}
    with person.stream(
        "POST",
        f"/page/sessions/{sid}/messages",
        json={"message": "What is 6 times 7?"},
        headers=headers,
    ) as streamed:
        assert streamed.headers["content-type"].startswith("text/event-stream")
        events = _events(streamed.read().decode())
    assert _said(events) == ["It's 42."]
    assert events[-1] == ("done", {"status": "done"})

    # The API reads the same chat, and the page's run and try reached the spans.
    found = app.done(sid)
    assert [i["data"] for i in found["items"]] == [
        {"role": "user", "content": "What is 6 times 7?"},
        {"role": "assistant", "content": "It's 42."},
    ]
    turn = [s for s in app.spans if s["name"] == "model" and s["session.id"] == sid]
    assert turn and turn[0]["evalution.run"] == "run-1" and turn[0]["evalution.try"] == "try-1"

    # And the page lists the chat and opens it with what was said.
    chat = person.get("/chat").text
    assert _find(chat, "a", href=f"/chat/{sid}")[0]["text"] == "What is 6 times 7?"
    assert _told(person.get(f"/chat/{sid}").text)["messages"] == [i["data"] for i in found["items"]]


def test_the_page_opens_a_chat_the_api_made_and_waits_out_its_background_work(app) -> None:
    sid = app.client.post("/api/sessions").json()["session"]["id"]
    app.client.post(f"/api/sessions/{sid}/messages", json={"message": "Remember that it's Rowan."})
    app.done(sid)
    person = app.person(2)
    told = _told(person.get(f"/chat/{sid}").text)
    assert told["session"] == sid
    assert [m["role"] for m in told["messages"]] == ["user", "assistant"]

    # "Queued!", then the file: the stream stays open until the work's done.
    said = person.post(f"/page/sessions/{sid}/messages", json={"message": "Make the report."})
    events = _events(said.text)
    assert _said(events) == ["Queued!", "It's ready: report.pdf"]
    assert events[-1] == ("done", {"status": "done"})
    items = app.client.get(f"/api/sessions/{sid}").json()["items"]
    assert [i["data"]["content"] for i in items][-3:] == [
        "Make the report.",
        "Queued!",
        "It's ready: report.pdf",
    ]
    assert app.client.get("/admin").json()["on_running"] == 0


@pytest.mark.parametrize("switch", SWITCHES)
def test_each_switch_changes_what_the_pages_code_is_told(app, switch) -> None:
    person = app.person()
    plain = person.get("/chat").text
    assert _told(plain)["switches"] == dict.fromkeys(SWITCHES, False)
    (script,) = [e["text"] for e in _find(plain, "script") if "id" not in e]
    assert f"switches.{switch}" in script, "the page's code acts on it"

    assert app.switch(**{switch: True})[switch] is True
    broken = person.get("/chat").text
    assert _told(broken)["switches"] == {name: name == switch for name in SWITCHES}
    send = [b["text"] for b in _find(broken, "button", type="submit")]
    assert send == (["Ask"] if switch == "rename_send" else ["Send"])

    app.switch(**{switch: False})
    assert _told(person.get("/chat").text)["switches"] == dict.fromkeys(SWITCHES, False)


def test_an_unknown_switch_is_refused(app) -> None:
    found = app.client.post("/admin/page", json={"rename_sned": True})
    assert found.status_code == 400 and "rename_sned" in found.text
