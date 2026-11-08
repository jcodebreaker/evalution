"""The http adapter against a stub chat endpoint: done on response, and done by polling."""

import json
import time
from pathlib import Path

import httpx
import pytest

from evalution.adapters import http as http_module
from evalution.adapters.http import HttpTarget, json_path, until
from evalution.config import ConfigError, TargetConfig
from evalution.core import net
from evalution.core.evidence import Failure, TurnEnd
from evalution.runs.play import play_try
from evalution.suite.cases import parse_case


def _target(http: dict, handler) -> HttpTarget:
    conf = TargetConfig("bot", "http", None, None, {"http": http})
    return HttpTarget(conf, transport=httpx.MockTransport(handler))


async def test_reply_from_the_response() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["session"] = request.headers.get("x-session")
        seen["tags"] = (
            request.headers.get("x-evalution-run"),
            request.headers.get("x-evalution-try"),
        )
        return httpx.Response(200, json={"out": {"text": "Montevideo"}, "trace": "abc"})

    target = _target(
        {
            "send": {"url": "http://bot/chat", "body": {"m": "{message}", "s": "{session}"}},
            "reply": "$.out.text",
            "session_header": "X-Session",
            "trace_id": "$.trace",
        },
        handler,
    )
    session = await target.open("run1")
    turn = await target.send(session, "Capital?", deadline=time.time() + 5)
    assert turn.error is None and turn.reply == "Montevideo" and turn.trace_ids == ["abc"]
    assert seen["body"] == {"m": "Capital?", "s": session.id}
    assert seen["session"] == session.id
    assert session.trace_value == session.id and target.capabilities.names() == [
        "isolation",
        "parallel",
    ]
    # Eval sessions are tagged with the run and the try, in the id and in headers.
    assert session.id.startswith("evalution-run1-t1-") and seen["tags"] == ("run1", "1")
    assert (await target.open("run1")).id.startswith("evalution-run1-t2-")


async def test_done_by_polling() -> None:
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202)
        polls["n"] += 1
        status = "idle" if polls["n"] >= 2 else "busy"
        return httpx.Response(200, json={"status": status, "reply": f"after {polls['n']}"})

    target = _target(
        {
            "send": {"url": "http://bot/chat", "body": {"m": "{message}"}},
            "done": {
                "poll": {
                    "url": "http://bot/status/{session}",
                    "until": "$.status == 'idle'",
                    "interval": 0.01,
                }
            },
            "reply": "$.reply",
        },
        handler,
    )
    session = await target.open("run1")
    turn = await target.send(session, "hi", deadline=time.time() + 5)
    assert turn.error is None and turn.reply == "after 2"


async def test_errors_and_paths() -> None:
    target = _target(
        {"send": {"url": "http://bot/chat"}}, lambda request: httpx.Response(500, text="boom")
    )
    turn = await target.send(await target.open("r"), "hi", deadline=time.time() + 5)
    assert turn.error and "500" in turn.error
    assert json_path({"a": [{"b": 1}]}, "$.a[0].b") == 1
    assert until({"s": "idle"}, "$.s != 'busy'")


def _polling(status: str, **poll) -> HttpTarget:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"status": status, "reply": "Queued!", "error": "no disk"})

    conf = {
        "url": "http://bot/s/{session}",
        "until": "$.status == 'idle'",
        "reply": "$.reply",
        "interval": 0.01,
        "failed_when": "$.status == 'failed'",
        "blocked_when": "$.status == 'waiting'",
        **poll,
    }
    return _target({"send": {"url": "http://bot/chat"}, "done": {"poll": conf}}, handler)


