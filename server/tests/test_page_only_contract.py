"""A target reached through its page alone keeps the Target contract without a browser (ADR 0010,
amended): it declares what a page can do and nothing more, the stand-in ``adapter: page`` makes
outside a run says the same and plays nothing, its map reads like any other's, a run without a
managed footing is refused, and an empty or missing reply on its page is never the adapter's."""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_page_target_contract import COPY, MAP, NEW_CHAT, Fake

from evalution.adapters import infos, make
from evalution.adapters.base import NO_FILES, NO_STOP, Isolation, Person, Session
from evalution.adapters.page import PlaysOnlyOnItsPage
from evalution.config import ConfigError, TargetConfig, load
from evalution.core.evidence import (
    Clipped,
    Failure,
    PageRecord,
    PageTurn,
    TryEvidence,
    TurnRecord,
)
from evalution.core.judgement import Outcome, Role
from evalution.core.rendered import NOTHING_SHOWN, reply_after
from evalution.judging.checks import _shown_check
from evalution.runs import isolated, play
from evalution.runs.page.map import PageMap, as_block, page_map_of, page_only, read_map
from evalution.runs.page.target import PageTarget
from evalution.runs.play import Played
from evalution.traces.base import Span

ONLY = {key: value for key, value in MAP.items() if key != "open_session"}


@pytest.fixture(params=[False, True], ids=["plain", "new_chat"])
def alone(request, monkeypatch) -> PageTarget:
    actions = {**MAP["actions"], **(NEW_CHAT if request.param else {})}
    target = PageTarget(None, PageMap.model_validate({**ONLY, "actions": actions}), COPY)
    asked: list[str] = []
    target.asked = asked  # type: ignore[attr-defined]

    # No browser here: the page's own steps are only counted.
    async def live(session: Session) -> None:
        return None

    async def new_chat(page: Any) -> None:
        asked.append("new_chat")

    monkeypatch.setattr(target, "_live", live)
    monkeypatch.setattr(target, "_new_chat", new_chat)
    monkeypatch.setattr(target, "_where", lambda page, session: None)
    return target


def test_it_declares_only_what_a_page_can_do(alone) -> None:
    declared = alone.capabilities
    assert declared.conversation_break is (alone.page.actions.new_chat is not None)
    assert declared.isolation is Isolation.NONE, "a run's copy makes it managed"
    assert declared.person == frozenset({"language", "timezone"})
    never = ("reset", "sections", "files", "stop", "parallel", "probe", "background", "cancel")
    for name in (*never, "blocks", "deletes", "watch"):
        assert not getattr(declared, name), name
    assert not declared.state_checks
    assert alone.page_only and alone.inner is None


async def test_what_it_declares_is_what_it_does(alone) -> None:
    session = Session("s", "session.id", "s", {"page": {"context": "k"}, "run": "r", "try": 1})
    with pytest.raises(NotImplementedError):
        await alone.reset(session)
    with pytest.raises(NotImplementedError):
        await alone.cancel(session)
    with pytest.raises(NotImplementedError):
        await alone.watch(session, time.time(), deadline=time.time() + 5)
    with pytest.raises(NotImplementedError):
        await alone.probe(session, {"memory_contains": "x"})
    assert not (await alone.state(session, {"memory_contains": "x"})).observed
    assert await alone.read_memory(session) is None
    assert alone.sections({"messages": []}) == [] and alone.answers() == []
    if alone.capabilities.conversation_break:
        await alone.break_conversation(session)
        assert alone.asked == ["new_chat"]  # type: ignore[attr-defined]
        assert session.trace_value.startswith("evalution-page-r-t1-"), "a new id of its own"
    else:
        with pytest.raises(NotImplementedError):
            await alone.break_conversation(session)
    deadline = time.time() + 5
    with_file = await alone.send(session, "hi", files=[Path("note.txt")], deadline=deadline)
    stopped = await alone.send(session, "hi", stop_after=0.1, deadline=deadline)
    assert (with_file.error, stopped.error) == (NO_FILES, NO_STOP)
    alone.persona(Person(language="pt", timezone="Europe/Lisbon"))
    assert alone._context_options() == {"locale": "pt", "timezone_id": "Europe/Lisbon"}


async def test_close_needs_no_browser_and_no_inner_adapter() -> None:
    target = PageTarget(None, PageMap.model_validate(ONLY), COPY)
    for extra in ({}, {"page": {"context": "from-another-process"}}, {"page": None}):
        await target.close(Session("s", "session.id", "s", extra))


def test_the_stand_in_says_what_the_page_target_will() -> None:
    """``adapter: page`` makes a stand-in outside a run, no kind of app: what it declares is the
    page target's own, and it plays nothing."""
    for actions in (MAP["actions"], {**MAP["actions"], **NEW_CHAT}):
        page = {**ONLY, "actions": actions}
        conf = TargetConfig("chat", "page", None, None, {"adapter": "page", "page": page})
        stand_in = make(conf)
        played = PageTarget(None, PageMap.model_validate(page), COPY)
        assert stand_in.capabilities == played.capabilities
    assert "page" not in infos(), "not a kind of app"
    with pytest.raises(PlaysOnlyOnItsPage, match="only on evalution's own copy"):
        asyncio.run(stand_in.open("r"))
    asyncio.run(stand_in.close(Session("s", "session.id", "s")))


