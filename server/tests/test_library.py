"""The stateful test library: every template loads and fills into a valid case, fit verdicts,
adapting with profile evidence, and the conversation break it relies on."""

import json
from pathlib import Path

import pytest
import yaml

from evalution.adapters.base import Capabilities, Isolation, Session, Turn
from evalution.config import load
from evalution.results.store import FileStore
from evalution.runs.play import play_try
from evalution.runs.runner import Runs
from evalution.suite.cases import CaseError, parse_case
from evalution.suite.library import (
    KINDS,
    PLACEHOLDERS,
    LibraryError,
    adapt,
    dummy_values,
    fit,
    load_templates,
    parse_template,
)
from evalution.targets import Targets
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionFiles
from evalution.writer.tools import AgentTurn

LIBRARY = Path(__file__).resolve().parents[2] / "library"
STATEFUL = {"reset", "state", "sections", "isolation", "conversation_break"}
CHECKS = frozenset({"checkpoint", "memory_contains", "memory_lacks", "file"})
PROFILE = """# What the app does
Plans trips for families [trace]
Keeps long-term memory of the person's preferences [code: docs/memory.md]
Long chats are compacted into a checkpoint [trace]
"""


def _values(names: tuple[str, ...]) -> dict:
    values = dummy_values(names)
    for name in names:
        if PLACEHOLDERS[name][0] == "profile":
            values[name] = {"value": "trip planning", "evidence": "plans trips for families"}
    return values


def test_every_template_loads_and_fills_into_a_case() -> None:
    templates = load_templates(LIBRARY)
    assert len(templates) >= 10
    for template in templates:
        filled = adapt(template, _values(template.placeholders), PROFILE)
        case = parse_case(filled["case"], template.id)
        assert case.source == f"library: {template.id}"
        assert "{{" not in json.dumps(filled["case"])
        assert {e["placeholder"] for e in filled["evidence"]} == {
            n for n in template.placeholders if PLACEHOLDERS[n][0] == "profile"
        }


def test_a_template_must_declare_what_its_case_uses() -> None:
    case = {"name": "x", "turns": [{"message": "{{fact}}"}, {"break": "conversation"}]}
    meta = {"title": "t", "checks": "c", "why": "w", "est_turns": 2}
    with pytest.raises(LibraryError, match="also uses conversation_break"):
        parse_template({**meta, "needs": [], "case": case}, "t")
    with pytest.raises(LibraryError, match="no placeholder"):
        parse_template({**meta, "needs": [], "case": {"name": "{{nope}}", "turns": []}}, "t")
    parse_template({**meta, "needs": ["conversation_break"], "case": case}, "t")


def test_fit_verdicts() -> None:
    by_id = {t.id: t for t in load_templates(LIBRARY)}
    forget = by_id["forget-on-request"]
    assert fit(forget, STATEFUL, CHECKS, True, PROFILE)["verdict"] == "fits"
    http = fit(forget, {"isolation", "parallel"}, None, True, PROFILE)
    assert http["verdict"] == "needs" and http["label"] == "Needs state checks"
    assert http["missing"] == ["state", "conversation_break"]
    older = fit(forget, STATEFUL, frozenset({"memory_contains"}), True, PROFILE)
    assert older["missing"] == ["check:memory_lacks"]
    assert fit(forget, STATEFUL, CHECKS, False, PROFILE)["missing"] == ["traces"]
    assert fit(forget, STATEFUL, CHECKS, True, "")["label"] == "Needs a profile"
    stateless = fit(forget, STATEFUL, CHECKS, True, "Answers billing questions [trace]")
    assert stateless == {
        "verdict": "not_applicable",
        "label": "Not for this agent",
        "missing": ["memory"],
        "reason": "Its profile doesn't mention memory.",
    }
    plain = by_id["withdrawn-instruction"]
    assert fit(plain, set(), None, False, "")["verdict"] == "fits"


def test_adapt_needs_evidence_from_the_profile() -> None:
    template = next(t for t in load_templates(LIBRARY) if t.id == "withdrawn-instruction")
    values = _values(template.placeholders)
    values["ask"] = {"value": "Plan a weekend in Porto", "evidence": "keeps a diary"}
    values["seconds"] = 5
    with pytest.raises(LibraryError) as caught:
        adapt(template, values, PROFILE)
    assert "ask: evidence must quote a line" in str(caught.value)
    assert "seconds: a whole number from 30" in str(caught.value)


class FakeTarget:
    name = "fake"

    def __init__(self, conversation_break: bool) -> None:
        self.capabilities = Capabilities(Isolation.NONE, "", conversation_break=conversation_break)
        self.sent: list[str] = []

    async def send(self, session, message, *, files=(), stop_after=None, deadline):
        self.sent.append(message)
        return Turn(reply=f"re: {message}", started=0.0, ended=0.0)

    async def break_conversation(self, session: Session) -> None:
        self.sent.append("<break>")
        session.trace_value = "thread-2"