@pytest.mark.parametrize(
    ("status", "ended_as", "error"),
    [
        ("idle", TurnEnd.ANSWERED, None),
        ("failed", TurnEnd.FAILED, "no disk"),
        ("waiting", TurnEnd.BLOCKED, "no disk"),
    ],
)
async def test_each_poll_tells_how_the_work_ended(status, ended_as, error) -> None:
    target = _polling(status, why="$.error")
    turn = await target.send(await target.open("r"), "hi", deadline=time.time() + 5)
    assert (turn.ended_as, turn.error, turn.reply) == (ended_as, error, "Queued!")
    assert target.capabilities.blocks
    # Without a path to read it from, there is no reason yet: play says one in plain words.
    bare = await (t := _polling(status)).send(await t.open("r"), "hi", deadline=time.time() + 5)
    assert bare.ended_as is ended_as and bare.error is None


async def test_a_poll_that_never_ends_runs_out_of_time() -> None:
    target = _polling("busy")
    turn = await target.send(await target.open("r"), "hi", deadline=time.time() + 0.02)
    assert turn.failure is Failure.TIMED_OUT


async def test_watch_waits_by_polling_on_a_turn_the_page_sent() -> None:
    """With done.poll, a turn something else sent is read the way send reads one; it sends
    nothing itself, and a turn that errors after was heard."""
    said: list[str] = []
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        said.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        polls["n"] += 1
        status = "idle" if polls["n"] >= 2 else "busy"
        return httpx.Response(200, json={"status": status, "reply": "Biscuit"})

    target = _target(
        {**BOT, "done": {"poll": {**POLL["poll"], "interval": 0.01}}, "reply": "$.reply"}, handler
    )
    assert target.capabilities.watch
    session = await target.open("run")
    since = time.time() - 1
    turn = await target.watch(session, since, deadline=time.time() + 5)
    assert (turn.reply, turn.error, turn.started, turn.ended_as) == (
        "Biscuit",
        None,
        since,
        TurnEnd.ANSWERED,
    )
    assert "POST" not in said and polls["n"] == 2

    looks: list[int] = []

    def fails(request: httpx.Request) -> httpx.Response:
        looks.append(1)
        status = "busy" if len(looks) == 1 else "failed"
        return httpx.Response(200, json={"status": status, "error": "no disk"})

    poll = {
        **POLL["poll"],
        "interval": 0.01,
        "failed_when": "$.status == 'failed'",
        "why": "$.error",
    }
    failing = _target({**BOT, "done": {"poll": poll}}, fails)
    turn = await failing.watch(await failing.open("r"), time.time(), deadline=time.time() + 5)
    assert (turn.ended_as, turn.error) == (TurnEnd.FAILED, "no disk")
    late = _polling("busy")
    turn = await late.watch(await late.open("r"), time.time(), deadline=time.time() + 0.02)
    assert turn.failure is Failure.TIMED_OUT

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    gone = _target({**BOT, "done": POLL}, down)
    turn = await gone.watch(await gone.open("r"), time.time(), deadline=time.time() + 5)
    assert turn.failure is Failure.TRANSIENT and turn.heard, "the page sent it"


async def test_watch_never_takes_the_reply_from_before_the_page_sent() -> None:
    """The app still says idle, with the old reply, for a moment after the page's message."""
    sent_at = time.time()

    def handler(request: httpx.Request) -> httpx.Response:
        after = time.time() - sent_at
        if after < 0.3:
            return httpx.Response(200, json={"status": "idle", "reply": "Old"})
        if after < 0.5:
            return httpx.Response(200, json={"status": "busy", "reply": "Old"})
        return httpx.Response(200, json={"status": "idle", "reply": "New"})

    poll = {"poll": {**POLL["poll"], "interval": 0.05}}
    target = _target({**BOT, "done": poll}, handler)
    turn = await target.watch(await target.open("r"), sent_at, deadline=time.time() + 5)
    assert (turn.error, turn.reply) == (None, "New")

    def straight_to_new(request: httpx.Request) -> httpx.Response:
        new = time.time() - sent_at >= 0.3
        return httpx.Response(200, json={"status": "idle", "reply": "New" if new else "Old"})

    sent_at = time.time()
    target = _target({**BOT, "done": poll}, straight_to_new)
    turn = await target.watch(await target.open("r"), sent_at, deadline=time.time() + 5)
    assert turn.reply == "New", "a reply that changed is a move, though it never looked busy"

    def never_moves(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "idle", "reply": "Old"})

    target = _target({**BOT, "done": poll}, never_moves)
    turn = await target.watch(await target.open("r"), time.time(), deadline=time.time() + 0.3)
    assert turn.failure is Failure.TIMED_OUT and turn.heard and turn.reply == ""
    # send, which sent the message itself, takes a done session at its word, as before.
    turn = await target.send(await target.open("r"), "hi", deadline=time.time() + 5)
    assert (turn.error, turn.reply) == (None, "Old")


