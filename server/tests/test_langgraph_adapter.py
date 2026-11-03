"""The langgraph adapter against a stub LangGraph Server: threads, runs and join, cancel, thread
state, and the store, all in memory (offline)."""

import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
import pytest

from evalution.adapters import langgraph
from evalution.adapters.base import Session, TargetGone
from evalution.adapters.langgraph import LangGraphTarget
from evalution.config import ConfigError, TargetConfig
from evalution.core.evidence import Failure, TurnEnd
from evalution.runs.play import play_try
from evalution.suite.cases import parse_case

KEEP = 2  # the stub graph summarises past this many messages


class Stub:
    """A LangGraph Server with one graph, ``agent``: it echoes, remembers "remember <x>" in
    the store under ``["memories", <user>]``, waits on "slow" until cancelled, and summarises
    the thread once it holds more than :data:`KEEP` messages."""

    def __init__(self) -> None:
        self.threads: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.store: dict[tuple, dict] = {}
        self.bodies: list[dict] = []
        self.cancelled: list[str] = []
        self.keys: set[str | None] = set()

    def _user(self, body: dict) -> str:
        return (body.get("config") or {}).get("configurable", {}).get("user_id") or body.get(
            "context", {}
        ).get("user_id")

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.keys.add(request.headers.get("x-api-key"))
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        parts = path.strip("/").split("/")
        if (method, path) == ("POST", "/threads"):
            tid = str(uuid.uuid4())
            self.threads[tid] = {"metadata": body.get("metadata") or {}, "values": {}}
            return httpx.Response(200, json={"thread_id": tid, "status": "idle"})
        if (method, path) == ("POST", "/threads/search"):
            wanted = body.get("metadata") or {}
            rows = [
                {"thread_id": t, "metadata": row["metadata"]}
                for t, row in self.threads.items()
                if all(row["metadata"].get(k) == v for k, v in wanted.items())
            ]
            return httpx.Response(200, json=rows[: body.get("limit", 10)])
        if parts[0] == "threads" and parts[1] not in self.threads:
            return httpx.Response(404, json={"detail": "Thread not found"})
        if method == "DELETE" and parts[0] == "threads" and len(parts) == 2:
            del self.threads[parts[1]]
            return httpx.Response(204)
        if method == "GET" and parts[2:] == ["state"]:
            values = self.threads[parts[1]]["values"]
            return httpx.Response(200, json={"values": values, "next": [], "checkpoint": {}})
        if method == "POST" and parts[2:] == ["runs"]:
            return self._run(parts[1], body)
        if method == "GET" and parts[2:] == ["runs"]:
            rows = [
                {"run_id": rid, "status": "success" if run["done"].is_set() else "running"}
                for rid, run in self.runs.items()
                if run["thread"] == parts[1]
            ]
            return httpx.Response(200, json=rows)
        if method == "GET" and parts[2] == "runs" and parts[4:] == ["join"]:
            run = self.runs[parts[3]]
            await run["done"].wait()
            return httpx.Response(200, content=b"  " + json.dumps(run["values"]).encode())
        if method == "POST" and parts[2] == "runs" and parts[4:] == ["cancel"]:
            assert request.url.params.get("action") == "interrupt"
            self.cancelled.append(parts[3])
            run = self.runs[parts[3]]
            run["values"] = self.threads[parts[1]]["values"]
            run["done"].set()
            return httpx.Response(202)
        if (method, path) == ("POST", "/store/items/search"):
            prefix = tuple(body["namespace_prefix"])
            items = [i for k, i in self.store.items() if k[: len(prefix)] == prefix]
            start = body.get("offset", 0)
            return httpx.Response(200, json={"items": items[start : start + body["limit"]]})
        if (method, path) == ("DELETE", "/store/items"):
            self.store.pop((*body["namespace"], body["key"]), None)
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": f"no route {method} {path}"})

    def _run(self, tid: str, body: dict) -> httpx.Response:
        self.bodies.append(body)
        assert body["assistant_id"] == "agent"
        text = body["input"]["messages"][0]["content"]
        values = self.threads[tid]["values"]
        messages = [*values.get("messages", []), {"type": "human", "content": text}]
        if text.startswith("remember "):
            user = self._user(body)
            fact = text.removeprefix("remember ")
            self.store[("memories", user, fact)] = {
                "namespace": ["memories", user],
                "key": fact,
                "value": {"text": fact},
            }
        if text != "slow":
            reply = [{"type": "text", "text": f"echo: {text}"}]
            messages.append({"type": "ai", "content": reply})
        if len(messages) > KEEP:
            values["summary"] = "they talked about " + ", ".join(
                m["content"] for m in messages if m["type"] == "human"
            )
            messages = messages[-KEEP:]
        values["messages"] = messages
        rid = str(uuid.uuid4())
        done = asyncio.Event()
        if text != "slow":
            done.set()
        self.runs[rid] = {"done": done, "values": dict(values), "thread": tid}
        return httpx.Response(200, json={"run_id": rid, "thread_id": tid, "status": "pending"})


