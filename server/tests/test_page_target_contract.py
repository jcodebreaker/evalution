"""The page target keeps the Target contract without a browser (ADR 0010, phase 3): what it
declares is what it delegates, over inner adapters with and without ``watch``, ``reset`` and
``conversation_break``; a page map plays only on evalution's copy; a ``fresh`` turn is refused."""

import itertools
import time
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from evalution.adapters.base import (
    NO_FILES,
    NO_STOP,
    Capabilities,
    Isolation,
    Person,
    Session,
    StateResult,
    Target,
    Turn,
)
from evalution.config import load
from evalution.core.evidence import Failure
from evalution.runs import isolated
from evalution.runs.fit import why_not
from evalution.runs.page.map import PageMap
from evalution.runs.page.target import PageTarget, page_capabilities
from evalution.suite.cases import parse_case

COPY = "http://127.0.0.1:41234"
MAP = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {"in": {"role": "log", "name": "Messages"}},
}
NEW_CHAT = {"new_chat": {"click": {"role": "button", "name": "New chat"}}}


class Fake(Target):
    """An inner adapter that says what was asked of it; what it declares, a test sets."""

    capabilities = Capabilities(
        Isolation.IDENTITY,
        "A new user.",
        parallel=True,
        cancel=True,
        background=True,
        state_checks=frozenset({"memory_contains"}),
        probe=True,
        sections=True,
        files=True,
        stop=True,
        person=frozenset({"name"}),
    )

    def __init__(self, **declared: bool) -> None:
        self.name = "fake"
        self.capabilities = replace(self.capabilities, **declared)
        self.asked: list[str] = []

    async def open(self, run: str) -> Session:
        return Session("s", "session.id", "s", {"run": run})

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
        self.asked.append("send")
        return Turn("OK", 1.0, 2.0)

    async def close(self, session) -> None:
        self.asked.append("close")

    async def reset(self, session) -> None:
        if not self.capabilities.reset:
            await super().reset(session)
        self.asked.append("reset")

    async def break_conversation(self, session) -> None:
        if not self.capabilities.conversation_break:
            await super().break_conversation(session)
        self.asked.append("break")

    async def watch(self, session, since, *, deadline) -> Turn:
        if not self.capabilities.watch:
            return await super().watch(session, since, deadline=deadline)
        self.asked.append("watch")
        return Turn("OK", since, time.time())

    async def cancel(self, session) -> None:
        if not self.capabilities.cancel:
            await super().cancel(session)
        self.asked.append("cancel")

    async def state(self, session, check) -> StateResult:
        self.asked.append("state")
        return StateResult(True, "x", "held")

    async def probe(self, session, check) -> StateResult:
        self.asked.append("probe")
        return StateResult(False, None, "nothing yet")

    def sections(self, request) -> list:
        self.asked.append("sections")
        return []


INNERS = [
    dict(zip(("watch", "reset", "conversation_break"), flags, strict=True))
    for flags in itertools.product((False, True), repeat=3)
]


def _named(declared: dict[str, bool]) -> str:
    return "+".join(name for name, on in declared.items() if on) or "plain"


@pytest.fixture(
    params=[(d, n) for d in INNERS for n in (False, True)],
    ids=lambda p: f"{_named(p[0])}{'-new_chat' if p[1] else ''}",
)
def wrapped(request, monkeypatch) -> tuple[PageTarget, Fake]:
    declared, new_chat = request.param
    inner = Fake(**declared)
    actions = {**MAP["actions"], **(NEW_CHAT if new_chat else {})}
    target = PageTarget(inner, PageMap.model_validate({**MAP, "actions": actions}), COPY)

    # No browser here: the page's own steps are only counted.
    async def new_context(session: Session) -> None:
        session.extra["page"] = {"context": "k"}
        inner.asked.append("page")

    async def show(page: Any, session: Session) -> None:
        inner.asked.append("shown")

    monkeypatch.setattr(target, "_new_context", new_context)
    monkeypatch.setattr(target, "_show", show)
    monkeypatch.setattr(target, "_page_of", lambda session: None)
    return target, inner


def test_it_declares_its_inner_adapters_capabilities_less_what_a_page_cant(wrapped) -> None:
    target, inner = wrapped
    declared, theirs = target.capabilities, inner.capabilities
    assert not declared.files and not declared.stop and not declared.watch
    changed = {"files", "stop", "watch"}
    for item in fields(Capabilities):
        if item.name not in changed:
            assert getattr(declared, item.name) == getattr(theirs, item.name), item.name
    assert "capabilities" in vars(target), "an instance's own, as http.py's"