async def test_watch_isnt_declared_without_a_poll() -> None:
    target = _target(BOT, lambda _request: httpx.Response(200, json={"reply": "Hi"}))
    assert not target.capabilities.watch
    with pytest.raises(NotImplementedError):
        await target.watch(await target.open("r"), time.time(), deadline=time.time() + 5)


def test_what_a_poll_checks_must_read() -> None:
    with pytest.raises(ConfigError, match="failed_when"):
        _polling("idle", failed_when="status is failed")
    with pytest.raises(ConfigError, match="cancel"):
        _target({"send": {"url": "u"}, "cancel": {"method": "POST"}}, lambda r: httpx.Response(200))
    assert not _target({"send": {"url": "u"}}, lambda r: httpx.Response(200)).capabilities.blocks


async def test_cancel_goes_to_this_tries_session_only() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    http = {"send": {"url": "http://bot/chat"}, "cancel": {"url": "http://bot/stop/{session}"}}
    target = _target(http, handler)
    one, two = await target.open("r"), await target.open("r")
    assert one.id != two.id, "a session id is unique to its try"
    await target.cancel(one)
    assert [str(r.url) for r in seen] == [f"http://bot/stop/{one.id}"]
    assert seen[0].method == "POST" and seen[0].headers["X-Evalution-Try"] == "1"
    await target.close(one)
    assert len(seen) == 1, "close keeps what the try left"
    with pytest.raises(NotImplementedError):
        await _target({"send": {"url": "u"}}, handler).cancel(one)


