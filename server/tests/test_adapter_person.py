"""The test person: who plays a run's tries (name, language, time zone), set through
``Target.persona`` and honored by the adapters that declare it. Offline: no app is called."""

import json
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from evalution import adapters
from evalution.adapters import build, contract, infos
from evalution.adapters.base import (
    AdapterInfo,
    Capabilities,
    Isolation,
    Person,
    Session,
    Target,
    Turn,
)
from evalution.adapters.http import HttpTarget
from evalution.config import TargetConfig

ALEX = Person(name="Alex Rivera", language="French", timezone="Europe/Lisbon")


class Older(Target):
    """An adapter written before the test person: it knows nothing of it."""

    capabilities = Capabilities(isolation=Isolation.SESSION, isolation_note="A new id per try.")

    def __init__(self) -> None:
        self.name = "older"
        self.opened = 0
        self.closed: list[Session] = []

    async def open(self, run: str) -> Session:
        self.opened += 1
        return Session(f"s{self.opened}", "session.id", f"s{self.opened}")

    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),  # type: ignore[assignment]
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn:
        return Turn("OK", time.time(), time.time())

    async def close(self, session: Session) -> None:
        self.closed.append(session)


class Named(Older):
    """One that honors a name, and keeps who each session opened as."""

    capabilities = replace(Older.capabilities, person=frozenset({"name"}))

    async def open(self, run: str) -> Session:
        session = await super().open(run)
        session.extra["name"] = self.person.name if self.person else ""
        return session


def _http(http: dict, seen: list[httpx.Request]) -> HttpTarget:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/sessions":
            return httpx.Response(200, json={"id": "made-1"})
        return httpx.Response(200, json={"reply": "OK"})

    conf = TargetConfig("bot", "http", None, None, {"http": http})
    return HttpTarget(conf, transport=httpx.MockTransport(handler))


async def _play(target: Target) -> contract.Report:
    info = AdapterInfo("scripted", "Scripted", (), (), (), None)
    return await contract.run(target, info, short=0.5, grace=1.5, settle=0.05)


# The hook


def test_persona_is_kept_as_the_person_whatever_the_adapter_honors() -> None:
    target = Older()
    assert target.person is None
    target.persona(ALEX)  # it honors none of it, and doesn't refuse
    assert target.person == ALEX and Older.person is None, "kept on the target, not its class"
    assert Person() == Person("", "", "", new_every_try=True)


def test_the_capability_is_said_only_when_declared() -> None:
    assert Older.capabilities.person == frozenset()
    assert "person" not in Older.capabilities.names()
    assert "person" in Named.capabilities.names()


# The http adapter


async def test_http_fills_the_person_in_its_templates_and_declares_only_what_they_use() -> None:
    seen: list[httpx.Request] = []
    target = _http(
        {
            "send": {
                "url": "http://bot/chat",
                "body": {
                    "m": "{message}",
                    "who": "Hi, I'm {person_name}",
                    "tz": "{person_timezone}",
                },
                "headers": {"X-Tz": "{person_timezone}"},
            },
            "reply": "$.reply",
        },
        seen,
    )
    assert target.capabilities.person == frozenset({"name", "timezone"})
    assert "person" in target.capabilities.names()

    target.persona(ALEX)
    session = await target.open("run1")
    assert session.extra["person"] == {"name": "Alex Rivera", "timezone": "Europe/Lisbon"}
    target.persona(Person(name="Someone Else"))  # a session keeps the person it opened as
    turn = await target.send(session, "Hello", deadline=time.time() + 5)
    assert turn.error is None and turn.reply == "OK"
    body = json.loads(seen[-1].content)
    assert body == {"m": "Hello", "who": "Hi, I'm Alex Rivera", "tz": "Europe/Lisbon"}
    assert seen[-1].headers["x-tz"] == "Europe/Lisbon"

    # Rebuilt from its fields, as a later server process would, it is still the same person.
    rebuilt = Session(**json.loads(json.dumps(session.as_dict())))
    await target.send(rebuilt, "Again", deadline=time.time() + 5)
    assert json.loads(seen[-1].content)["who"] == "Hi, I'm Alex Rivera"


