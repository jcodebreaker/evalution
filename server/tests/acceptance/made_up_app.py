"""Run as a process: a made-up assistant and a made-up model gateway, over real sockets.

The app works the way apps that aren't built for evalution do. It makes its own sessions, nests its
messages in a list, says "Queued!" and does the work in a linked child session, keeps its memory per
person rather than per session when told to (SHARED_MEMORY), and has real people's chats that
nothing should touch. It sends its spans by OTLP and copies the run and try headers onto them.

It also has a page, as a person would use it: a small chat page served by the same app, plain HTML
and JavaScript with no build step. Sign in at /login with one of the test accounts below (any of
them works; there is one per slot, as evalution plays at most three copies at once), and /chat
opens a new chat, /chat/{session} an existing one, the API's included. The page sends through
/page/sessions/{session}/messages, which starts the same turn the API's messages route does, and
streams the replies back as server-sent events. So a chat started on the page reads and waits
through the API like any other, and the run and try headers on the page's requests reach the spans
the same way. The page is built to be found by role and name, as a page map finds it: textboxes
"Email" and "Password", button "Sign in"; button "New chat"; a log "Messages" of articles with
data-role "user" or "assistant"; a textbox "Message"; a button "Send" that says "Stop", and the log
aria-busy, while a reply streams.

Switches break the page when told (POST /admin/page with any of them, all off at first):
rename_send (Send says "Ask"), never_clear_busy (Stop and aria-busy stay after the reply),
throw_on_reply (a TypeError drawing a reply, "Couldn't load this reply" in its place),
error_after_text (the text streams, then an error replaces it) and drop_reply (the reply never
shows, though the API has it). The page's code is told them in its #page-data, and does the rest.

The gateway answers like an OpenAI-compatible server, and goes down when told.

To look at the page by hand, run it with TRACES pointing at something that takes OTLP posts (a
running ``evalution serve``'s /v1/traces): ``TRACES=... python made_up_app.py 8000``.
"""

import asyncio
import json
import os
import re
import secrets
import sys
import time
from collections.abc import AsyncIterator
from html import escape
from string import Template
from urllib.parse import parse_qs, urlencode

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

TRACES = os.environ["TRACES"]
SHARED_MEMORY = os.environ.get("SHARED_MEMORY") == "1"
WORK_SECONDS = float(os.environ.get("WORK_SECONDS", "4"))

#: Test people for the page, one per slot. Made up, like everything here.
ACCOUNTS = {f"person{slot}@made-up.test": f"made-up-{slot}" for slot in range(3)}
COOKIE = "made_up_person"

app = FastAPI()
sessions: dict[str, dict] = {
    "a-real-persons-chat": {
        "items": [{"data": {"role": "user", "content": "Something of my own"}}],
        "work": {"status": "done"},
        "memory": [],
    }
}
everyone: list[str] = []
seen = {"deleted": [], "cancelled": [], "on_running": 0, "gateway_calls": 0}
gateway_down = {"down": False}
#: The app's setup, as a change someone made to it.
setup = {"model": "made-up-model", "answer": "It's 42."}
#: Who's signed in on the page, by their cookie.
signed_in: dict[str, str] = {}
#: Ways to break the page, as a change someone made to it.
page_switches = dict.fromkeys(
    ("rename_send", "never_clear_busy", "throw_on_reply", "error_after_text", "drop_reply"), False
)
_background: set[asyncio.Task] = set()


def _attr(key: str, value: object) -> dict:
    return {"key": key, "value": {"stringValue": str(value)}}


def _span(trace: str, span: str, parent: str, name: str, start: float, end: float, attrs) -> dict:
    return {
        "traceId": trace,
        "spanId": span,
        "parentSpanId": parent,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(int(start * 1e9)),
        "endTimeUnixNano": str(int(end * 1e9)),
        "status": {"code": 1},
        "attributes": attrs,
    }