async def test_fresh_never_touches_the_message() -> None:
    """A fresh try gets a new hidden token each try, carried by {fresh}; the person's words stay
    as the case wrote them."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"reply": "ok"})

    target = _target(
        {
            "send": {"url": "http://bot/chat", "body": {"m": "{message}", "f": "{fresh}"}},
            "reply": "$.reply",
        },
        handler,
    )
    case = parse_case({"name": "n", "turns": [{"message": "Make a deck", "fresh": True}]}, "c")
    for _ in range(2):
        session = await target.open("run1")
        played = await play_try(target, session, case, Path("."), filler=None, source=None)
        assert played.error is None and played.turns[0]["message"] == "Make a deck"
    assert [s["m"] for s in seen] == ["Make a deck", "Make a deck"]
    assert seen[0]["f"] and seen[1]["f"] and seen[0]["f"] != seen[1]["f"]


BOT = {"send": {"url": "http://bot/chat", "body": {"m": "{message}"}}, "reply": "$.reply"}
POLL = {"poll": {"url": "http://bot/state", "until": "$.status == 'idle'", "reply": "$.reply"}}


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        (401, Failure.REFUSED),
        (403, Failure.REFUSED),
        (429, Failure.TRANSIENT),
        (502, Failure.TRANSIENT),
        (503, Failure.TRANSIENT),
        (504, Failure.TRANSIENT),
        (500, Failure.APP),
        (404, Failure.APP),
    ],
)
async def test_what_a_status_says_of_the_app(status, failure) -> None:
    target = _target(BOT, lambda _request: httpx.Response(status))
    session = await target.open("run")
    turn = await target.send(session, "Hi", deadline=time.time() + 5)
    assert turn.failure is failure and turn.error
    assert turn.heard is (status in (502, 504)), "a gateway may have passed it on"


async def test_a_poll_that_fails_comes_after_the_app_heard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}) if request.method == "POST" else httpx.Response(503)

    target = _target({**BOT, "done": POLL}, handler)
    session = await target.open("run")
    turn = await target.send(session, "Hi", deadline=time.time() + 5)
    assert turn.failure is Failure.TRANSIENT and turn.heard


async def test_settings_that_cant_read_the_answer_are_the_adapters() -> None:
    bad = {**BOT, "reply": "no.dollar"}
    target = _target(bad, lambda _request: httpx.Response(200, json={"reply": "Hi"}))
    session = await target.open("run")
    turn = await target.send(session, "Hi", deadline=time.time() + 5)
    assert turn.failure is Failure.ADAPTER and turn.error


def test_background_needs_a_poll_to_wait_for_the_work() -> None:
    with pytest.raises(ConfigError, match=r"background.*done\.poll"):
        _target({**BOT, "background": True}, lambda _request: httpx.Response(200))
    with pytest.raises(ConfigError, match=r"background.*done\.poll"):
        _target({**BOT, "background": True, "done": "response"}, lambda _r: httpx.Response(200))
    with pytest.raises(ValueError, match=r"background.*done\.poll"):
        http_module.INFO.validate({**BOT, "done": "response", "background": True})
    # Control: with one it means what it says, and off it asks for nothing.
    target = _target({**BOT, "background": True, "done": POLL}, lambda _r: httpx.Response(200))
    assert target.capabilities.background
    http_module.INFO.validate({**BOT, "done": POLL, "background": True})
    assert not _target(BOT, lambda _r: httpx.Response(200)).capabilities.background
    http_module.INFO.validate({**BOT, "done": "response"})


async def test_an_error_keeps_the_status_and_path_never_the_urls_login_or_query() -> None:
    url = "http://me:fake-pw@bot.example.test/chat?api_key=fake-key"
    target = _target({"send": {"url": url}}, lambda request: httpx.Response(401))
    turn = await target.send(await target.open("r"), "hi", deadline=time.time() + 5)
    assert turn.error == "HTTPStatusError: 401 from /chat"
    assert turn.failure is Failure.REFUSED, "how it failed is read from the error itself"

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"can't connect to {request.url}", request=request)

    target = _target({"send": {"url": url}}, refused)
    turn = await target.send(await target.open("r"), "hi", deadline=time.time() + 5)
    assert "fake-pw" not in turn.error and "fake-key" not in turn.error
    assert "bot.example.test" in turn.error

    # What an adapter of someone else's quotes is cut down the same way; other words stay.
    said = "Bad 'http://me:fake-pw@bot.example.test:81/chat?k=fake-key#x' and postgres://u:p@h/d"
    assert net.no_login(said) == "Bad 'http://bot.example.test:81/chat' and postgres://h/d"
    assert net.said(ValueError(said)) == f"ValueError: {net.no_login(said)}"
    assert net.no_login("nothing to see: 12:30 a/b") == "nothing to see: 12:30 a/b"


async def test_url_is_where_evalution_points_the_target() -> None:
    """``{url}`` is the target's ``url``: on evalution's copy, the copy's address."""
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, json={"reply": "Hi"})

    http = {"send": {"url": "{url}/chat/{session}"}, "reply": "$.reply"}
    conf = TargetConfig("bot", "http", None, None, {"url": "http://copy:9/", "http": http})
    target = HttpTarget(conf, transport=httpx.MockTransport(handler))
    session = await target.open("r")
    turn = await target.send(session, "Hi", deadline=time.time() + 5)
    assert turn.reply == "Hi" and called == [f"http://copy:9/chat/{session.id}"]