def test_its_map_reads_and_a_map_over_an_adapter_needs_open_session() -> None:
    only = TargetConfig("chat", "page", None, None, {"adapter": "page", "page": ONLY})
    assert page_map_of(only) is not None and page_only(only)
    with pytest.raises(ConfigError, match=r"targets\.chat\.page: missing"):
        page_map_of(TargetConfig("chat", "page", None, None, {"adapter": "page"}))
    with pytest.raises(ConfigError, match="open_session: missing"):
        page_map_of(TargetConfig("chat", "http", None, None, {"page": ONLY}))
    with pytest.raises(ConfigError, match="open_session"):
        PageTarget(Fake(), PageMap.model_validate(ONLY), COPY)


@pytest.mark.parametrize(
    ("pattern", "said"),
    [("/chat/(.+)", "needs a group named id"), ("/chat/(?P<id>[", "isn't a regular expression")],
)
def test_session_id_is_read_as_the_rest_of_the_map(pattern, said) -> None:
    with pytest.raises(ConfigError, match=re.escape(said)):
        read_map({**ONLY, "session_id": {"from": "url", "pattern": pattern}})
    with pytest.raises(ConfigError, match="from"):
        read_map({**ONLY, "session_id": {"from": "title", "pattern": "(?P<id>.+)"}})
    with pytest.raises(ConfigError, match="quiet_ms"):
        read_map({**ONLY, "quiet_ms": -1})
    found = read_map({**ONLY, "session_id": {"from": "url", "pattern": r"/c/(?P<id>\w+)"}})
    assert found.quiet_ms == 800 and found.session_id is not None
    assert as_block(found)["session_id"] == {"from": "url", "pattern": r"/c/(?P<id>\w+)"}


def test_without_a_managed_footing_it_is_refused(tmp_path) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"chat": {"adapter": "page", "page": ONLY}}})
    )
    said = isolated.refusal(load(tmp_path / "evalution.yaml"), "chat", isolated.Footing("none"))
    assert said is not None and "can't play through its page" in said


def test_the_reply_is_what_follows_the_message_on_the_page() -> None:
    assert reply_after("hello\nEcho: hello", message="hello") == "Echo: hello"
    page = "hello\nEcho: hello\nagain\nEcho: again"
    assert reply_after(page, message="again", after=3) == "Echo: again", "this turn's only"
    assert reply_after("hello", message="hello") == "", "nothing after it"
    assert reply_after("Hi there", message="unseen", after=0) == "Hi there", "no echo: all new"


def _played(failure: Failure | None, error: str | None) -> Played:
    made = Played(t0=9.0, page_only=True, sent_at=10.0, sent_text="hi", turns_sent=1)
    made.records.append(
        TurnRecord(
            kind="message",
            message=Clipped(text="hi"),
            reply=Clipped(),
            started=10.0,
            ended=12.0,
            error=error,
        )
    )
    made.failure, made.error = failure, error
    return made


def test_the_page_alone_never_blames_an_adapter() -> None:
    """A page-only turn that showed nothing stays the app's failure; the traces only add that the
    agent answered (answered, not shown). An empty reply with no error isn't re-labelled."""
    said = {"role": "assistant", "content": "Done."}
    root = Span("root", "t", None, "turn", "AGENT", 10.0, 12.0, "OK", {})
    attrs = {"gen_ai": {"output": {"messages": json.dumps([said])}}}
    answered = [Span("a", "t", "root", "llm", "LLM", 10.0, 11.0, "OK", attrs), root]

    lost = _played(Failure.APP, NOTHING_SHOWN)
    play._check_the_page(lost, answered)
    assert lost.failure is Failure.APP
    assert "answered, not shown" in (lost.error or "")
    plain = _played(Failure.APP, NOTHING_SHOWN)
    play._check_the_page(plain, [])
    assert (plain.failure, plain.error) == (Failure.APP, NOTHING_SHOWN), "a plain failure"
    late = _played(Failure.TIMED_OUT, "the page was still busy when the time ran out")
    play._check_the_page(late, answered)
    assert late.failure is Failure.TIMED_OUT and "answered, not shown" in (late.error or "")
    empty = _played(None, None)
    play._check_the_page(empty, answered)
    assert empty.failure is None
    through = _played(None, None)
    through.page_only = False
    play._check_the_adapter(through, answered, 0.0)
    assert through.failure is Failure.ADAPTER, "control: through an adapter it is the adapter's"


def test_nothing_shown_through_the_page_alone_fails_the_shown_check() -> None:
    turn = TurnRecord(
        kind="message",
        message=Clipped(text="hi"),
        reply=Clipped(),
        started=1.0,
        ended=2.0,
        error=NOTHING_SHOWN,
    )
    page = PageRecord(turns=(PageTurn(instead=Clipped(text="hi")),), page_only=True)
    evidence = TryEvidence(
        case="c",
        n=1,
        t0=0.0,
        seconds=2.0,
        turns=(turn,),
        page=page,
        failure=Failure.APP,
        error=NOTHING_SHOWN,
    )
    found = _shown_check(evidence, 1, Role.GUIDE)
    assert found is not None and found.outcome is Outcome.FAILED
    beside = evidence.model_copy(update={"page": page.model_copy(update={"page_only": False})})
    found = _shown_check(beside, 1, Role.GUIDE)
    assert found is not None and found.outcome is Outcome.UNJUDGED, "an adapter's error: no reply"