class Trace:
    """One trace's spans, tagged the way this app tags them."""

    def __init__(self, session: str, marks: dict[str, str], parent_session: str | None = None):
        self.trace, self.root = secrets.token_hex(16), secrets.token_hex(8)
        self.session, self.spans, self.started = session, [], time.time()
        self.tags = [_attr("session.id", session)]
        if parent_session:
            self.tags.append(_attr("session.parent_id", parent_session))
        for key, value in marks.items():
            self.tags.append(_attr(key, value))

    def model(self, said: str, heard: str) -> None:
        now = time.time()
        attrs = [
            _attr("openinference.span.kind", "LLM"),
            _attr("llm.model_name", setup["model"]),
            _attr("llm.input_messages.0.message.role", "user"),
            _attr("llm.input_messages.0.message.content", heard),
            _attr("llm.output_messages.0.message.role", "assistant"),
            _attr("llm.output_messages.0.message.content", said),
            *self.tags,
        ]
        self.spans.append(
            _span(self.trace, secrets.token_hex(8), self.root, "model", now, now + 0.01, attrs)
        )

    def tool(self, name: str, out: dict) -> None:
        now = time.time()
        attrs = [
            _attr("openinference.span.kind", "TOOL"),
            _attr("tool.name", name),
            _attr("input.value", "{}"),
            _attr("output.value", json.dumps(out)),
            *self.tags,
        ]
        self.spans.append(
            _span(self.trace, secrets.token_hex(8), self.root, name, now, now + 0.01, attrs)
        )

    async def send(self) -> None:
        kind = [_attr("openinference.span.kind", "AGENT"), *self.tags]
        spans = [
            _span(self.trace, self.root, "", "turn", self.started, time.time() + 0.02, kind),
            *self.spans,
        ]
        body = {
            "resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": spans}]}]
        }
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.post(TRACES, json=body)


def _say(session: dict, text: str) -> None:
    session["items"].append({"data": {"role": "assistant", "content": text}})