def _target(stub: Stub, **options) -> LangGraphTarget:
    conf = TargetConfig(
        "graph", "langgraph", None, None, {"url": "http://lg", "assistant_id": "agent", **options}
    )
    return LangGraphTarget(conf, transport=httpx.MockTransport(stub))


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(langgraph, "STATE_WAIT_SECONDS", 0.0)


async def test_a_session_from_open_to_close(monkeypatch) -> None:
    monkeypatch.setenv("LG_KEY", "key-value")
    stub = Stub()
    target = _target(stub, api_key_env="LG_KEY")
    deadline = time.time() + 5
    session = await target.open("run1")
    assert session.extra["user"] == "eval-run1" and session.trace_value == session.id
    assert stub.threads[session.id]["metadata"] == {"evalution_user": "eval-run1"}
    assert set(target.capabilities.names()) == {
        "isolation",
        "reset",
        "state",
        "conversation_break",
        "parallel",
        "cancel",
        "blocks",
    }

    turn = await target.send(session, "remember tea", deadline=deadline)
    assert turn.error is None and turn.reply == "echo: remember tea"
    assert stub.bodies[0]["config"] == {"configurable": {"user_id": "eval-run1"}}
    assert stub.keys == {"key-value"}
    assert (await target.state(session, {"memory_contains": "TEA"})).ok
    assert not (await target.state(session, {"memory_contains": "coffee"})).ok
    assert (await target.state(session, {"memory_lacks": "coffee"})).ok
    assert not (await target.state(session, {"memory_lacks": "tea"})).ok
    found = await target.state(session, {"state_contains": "$.messages[1] echo: remember"})
    assert found.ok, found
    assert not (await target.state(session, {"state_contains": "$.nothing tea"})).ok
    assert (await target.state(session, {"checkpoint": False})).ok
    assert (await target.state(session, {"messages_trimmed": False})).ok

    # The stub summarises and trims once the thread holds more than two messages.
    await target.send(session, "second", deadline=deadline)
    summary = await target.state(session, {"checkpoint": True})
    assert summary.ok and "remember tea" in summary.actual
    assert (await target.state(session, {"messages_trimmed": True})).ok
    assert not (await target.state(session, {"nope": 1})).ok

    # A break: a new thread for the same user; the store stays.
    first = session.id
    await target.break_conversation(session)
    assert session.id != first and session.trace_value == session.id
    assert stub.threads[session.id]["metadata"] == {"evalution_user": "eval-run1"}
    assert (await target.state(session, {"memory_contains": "tea"})).ok
    assert (await target.state(session, {"checkpoint": False})).ok

    # A reset: a new thread and an empty store.
    await target.reset(session)
    assert not (await target.state(session, {"memory_contains": "tea"})).ok
    assert len(stub.threads) == 3

    # Ending a session keeps what it made; only a target that asks has it removed, and only its own.
    kept = dict(stub.threads)
    await target.close(session)
    assert stub.threads == kept
    tidying = _target(stub, api_key_env="LG_KEY", cleanup=True)
    other = await tidying.open("run2")
    await tidying.close(session)
    assert set(stub.threads) == {other.id}
    with pytest.raises(TargetGone):
        await target.send(session, "hi", deadline=deadline)


async def test_eval_users_are_isolated() -> None:
    stub = Stub()
    target = _target(stub, user_field="context.user_id", cleanup=True)
    deadline = time.time() + 5
    one, two = await target.open("run1-1"), await target.open("run1-2")
    await target.send(one, "remember tea", deadline=deadline)
    await target.send(two, "remember chess", deadline=deadline)
    assert stub.bodies[0]["context"] == {"user_id": "eval-run1-1"}
    assert (await target.state(one, {"memory_contains": "tea"})).ok
    assert not (await target.state(one, {"memory_contains": "chess"})).ok
    await target.close(one)
    assert list(stub.threads) == [two.id]
    assert (await target.state(two, {"memory_contains": "chess"})).ok


async def test_a_case_with_a_break_plays_through() -> None:
    stub = Stub()
    target = _target(stub)
    case = parse_case(
        {
            "name": "n",
            "turns": [
                {"message": "remember tea"},
                {"break": "conversation"},
                {"message": "what do I drink?"},
                {"expect_state": {"memory_contains": "tea", "messages_trimmed": False}},
            ],
        },
        "c",
    )
    session = await target.open("run1")
    first = session.id
    played = await play_try(target, session, case, Path("."), filler=None, source=None)
    assert played.error is None, played.error
    assert session.id != first and [s.ok for s in played.states] == [True, True]


