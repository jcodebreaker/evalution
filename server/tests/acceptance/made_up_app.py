"""Run as a process: a made-up assistant and a made-up model gateway, over real sockets.

The app works the way apps that aren't built for evalution do. It makes its own sessions, nests its
messages in a list, says "Queued!" and does the work in a linked child session, keeps its memory per
person rather than per session when told to (SHARED_MEMORY), and has real people's chats that
nothing should touch. It sends its spans by OTLP and copies the run and try headers onto them.

The gateway answers like an OpenAI-compatible server, and goes down when told.
"""

import asyncio
import json
import os
import re
import secrets
import sys
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

TRACES = os.environ["TRACES"]
SHARED_MEMORY = os.environ.get("SHARED_MEMORY") == "1"
WORK_SECONDS = float(os.environ.get("WORK_SECONDS", "4"))

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


@app.post("/api/sessions")
async def make_session() -> dict:
    sid = f"made-{secrets.token_hex(4)}"
    sessions[sid] = {"items": [], "work": {"status": "done"}, "memory": []}
    return {"session": {"id": sid}}


@app.post("/api/sessions/{sid}/messages")
async def message(sid: str, request: Request) -> dict:
    body = await request.json()
    session = sessions[sid]
    if session["work"]["status"] == "running":
        seen["on_running"] += 1
    session["items"].append({"data": {"role": "user", "content": body["message"]}})
    session["work"]["status"] = "running"
    marks = {}
    if run := request.headers.get("x-evalution-run"):
        marks = {"evalution.run": run, "evalution.try": request.headers.get("x-evalution-try", "")}
    _background.add(asyncio.create_task(_turn(sid, session, body["message"], marks)))
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
