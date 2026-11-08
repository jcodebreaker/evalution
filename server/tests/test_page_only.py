"""A target reached through its page alone, in a real browser (ADR 0010, amended): no API adapter
under it, so the page sends, its own signs say when the turn is done (busy first, then quiet), and
what it shows is the reply. A page that showed nothing is the app's failure, one still busy at the
deadline is out of time, a renamed Send is the map's, and the session's id comes from the page's
address when the map says where.

Skipped without the page extra or a browser, as ``test_page_target.py`` is."""

import time

import pytest

pytest.importorskip(
    "playwright", reason="page mode's tests need the page extra: uv sync --extra page"
)

from fastapi import FastAPI
from test_page_target import MAP, SECRET_ENV, ChatApp, Served

from evalution.core.evidence import Failure
from evalution.core.rendered import NOTHING_SHOWN
from evalution.runs.page.map import PageMap
from evalution.runs.page.target import STILL_BUSY, PageTarget


@pytest.fixture
def chat():
    """The made-up chat page of ``test_page_target.py``, with another origin it reaches."""
    with Served(FastAPI()) as other:
        made = ChatApp(other.url)
        with Served(made.app) as served:
            made.url = served.url  # type: ignore[attr-defined]
            yield made


@pytest.fixture(autouse=True)
def secret(monkeypatch) -> None:
    monkeypatch.setenv(SECRET_ENV, "s3cret")


#: The made-up chat page, reached through it alone: no open_session, the chat at its own address.
ONLY = {
    **{key: value for key, value in MAP.items() if key != "open_session"},
    "url": "{copy_page}/chat/one",
    "session_id": {"from": "url", "pattern": r"/chat/(?P<id>[^/?#]+)"},
    "quiet_ms": 300,
}


def _only(chat: ChatApp, **changed) -> PageTarget:
    page = PageMap.model_validate({**ONLY, **changed})
    return PageTarget(None, page, chat.url, element_seconds=1.5, name="chat")  # type: ignore[attr-defined]


async def test_the_reply_is_what_the_page_shows_and_the_session_is_the_pages(chat) -> None:
    target = _only(chat)
    try:
        session = await target.open("r")
        assert (session.id, session.trace_value) == ("one", "one"), "read from the page's address"
        assert (session.extra["run"], session.extra["try"]) == ("r", 1), "for the try's headers"
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert turn.error is None, turn.error
        assert turn.reply == turn.shown == "Echo: hello", "the page is the only witness"
        sent = [p for p, _ in chat.seen if p == "/api/send"]
        assert sent == ["/api/send"], "the browser sent the message"
        again = await target.send(session, "hello", deadline=time.time() + 20)
        assert again.reply == "Echo: hello" and again.error is None, "the second turn's own"
    finally:
        await target.dispose()


async def test_busy_first_then_quiet(chat) -> None:
    """The reply lands well after Send: Stop shows meanwhile, and the turn waits it out, however
    short its quiet time."""
    chat.lag = 1.5
    target = _only(chat, quiet_ms=100)
    try:
        session = await target.open("r")
        sent = time.time()
        turn = await target.send(session, "slow", deadline=time.time() + 20)
        assert turn.error is None, turn.error
        assert turn.reply == "Echo: slow"
        assert turn.ended - sent >= 1.8, "not done while the page said Stop"
    finally:
        await target.dispose()


async def test_a_page_that_never_stops_being_busy_runs_out_of_time(chat) -> None:
    chat.never_idle = True
    target = _only(chat)
    try:
        session = await target.open("r")
        started = time.time()
        turn = await target.send(session, "hello", deadline=started + 3)
        assert (turn.failure, turn.error, turn.reply) == (Failure.TIMED_OUT, STILL_BUSY, "")
        assert turn.heard and turn.shown is None
        assert time.time() - started < 6, "the deadline holds"
        assert "Echo: hello" in session.extra["page"]["last_shown"], "what showed is kept"
    finally:
        await target.dispose()


async def test_a_page_that_shows_nothing_is_the_apps_failure(chat) -> None:
    chat.hide = True
    target = _only(chat)
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert (turn.failure, turn.error, turn.reply, turn.shown) == (
            Failure.APP,
            NOTHING_SHOWN,
            "",
            None,
        ), "never the adapter's: there is none"
    finally:
        await target.dispose()


async def test_what_the_page_shows_in_place_of_the_reply_is_the_reply(chat) -> None:
    """With no marks on the page's replies, all it shows after the message is the reply: a
    placeholder in its place is what judging reads, and fails on."""
    chat.drop_from = 0
    target = _only(chat)
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert turn.error is None
        assert turn.reply == turn.shown == "Couldn't load this reply"
    finally:
        await target.dispose()


async def test_a_renamed_send_is_the_page_maps_failure(chat) -> None:
    chat.rename = True
    target = _only(chat)
    try:
        session = await target.open("r")
        turn = await target.send(session, "hello", deadline=time.time() + 20)
        assert turn.failure is Failure.PAGE_MAP and "Send" in (turn.error or "")
        assert not [p for p, _ in chat.seen if p == "/api/send"]
    finally:
        await target.dispose()


async def test_without_session_id_the_session_is_evaluations_own(chat) -> None:
    page = {key: value for key, value in ONLY.items() if key != "session_id"}
    target = PageTarget(None, PageMap.model_validate(page), chat.url, element_seconds=1.5)  # type: ignore[attr-defined]
    try:
        session = await target.open("r")
        assert session.id == session.trace_value and session.id.startswith("evalution-page-r-t1-")
        second = await target.open("r")
        assert second.extra["try"] == 2 and second.id != session.id
        assert session.extra["email"] != second.extra["email"], "a new person each try"
        turn = await target.send(session, "hi", deadline=time.time() + 20)
        assert turn.reply == "Echo: hi"
        sent = [h for p, h in chat.seen if p == "/api/send"]
        assert sent and sent[0]["x-evalution-run"] == "r" and sent[0]["x-evalution-try"] == "1"
        assert session.id.startswith("evalution-page-"), "the page's address names none"
    finally:
        await target.dispose()