async def _delegated(sid: str, session: dict, marks: dict[str, str], cancel: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(cancel.wait(), WORK_SECONDS)
        session["work"]["status"] = "cancelled"
        return
    except TimeoutError:
        pass
    # Delegated work doesn't carry the run and try: only its link to the session that asked.
    child = Trace(f"{sid}-child", {}, parent_session=sid)
    child.tool("write_file", {"pages": 2, "written": True})
    child.model("Wrote it.", "Write the file")
    await child.send()
    last = Trace(sid, marks)
    last.model("It's ready: report.pdf", "Is it done?")
    await last.send()
    _say(session, "It's ready: report.pdf")
    session["work"]["status"] = "done"


async def _turn(sid: str, session: dict, message: str, marks: dict[str, str]) -> None:
    await asyncio.sleep(0.05)
    trace = Trace(sid, marks)
    trace.tool("recall", {"found": 0})
    remembered = re.search(r"remember (?:that )?(.+?)[.]?$", message, re.I)
    if remembered:
        session["memory"].append(remembered.group(1))
        if SHARED_MEMORY:
            everyone.append(remembered.group(1))
    known = everyone if SHARED_MEMORY else session["memory"]
    if "6 times 7" in message:
        said = setup["answer"]
    elif "report" in message.lower():
        said = "Queued!"
        trace.model(said, message)
        await trace.send()
        _say(session, said)
        session["cancel"] = asyncio.Event()
        _background.add(asyncio.create_task(_delegated(sid, session, marks, session["cancel"])))
        return
    elif "take your time" in message.lower():
        await asyncio.sleep(25)
        said = "Done, slowly."
    elif (
        "what did i tell you" in message.lower() or "did anyone give you a word" in message.lower()
    ):
        said = "; ".join(known) if known else "No."
    else:
        said = f"Noted, turn {len(session['items'])}."
    trace.model(said, message)
    await trace.send()
    _say(session, said)
    session["work"]["status"] = "done"


def _new_session(owner: str | None = None) -> str:
    sid = f"made-{secrets.token_hex(4)}"
    sessions[sid] = {"items": [], "work": {"status": "done"}, "memory": []}
    if owner:
        sessions[sid]["owner"] = owner
    return sid


def _accept(sid: str, message: str, request: Request) -> None:
    """A person's message, from the API or the page: kept, and its turn started."""
    session = sessions[sid]
    if session["work"]["status"] == "running":
        seen["on_running"] += 1
    session["items"].append({"data": {"role": "user", "content": message}})
    session["work"]["status"] = "running"
    marks = {}
    if run := request.headers.get("x-evalution-run"):
        marks = {"evalution.run": run, "evalution.try": request.headers.get("x-evalution-try", "")}
    _background.add(asyncio.create_task(_turn(sid, session, message, marks)))


@app.post("/api/sessions")
async def make_session() -> dict:
    return {"session": {"id": _new_session()}}


@app.post("/api/sessions/{sid}/messages")
async def message(sid: str, request: Request) -> dict:
    body = await request.json()
    _accept(sid, body["message"], request)
    return {"accepted": True}


@app.get("/api/sessions/{sid}")
async def read(sid: str) -> dict:
    found = sessions[sid]
    return {"items": found["items"], "work": found["work"]}


@app.post("/api/sessions/{sid}/cancel")
async def cancel(sid: str) -> dict:
    seen["cancelled"].append(sid)
    if event := sessions[sid].get("cancel"):
        event.set()
    sessions[sid]["work"]["status"] = "cancelled"
    return {"ok": True}


@app.delete("/api/sessions/{sid}")
async def delete(sid: str) -> dict:
    seen["deleted"].append(sid)
    sessions.pop(sid, None)
    return {"ok": True}


@app.get("/admin")
async def admin() -> dict:
    return {**seen, "real": sessions["a-real-persons-chat"]["items"]}


@app.post("/admin/setup")
async def set_setup(request: Request) -> dict:
    setup.update(await request.json())
    return setup


@app.post("/admin/gateway")
async def set_gateway(request: Request) -> dict:
    gateway_down["down"] = (await request.json())["down"]
    return gateway_down


@app.post("/admin/page")
async def set_page(request: Request) -> JSONResponse:
    asked = await request.json()
    if unknown := sorted(set(asked) - set(page_switches)):
        return JSONResponse({"error": f"no such switch: {', '.join(unknown)}"}, status_code=400)
    page_switches.update({name: bool(on) for name, on in asked.items()})
    return JSONResponse(page_switches)


# The page


def _person(request: Request) -> str | None:
    return signed_in.get(request.cookies.get(COOKIE, ""))


def _after_sign_in(asked: str | None) -> str:
    """Where signing in leads: back to the chat asked for, and nowhere else."""
    return asked if asked and re.fullmatch(r"/chat(/[\w-]+)?", asked) else "/chat"


@app.get("/login")
async def login(request: Request) -> HTMLResponse:
    return HTMLResponse(_login_page(_after_sign_in(request.query_params.get("next")), ""))


@app.post("/login")
async def sign_in(request: Request) -> Response:
    # A plain form post, read by hand: the app takes no form-parsing package for one form.
    form = {key: values[0] for key, values in parse_qs((await request.body()).decode()).items()}
    email, then = form.get("email", ""), _after_sign_in(form.get("next"))
    if email not in ACCOUNTS or ACCOUNTS[email] != form.get("password"):
        wrong = "That email and password don't match a test account."
        return HTMLResponse(_login_page(then, wrong), status_code=401)
    token = secrets.token_hex(16)
    signed_in[token] = email
    response = RedirectResponse(then, status_code=303)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
    return response


@app.get("/chat")
async def chat(request: Request) -> Response:
    return _chat(request, None)


@app.get("/chat/{sid}")
async def chat_session(sid: str, request: Request) -> Response:
    return _chat(request, sid)


def _chat(request: Request, sid: str | None) -> Response:
    person = _person(request)
    if person is None:
        back = "" if sid is None else "?" + urlencode({"next": f"/chat/{sid}"})
        return RedirectResponse(f"/login{back}", status_code=303)
    if sid is not None and sid not in sessions:
        return HTMLResponse("<!doctype html><title>Not found</title>No such chat.", 404)
    return HTMLResponse(_chat_page(person, sid))


@app.post("/page/sessions")
async def page_session(request: Request) -> JSONResponse:
    person = _person(request)
    if person is None:
        return JSONResponse({"error": "sign in first"}, status_code=401)
    return JSONResponse({"session": {"id": _new_session(owner=person)}})


@app.post("/page/sessions/{sid}/messages")
async def page_message(sid: str, request: Request) -> Response:
    """The page's Send: the same turn the API starts, its replies streamed back as they come."""
    if _person(request) is None:
        return JSONResponse({"error": "sign in first"}, status_code=401)
    if sid not in sessions:
        return JSONResponse({"error": "no such chat"}, status_code=404)
    _accept(sid, (await request.json())["message"], request)
    session = sessions[sid]
    return StreamingResponse(
        _stream(session, len(session["items"])),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def _stream(session: dict, start: int) -> AsyncIterator[str]:
    """Each reply the turn adds after ``start``, a word at a time, until its work is over.

    A reply is ``start``, its words as ``delta``, then ``end`` with the whole text; ``done`` ends
    the stream. Work done in the background ("Queued!", then the file) keeps it open till it's done.
    """
    sent = start
    while True:
        items = session["items"]
        while sent < len(items):
            said = items[sent]["data"]
            sent += 1
            if said["role"] != "assistant":
                continue
            yield _event("start", {})
            for word in re.findall(r"\S+\s*", said["content"]):
                await asyncio.sleep(0.02)
                yield _event("delta", {"text": word})
            yield _event("end", {"content": said["content"]})
        if session["work"]["status"] != "running":
            break
        await asyncio.sleep(0.05)
    yield _event("done", {"status": session["work"]["status"]})


def _login_page(then: str, wrong: str) -> str:
    return LOGIN.substitute(style=STYLE, next=escape(then), wrong=escape(wrong))


def _chat_page(person: str, sid: str | None) -> str:
    mine = [each for each, found in sessions.items() if found.get("owner") == person]
    if sid is not None and sid not in mine:
        mine.append(sid)
    links = []
    for each in reversed(mine):
        asked = [
            i["data"]["content"] for i in sessions[each]["items"] if i["data"]["role"] == "user"
        ]
        title = escape(asked[0][:40] if asked else "Untitled")
        current = ' aria-current="page"' if each == sid else ""
        links.append(f'    <li><a href="/chat/{escape(each)}"{current}>{title}</a></li>')
    data = {
        "session": sid,
        "messages": [item["data"] for item in sessions[sid]["items"]] if sid else [],
        "switches": page_switches,
    }
    return CHAT.substitute(
        style=STYLE,
        person=escape(person),
        conversations="\n".join(links),
        send="Ask" if page_switches["rename_send"] else "Send",
        # JSON inside a <script>: no "<" in it can close the element.
        data=json.dumps(data).replace("<", "\\u003c"),
        script=SCRIPT,
    )


STYLE = """
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.45 system-ui, sans-serif; color: #1d1d1f; background: #fafaf7; }
main.sign-in { max-width: 22rem; margin: 4rem auto; padding: 0 1rem; }
main.sign-in form { display: grid; gap: .5rem; }
body.chat { display: grid; grid-template-columns: 14rem 1fr; height: 100vh; }
nav { border-right: 1px solid #ddd; padding: 1rem; overflow-y: auto; }
nav ul { list-style: none; padding: 0; }
nav a[aria-current] { font-weight: 600; }
body.chat main { display: grid; grid-template-rows: 1fr auto; min-height: 0; }
[role=log] { overflow-y: auto; padding: 1rem; display: grid; gap: .5rem; align-content: start; }
article { white-space: pre-wrap; padding: .5rem .75rem; border-radius: .5rem; max-width: 40rem; }
article[data-role=user] { background: #e8eefc; justify-self: end; }
article[data-role=assistant] { background: #fff; border: 1px solid #e3e3e3; }
article[data-error] { color: #a1260d; }
form.composer { display: flex; gap: .5rem; padding: 1rem; border-top: 1px solid #ddd; }
textarea { flex: 1; font: inherit; }
input, button { font: inherit; padding: .4rem .6rem; }
.who { color: #666; font-size: 13px; }
"""

LOGIN = Template("""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Sign in - Made-up assistant</title><style>$style</style></head>
<body>
<main class="sign-in">
<h1>Sign in</h1>
<form method="post" action="/login">
  <input type="hidden" name="next" value="$next">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" required>
  <p role="alert">$wrong</p>
  <button type="submit">Sign in</button>
</form>
</main>
</body>
</html>
""")

CHAT = Template("""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Made-up assistant</title><style>$style</style></head>
<body class="chat">
<nav aria-label="Conversations">
  <button type="button" id="new-chat">New chat</button>
  <ul id="conversations">
$conversations
  </ul>
  <p class="who">$person</p>
</nav>
<main>
  <div id="messages" role="log" aria-label="Messages" aria-live="polite" aria-busy="false"></div>
  <form class="composer" id="composer">
    <textarea id="message" aria-label="Message" rows="2"></textarea>
    <button type="submit" id="send">$send</button>
  </form>
</main>
<script type="application/json" id="page-data">$data</script>
<script>$script</script>
</body>
</html>
""")

# The page's code. What it's told is in #page-data: the chat, its messages so far, and the switches
# that break it.
SCRIPT = """
"use strict";
(function () {
  const page = JSON.parse(document.getElementById("page-data").textContent);
  const switches = page.switches;
  const log = document.getElementById("messages");
  const box = document.getElementById("message");
  const button = document.getElementById("send");
  const idle = switches.rename_send ? "Ask" : "Send";
  const failed = "Something went wrong. Try again.";
  let session = page.session;
  let streaming = null;

  function busy(on) {
    if (!on && switches.never_clear_busy) return;
    log.setAttribute("aria-busy", on ? "true" : "false");
    button.textContent = on ? "Stop" : idle;
  }

  function article(role) {
    const item = document.createElement("article");
    item.dataset.role = role;
    log.appendChild(item);
    log.scrollTop = log.scrollHeight;
    return item;
  }

  // How a reply is drawn. With throw_on_reply it reads a reply as a shape it isn't: a TypeError.
  function draw(item, reply) {
    if (switches.throw_on_reply) {
      item.textContent = reply.parts.map(function (part) { return part.text; }).join("");
    } else {
      item.textContent = reply.content;
    }
  }

  function show(item, reply) {
    if (item.dataset.broken) return;
    try {
      draw(item, reply);
    } catch (error) {
      item.dataset.broken = "true";
      item.textContent = "Couldn't load this reply";
      setTimeout(function () { throw error; });  // uncaught, so it reaches the console
    }
  }

  function post(url, body, signal) {
    return fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body || {}),
      signal: signal,
    });
  }

  // Server-sent events over a POST's body: "event: name" and "data: {json}", a blank line apart.
  async function events(response, on) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const read = await reader.read();
      if (read.done) return;
      buffer += decoder.decode(read.value, {stream: true});
      let cut;
      while ((cut = buffer.indexOf("\\n\\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        let name = "message";
        let data = "";
        block.split("\\n").forEach(function (line) {
          if (line.startsWith("event: ")) name = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        });
        on(name, data ? JSON.parse(data) : {});
      }
    }
  }

  // A chat for the first message on /chat: made, put in the address and the list.
  async function opened(first) {
    if (session) return session;
    const made = await post("/page/sessions");
    session = (await made.json()).session.id;
    history.replaceState(null, "", "/chat/" + session);
    const link = document.createElement("a");
    link.href = "/chat/" + session;
    link.textContent = first.slice(0, 40);
    link.setAttribute("aria-current", "page");
    const item = document.createElement("li");
    item.appendChild(link);
    document.getElementById("conversations").prepend(item);
    return session;
  }

  async function send(text) {
    article("user").textContent = text;
    busy(true);
    streaming = new AbortController();
    let item = null;
    let said = "";
    try {
      const id = await opened(text);
      const url = "/page/sessions/" + id + "/messages";
      const response = await post(url, {message: text}, streaming.signal);
      if (!response.ok) throw new Error("The app answered " + response.status);
      await events(response, function (name, data) {
        if (name === "start") {
          said = "";
          item = switches.drop_reply ? null : article("assistant");
        } else if (name === "delta" && item) {
          said += data.text;
          show(item, {content: said});
        } else if (name === "end" && item) {
          show(item, {content: data.content});
          if (switches.error_after_text) {
            item.dataset.error = "true";
            item.textContent = failed;
          }
        }
      });
    } catch (error) {
      if (error.name !== "AbortError") {
        const shown = article("assistant");
        shown.dataset.error = "true";
        shown.textContent = failed;
        console.error(error);
      }
    } finally {
      streaming = null;
      busy(false);
    }
  }

  page.messages.forEach(function (said) {
    if (said.role === "user") article("user").textContent = said.content;
    else if (!switches.drop_reply) show(article("assistant"), said);
  });

  document.getElementById("composer").addEventListener("submit", function (event) {
    event.preventDefault();
    if (streaming) {  // the button says Stop
      streaming.abort();
      if (session) post("/api/sessions/" + session + "/cancel");
      return;
    }
    const text = box.value.trim();
    if (!text) return;
    box.value = "";
    send(text);
  });

  box.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      document.getElementById("composer").requestSubmit();
    }
  });

  document.getElementById("new-chat").addEventListener("click", async function () {
    const made = await post("/page/sessions");
    location.assign("/chat/" + (await made.json()).session.id);
  });
})();
"""


# The gateway


@app.get("/llm/v1/models")
async def models() -> dict:
    return {"data": [{"id": "made-up-grader"}]}


@app.post("/llm/v1/chat/completions")
async def complete(request: Request) -> JSONResponse:
    seen["gateway_calls"] += 1
    if gateway_down["down"]:
        return JSONResponse({"error": {"message": "gateway down"}}, status_code=503)
    body = json.dumps(await request.json())
    # Every line it's shown holds. What it grades is the code's to count, not this stub's.
    ids = sorted({int(n) for n in re.findall(r"(?:^|\\n)\s*(\d+)[.:)] ", body)}) or [1]
    lines = [
        {"id": n, "pass": True, "sure": True, "reason": "It holds.", "quotes": []} for n in ids
    ]
    said = json.dumps({"lines": lines, "score": 0.9, "causes": [], "well": [], "next": []})
    return JSONResponse(
        {
            "model": "made-up-grader",
            "choices": [{"message": {"role": "assistant", "content": said}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }
    )


uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