async def test_stop_cancels_the_run() -> None:
    stub = Stub()
    target = _target(stub)
    session = await target.open("run1")
    turn = await target.send(session, "slow", stop_after=0.05, deadline=time.time() + 5)
    assert turn.error is None and turn.reply == "" and len(stub.cancelled) == 1
    turn = await target.send(session, "slow", deadline=time.time() + 0.2)
    assert turn.error == "the turn did not finish in time" and len(stub.cancelled) == 2


async def test_cancel_stops_what_is_still_going_and_nothing_else() -> None:
    stub = Stub()
    target = _target(stub)
    session = await target.open("run1")
    other = await target.open("run2")
    await target.send(session, "hello", deadline=time.time() + 5)
    await target.cancel(session)
    assert stub.cancelled == [], "control: nothing was going"
    going = asyncio.ensure_future(target.send(session, "slow", deadline=time.time() + 5))
    elsewhere = asyncio.ensure_future(target.send(other, "slow", deadline=time.time() + 5))
    await asyncio.sleep(0.01)
    # Rebuilt from its fields, as for a session an earlier process left open.
    await target.cancel(Session(**session.as_dict()))
    assert len(stub.cancelled) == 1 and (await going).error is None
    assert not elsewhere.done()
    elsewhere.cancel()


@pytest.mark.parametrize(
    ("values", "want"),
    [
        ({"messages": []}, TurnEnd.ANSWERED),
        ({"__interrupt__": [{"value": "approve?"}]}, TurnEnd.BLOCKED),
        ({"__error__": "boom"}, TurnEnd.FAILED),
    ],
)
async def test_how_a_run_ended_is_said(monkeypatch, values, want) -> None:
    target = _target(Stub())

    async def joined(*_: object) -> dict:
        return values

    monkeypatch.setattr(target, "_join", joined)
    turn = await target.send(await target.open("run1"), "hello", deadline=time.time() + 5)
    assert turn.ended_as is want


@pytest.mark.parametrize(
    ("answer", "failure"),
    [
        (httpx.Response(401), Failure.REFUSED),
        (httpx.Response(429), Failure.TRANSIENT),
        (httpx.Response(500), Failure.APP),
        # An answer it can't read: the adapter's, not the app's.
        (httpx.Response(200, json={"not": "a run"}), Failure.ADAPTER),
    ],
    ids=["refused", "busy", "error", "unread"],
)
async def test_what_an_answer_to_a_message_says_of_the_app(answer, failure) -> None:
    stub = Stub()

    async def answering(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/runs"):
            return answer
        return await stub(request)

    target = _target(answering)
    session = await target.open("run")
    turn = await target.send(session, "Hi", deadline=time.time() + 5)
    assert turn.failure is failure and turn.error


def test_the_namespace_must_be_the_users_own() -> None:
    with pytest.raises(ConfigError):
        _target(Stub(), store_namespace=["memories", "shared"])


async def test_a_cleanup_checks_each_thread_is_its_own_whatever_the_search_says() -> None:
    stub = Stub()
    target = _target(stub, cleanup=True)
    session = await target.open("run1")
    someone = await _target(stub).open("run2")
    real_person = str(uuid.uuid4())
    stub.threads[real_person] = {"metadata": {"evalution_user": "a-real-person"}, "values": {}}
    no_mark = str(uuid.uuid4())
    stub.threads[no_mark] = {"metadata": {}, "values": {}}

    def ignores_the_filter(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/threads/search":
            rows = [{"thread_id": t, "metadata": r["metadata"]} for t, r in stub.threads.items()]
            return httpx.Response(200, json=rows)
        return stub(request)

    careless = _target(ignores_the_filter, cleanup=True)
    await careless.close(session)
    assert set(stub.threads) == {someone.id, real_person, no_mark}, "its own went, and only that"


async def test_an_error_from_the_server_says_its_status_not_its_address() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/threads":
            return httpx.Response(200, json={"thread_id": "t"})
        return httpx.Response(403)

    conf = TargetConfig(
        "graph",
        "langgraph",
        None,
        None,
        {"url": "http://me:fake-pw@lg.example.test", "assistant_id": "agent"},
    )
    target = LangGraphTarget(conf, transport=httpx.MockTransport(forbidden))
    turn = await target.send(await target.open("r"), "hi", deadline=time.time() + 5)
    assert "403" in turn.error and "fake" not in turn.error