CASE = {
    "name": "Remembers",
    "turns": [{"message": "I'm Ana"}, {"break": "conversation"}, {"message": "Who am I?"}],
}


async def test_a_break_turn_starts_a_new_conversation(tmp_path) -> None:
    case = parse_case(CASE, "x")
    assert [t.is_message for t in case.turns] == [True, False, True]
    target = FakeTarget(True)
    session = Session("s", "session.id", "thread-1")
    played = await play_try(target, session, case, tmp_path, filler=None, source=None)
    assert played.error is None and target.sent == ["I'm Ana", "<break>", "Who am I?"]
    assert played.earlier_values == ["thread-1"] and played.reply_text == "re: Who am I?"

    plain = FakeTarget(False)
    played = await play_try(plain, session, case, tmp_path, filler=None, source=None)
    assert played.error == "this target can't start a new conversation (no conversation_break)"
    assert plain.sent == ["I'm Ana"]
    with pytest.raises(CaseError, match="a break turn is only"):
        parse_case({"name": "x", "turns": [{"break": "person"}]}, "x")


async def test_the_agent_adapts_only_what_fits(tmp_path) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "library_dir": str(LIBRARY),
                "targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}},
            }
        )
    )
    (tmp_path / "sets" / "bot" / "main").mkdir(parents=True)
    (tmp_path / "sets" / "bot" / "main" / "set.json").write_text(json.dumps({"name": "Main"}))
    config = load(tmp_path / "evalution.yaml")
    agent = Agent(
        Runs(Targets(config), FileStore(config.data_dir), None), SessionFiles(tmp_path), None
    )
    opened = await agent.create("bot", "main", None)
    turn = AgentTurn(await agent._session(opened["id"]))
    rows = {r["id"]: r["fit"]["label"] for r in await agent.tools.list_library(turn, {})}
    assert rows["preference-across-conversations"] == "Needs new conversations"
    assert rows["long-chat-keeps-name-role"] == "Fits"
    assert rows["withdrawn-instruction"] == "Fits"
    refused = await agent.tools.adapt_template(turn, {"id": "forget-on-request"})
    assert refused.startswith("Refused: Needs state checks.")
    # The owner's words go in as they are; the time limit comes from what messages take.
    words = {
        "test_name": "Gives the total",
        "prompt": "What's the total of order 7?",
        "exact": "4,828.13",
        "outcome": "Gives the total",
        "must_not": "making up a figure",
    }
    made = await agent.tools.adapt_template(turn, {"id": "exact-answer", "values": words})
    assert made["case"]["reply"]["contains"] == ["4,828.13"]
    assert made["case"]["limits"]["seconds"] == 60
    typed = {**words, "seconds": 900}
    made = await agent.tools.adapt_template(turn, {"id": "exact-answer", "values": typed})
    assert made["case"]["limits"]["seconds"] == 900, "control: a limit asked for stays"
    shown = await agent.tools.adapt_template(turn, {"id": "withdrawn-instruction"})
    assert set(shown["fill"]) == {"preference", "ask", "filler_topic", "effect", "seconds"}


def test_the_kinds_ask_for_the_owners_words_and_fit_what_the_app_can_do() -> None:
    kinds = {t.kind: t for t in load_templates(LIBRARY) if t.kind}
    assert sorted(kinds) == sorted(KINDS)
    for template in kinds.values():
        asked = [a["name"] for a in template.asks()]
        assert {"prompt", "outcome", "must_not"} <= set(asked)

    plain = ({"isolation", "parallel"}, None, True, PROFILE, frozenset({"conversation"}))
    assert fit(kinds["exact"], *plain)["verdict"] == "fits"
    assert kinds["exact"].case["reply"]["contains"] == ["{{exact}}"]
    assert fit(kinds["hard_input"], *plain)["verdict"] == "fits"
    # One slip is what an attack test is for; a bad input gets the usual room to vary.
    assert kinds["attack"].case["must_hold"] is True and "must_hold" not in kinds["hard_input"].case
    assert fit(kinds["tools"], *plain)["verdict"] == "fits"
    assert "results" in kinds["tools"].case["tools"]
    assert fit(kinds["tools"], {"isolation"}, None, False, PROFILE)["missing"] == ["traces"]

    waits = fit(kinds["delegation"], *plain)
    assert waits["verdict"] == "needs" and waits["label"] == "Needs waiting on background work"
    able = ({"isolation", "background"}, None, True, PROFILE, frozenset())
    assert fit(kinds["delegation"], *able)["verdict"] == "fits", "control: it declares it"

    memory = kinds["memory"]
    assert memory.case["needs_clean"] == ["memory"] and memory.state_keys == ("memory_contains",)
    lacking = fit(memory, *plain)
    assert lacking["missing"][:2] == ["state", "conversation_break"]
    assert "clean:memory" in lacking["missing"]
    assert "Needs a clean start of memory" in lacking["reason"]
    full = (STATEFUL, CHECKS, True, PROFILE, frozenset({"memory", "conversation"}))
    assert fit(memory, *full)["verdict"] == "fits"