async def test_http_fills_an_unchosen_person_with_nothing() -> None:
    seen: list[httpx.Request] = []
    target = _http(
        {
            "send": {
                "url": "http://bot/chat",
                "body": {"m": "{message}", "l": "{person_language}"},
            },
            "reply": "$.reply",
        },
        seen,
    )
    assert target.capabilities.person == frozenset({"language"})
    session = await target.open("run1")  # no persona: the app's own choice
    assert "person" not in session.extra
    await target.send(session, "Hello", deadline=time.time() + 5)
    assert json.loads(seen[-1].content) == {"m": "Hello", "l": ""}


async def test_http_opens_a_session_as_the_person_when_the_app_makes_it() -> None:
    seen: list[httpx.Request] = []
    target = _http(
        {
            "send": {"url": "http://bot/chat/{session}", "body": {"m": "{message}"}},
            "open": {
                "url": "http://bot/sessions",
                "id": "$.id",
                "body": {"name": "{person_name}", "lang": "{person_language}"},
            },
            "reply": "$.reply",
        },
        seen,
    )
    assert target.capabilities.person == frozenset({"name", "language"})
    target.persona(ALEX)
    session = await target.open("run1")
    assert session.id == "made-1"
    assert json.loads(seen[0].content) == {"name": "Alex Rivera", "lang": "French"}


def test_http_without_person_templates_declares_none() -> None:
    target = _http({"send": {"url": "http://bot/chat", "body": {"m": "{message}"}}}, [])
    assert target.capabilities.person == frozenset()
    assert target.capabilities.names() == ["isolation", "parallel"]


# The contract kit


async def test_the_contract_opens_a_session_as_a_test_person() -> None:
    seen: list[httpx.Request] = []
    target = _http(
        {"send": {"url": "http://bot/chat", "body": {"m": "{message}", "n": "{person_name}"}}},
        seen,
    )
    report = await _play(target)
    assert report.ok, report.steps
    step = next(s for s in report.steps if s.name == "opens as a test person")
    assert step.status == "ok" and step.detail == "honors name"
    assert target.person == Person(), "the target's own person is put back"


async def test_the_contract_says_when_opening_as_a_test_person_fails() -> None:
    class Refuses(Named):
        async def open(self, run: str) -> Session:
            if self.person and self.person.name:
                raise httpx.ConnectError("refused")
            return await super().open(run)

    target = Refuses()
    target.persona(Person())
    report = await _play(target)
    step = next(s for s in report.steps if s.name == "opens as a test person")
    assert step.status == "failed" and "couldn't reach the app" in step.detail
    assert not report.ok


async def test_the_contract_refuses_a_field_that_isnt_one() -> None:
    class Odd(Named):
        capabilities = replace(Named.capabilities, person=frozenset({"name", "shoe_size"}))

    report = await _play(Odd())
    step = next(s for s in report.steps if s.name == "opens as a test person")
    assert step.status == "failed" and "shoe_size" in step.detail


async def test_an_adapter_without_a_test_person_plays_the_contract_as_before() -> None:
    target = Older()
    report = await _play(target)
    assert report.ok, report.steps
    assert "opens as a test person" not in {s.name for s in report.steps}
    assert target.person is None and target.opened == 3


@pytest.mark.parametrize("name", ["langgraph", "letta"])
def test_the_built_in_adapters_without_it_are_unaffected(name, monkeypatch) -> None:
    monkeypatch.setattr(adapters, "_built", {})
    options = {"url": "http://127.0.0.1:1", "assistant_id": "agent"}
    target = build(TargetConfig("t", name, None, None, options))
    assert name in infos() and target.capabilities.person == frozenset()
    assert "person" not in target.capabilities.names()
    target.persona(ALEX)  # stored, ignored, never refused
    assert target.person == ALEX