async def test_what_it_declares_is_what_it_delegates(wrapped) -> None:
    target, inner = wrapped
    declared = target.capabilities
    session = Session("s", "session.id", "s", {"page": {"context": "k"}})

    if declared.reset:
        await target.reset(session)
        assert inner.asked[-3:] == ["reset", "page", "shown"]
    else:
        with pytest.raises(NotImplementedError):
            await target.reset(session)

    if declared.conversation_break:
        await target.break_conversation(session)
        assert inner.asked[-2:] == ["break", "shown"]
    else:
        with pytest.raises(NotImplementedError):
            await target.break_conversation(session)
        assert "break" not in inner.asked, "the page couldn't follow it there"

    with pytest.raises(NotImplementedError):
        await target.watch(session, time.time(), deadline=time.time() + 5)

    assert (await target.state(session, {"memory_contains": "x"})).ok
    assert not (await target.probe(session, {"memory_contains": "x"})).ok
    assert target.sections({"messages": []}) == []
    await target.cancel(session)
    assert {"state", "probe", "sections", "cancel"} <= set(inner.asked)

    deadline = time.time() + 5
    asked = len(inner.asked)
    with_file = await target.send(session, "hi", files=[Path("note.txt")], deadline=deadline)
    stopped = await target.send(session, "hi", stop_after=0.1, deadline=deadline)
    assert (with_file.error, stopped.error) == (NO_FILES, NO_STOP)
    assert len(inner.asked) == asked, "refused before the inner adapter is asked"

    target.persona(Person(name="Ana"))
    assert inner.person == target.person == Person(name="Ana")


async def test_an_inner_adapter_that_cant_cancel_isnt_made_to() -> None:
    inner = Fake(cancel=False)
    target = PageTarget(inner, PageMap.model_validate(MAP), COPY)
    assert not target.capabilities.cancel
    with pytest.raises(NotImplementedError):
        await target.cancel(Session("s", "session.id", "s"))


async def test_close_without_a_browser_still_closes_the_inner_session() -> None:
    inner = Fake()
    target = PageTarget(inner, PageMap.model_validate(MAP), COPY)
    for extra in ({}, {"page": {"context": "from-another-process"}}, {"page": None}):
        await target.close(Session("s", "session.id", "s", extra))
    assert inner.asked == ["close"] * 3


def test_page_capabilities_follow_the_inner_adapters() -> None:
    plain = Capabilities(Isolation.SESSION, "New id.", conversation_break=True, files=True)
    page = PageMap.model_validate(MAP)
    assert page_capabilities(plain, page).conversation_break
    # The page sending doesn't stop it: the adapter starts the conversation, the page opens it.
    assert page_capabilities(replace(plain, watch=True), page).conversation_break


# Refusals


def _config(tmp_path: Path, page: dict[str, Any] | None) -> Any:
    block: dict[str, Any] = {
        "adapter": "http",
        "http": {"send": {"url": "http://127.0.0.1:9/x"}, "reply": "$.reply"},
    }
    if page is not None:
        block["page"] = page
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"chat": block}}))
    return load(tmp_path / "evalution.yaml")


def test_a_page_map_without_a_managed_footing_is_refused_in_plain_words(tmp_path) -> None:
    config = _config(tmp_path, MAP)
    said = isolated.refusal(config, "chat", isolated.Footing("none"))
    assert said is not None
    assert "can't play through its page" in said and "evalution's own copy" in said
    assert "`evalution isolate --target chat`" in said
    assert isolated.refusal(_config(tmp_path, None), "chat", isolated.Footing("none")) is None
    bad = _config(tmp_path, {**MAP, "url": "http://localhost:5173/chat"})
    assert "targets.chat.page.url" in (
        isolated.refusal(bad, "chat", isolated.Footing("none")) or ""
    )


async def test_a_fresh_turn_is_refused_in_page_mode() -> None:
    case = parse_case({"name": "n", "turns": [{"message": "hi", "fresh": True}]}, "c", "c")
    capabilities = Capabilities(Isolation.MANAGED, "On evalution's copy.")
    assert why_not(case, capabilities, True) is None
    said = why_not(case, capabilities, True, page=True)
    assert said is not None and "page" in said

    target = PageTarget(Fake(), PageMap.model_validate(MAP), COPY)
    session = Session("s", "session.id", "s", {"fresh": "ab12"})
    turn = await target.send(session, "hi", deadline=time.time() + 5)
    assert turn.failure is Failure.NOT_SUPPORTED and turn.error
