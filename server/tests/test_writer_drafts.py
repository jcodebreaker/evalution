"""Asked for a test, the test writer drafts it first: its instructions say to fill gaps with
stated assumptions instead of collecting the admin's answers, they know the page's checks
(``page.shows``, ``page.hides``, ``page.looks``), and with no page map yet its reply still carries
the plan, with one offer to learn the page.

Whether a case's ``page`` block reads (its elements and questions) is :mod:`evalution.suite.cases`'
to decide, so only the tool schema and the instructions are checked for the new fields here; the
loop below uses a ``page.contains`` check, which every case reads today."""

from typing import Any

import httpx
import yaml

from evalution import llm
from evalution.config import ModelConfig, load, save_page_target
from evalution.runs.runner import Runs
from evalution.storage import Database
from evalution.suite.cases import UI_NEEDS_PAGE
from evalution.suite.store import save_set
from evalution.targets import Targets
from evalution.writer import paging
from evalution.writer.agent import Agent
from evalution.writer.prompts import INSTRUCTIONS
from evalution.writer.schemas import TOOLS
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import AgentTurn, save_plan

ASK = (
    "create a test to check when we create a page html about acme does it spin a sub agent and "
    "show a thumbs up in the ui"
)
#: The writer's draft for :data:`ASK`, as its instructions now direct.
TEST = {
    "name": "An HTML page about Acme starts a sub-agent and shows a thumbs-up",
    "turns": [{"message": "Create an HTML page about Acme."}],
    "tools": {"order": "any_order", "extras": True, "expected": [{"name": "spawn_agent"}]},
    "page": {"contains": ["Acme"]},
}
PLAN = {
    "steps": [
        {"kind": "message", "title": "Asks for an HTML page about Acme"},
        {"kind": "check", "title": "Starts a sub-agent", "note": "a spawn_agent call"},
        {"kind": "check", "title": "Shows a thumbs-up under the reply"},
    ],
    "tries": 3,
    "case": "acme-page-sub-agent",
    "file": TEST,
}
SAID = (
    "Here's a draft. The page checks wait for the page to be learned.\n\n"
    "Assumed:\n- sub-agent = a spawn_agent call\n"
    "- thumbs-up = button named 'Thumbs up' under the reply"
)


def _model(script: list[Any]) -> llm.LLM:
    """A model whose every writer step is the next of *script*."""
    count = [0]

    def answer(request: httpx.Request) -> httpx.Response:
        said = script.pop(0)
        count[0] += 1
        blocks = (
            [{"type": "text", "text": said}]
            if isinstance(said, str)
            else [{"type": "tool_use", "id": f"t{count[0]}", "name": said[0], "input": said[1]}]
        )
        usage = {"input_tokens": 10, "output_tokens": 5}
        return httpx.Response(200, json={"content": blocks, "usage": usage, "model": "m"})

    config = ModelConfig(
        kind="anthropic", base_url="http://model", api_key_env=None, models={"strong": "m"}
    )
    return llm.LLM(config, transport=httpx.MockTransport(answer))


def _agent(tmp_path, model: llm.LLM | None = None, page: dict | None = None) -> Agent:
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"web_dir": "none"}))
    docs = Database.open(tmp_path / "data").docs()
    block: dict[str, Any] = {"adapter": "http", "http": {"send": {"url": "x"}}}
    if page is not None:
        block["page"] = page
    save_page_target(docs, "bot", block)
    runs = Runs(Targets(load(tmp_path / "evalution.yaml")), model)
    save_set(runs.targets.docs, "bot", "main", {"name": "Main"})
    return Agent(runs, SessionStore(runs.targets.docs), model)


def test_the_instructions_draft_first_and_know_the_pages_checks() -> None:
    said = " ".join(INSTRUCTIONS.split())
    assert "you need three things" not in said
    assert "Ask in one short message for whichever is missing" not in said
    assert "draft it at once: your first reply is the whole plan" in said
    assert 'a short list headed "Assumed:"' in said
    assert "ask one question, no more" in said
    for key in ('"shows"', '"hides"', '"looks"', '"within": "reply"'):
        assert key in said
    assert "never say a UI check can't be made" in said
    assert "pass learn_page: true to reply" in said and "Never refuse or shrink a test" in said
    assert "the plan, the Assumed list and at most two sentences" in said


def test_the_plan_tool_describes_shows_hides_and_looks() -> None:
    reply = next(t for t in TOOLS if t["name"] == "reply")["parameters"]["properties"]
    page = reply["plan"]["properties"]["file"]["properties"]["page"]
    assert set(page["properties"]) == {"shows", "hides", "looks"}
    element = page["properties"]["shows"]["items"]["properties"]
    assert set(element) == {"role", "name", "test_id", "text", "within", "count", "turn"}
    assert element["within"]["enum"] == ["reply", "page"]
    assert page["properties"]["hides"]["items"] is page["properties"]["shows"]["items"]
    looks = page["properties"]["looks"]["items"]
    assert set(looks["properties"]) == {"question", "turn"} and looks["required"] == ["question"]
    assert reply["learn_page"] == {"type": "boolean"}


async def test_with_no_page_map_the_first_reply_is_a_plan_and_one_offer_to_learn_it(
    tmp_path,
) -> None:
    script: list[Any] = [("reply", {"text": SAID, "plan": PLAN}), "Waiting for the admin."]
    agent = _agent(tmp_path, _model(script))
    sid = (await agent.create("bot", "main"))["id"]
    await agent.message(sid, ASK)
    await agent._tasks[sid]
    session = await agent._session(sid)

    said = [m for m in session["messages"] if m["role"] == "agent"]
    assert len(said) == 1, "the first reply is the draft, not questions"
    first = said[0]
    assert [s["title"] for s in first["plan"]["steps"]][1] == "Starts a sub-agent"
    assert "Assumed:" in first["text"] and "?" not in first["text"]
    offer = first["page_offer"]
    assert offer["target"] == "bot" and offer["offer"] is True
    # The copy isn't set up here, so the offer says what comes first, as the page's does.
    assert offer["can_learn"] is False and offer["why_not"]
    shown = next(m["content"] for m in session["history"] if m["role"] == "tool")
    assert "Shown to the admin as plan" in shown and "isn't learned yet" in shown


async def test_learn_page_asked_offers_it_and_a_learned_page_offers_nothing(tmp_path) -> None:
    agent = _agent(tmp_path)
    session = await agent._session((await agent.create("bot", "main"))["id"])
    plain = {**PLAN, "file": {k: v for k, v in TEST.items() if k != "page"}}
    await agent.tools.reply(AgentTurn(session), {"text": "Draft.", "plan": plain})
    assert "page_offer" not in session["messages"][-1], "no page checks, not asked"
    await agent.tools.reply(AgentTurn(session), {"text": "Learn it?", "learn_page": True})
    assert session["messages"][-1]["page_offer"]["target"] == "bot"
    told = await agent.tools.read_profile(AgentTurn(session), {})
    assert "The page: not learned yet" in told


async def test_a_learned_page_gives_its_words_and_no_offer(tmp_path) -> None:
    page = {
        "url": "{copy_page}/chat",
        "ready": {"role": "textbox", "name": "Message"},
        "open_session": {"url": "{copy_page}/chat/{session}"},
        "actions": {
            "type": {"fill": {"role": "textbox", "name": "Message"}},
            "send": {"click": {"role": "button", "name": "Send"}},
        },
        "replies": {"in": {"role": "log", "name": "Messages"}},
    }
    agent = _agent(tmp_path, page=page)
    assert paging.learn_offer(agent.targets, "bot") is None
    session = await agent._session((await agent.create("bot", "main"))["id"])
    await agent.tools.reply(AgentTurn(session), {"text": "Draft.", "plan": PLAN})
    assert "page_offer" not in session["messages"][-1]
    told = await agent.tools.read_profile(AgentTurn(session), {})
    assert "The page: learned." in told and "log named" in told


async def test_a_draft_with_ui_checks_is_saved_with_no_page_map_and_its_card_says_them(
    tmp_path,
) -> None:
    """Draft first, learn after: the writer saves a test with UI checks though the app has no page
    map yet, under the same rule as the API; only a test saying it plays through the API alone
    (``plays: api``) is refused."""
    agent = _agent(tmp_path)
    session = await agent._session((await agent.create("bot", "main"))["id"])
    thumbs = {"role": "button", "name": "Thumbs up"}
    test = {**TEST, "page": {"shows": [thumbs]}}
    await agent.tools.reply(AgentTurn(session), {"text": "Draft.", "plan": {**PLAN, "file": test}})
    card = session["messages"][-1]
    assert card["plan"]["page"] == {"shows": [thumbs], "hides": [], "looks": []}
    assert card["page_offer"]["target"] == "bot"

    plan = {"file": test, "replaces": None}
    saved = await save_plan(agent.targets, session, "p1", plan, None)
    assert saved == {"case": plan["saved"], "set": "main"}
    api_only = {"file": {**test, "plays": "api"}, "replaces": None}
    refused = await save_plan(agent.targets, session, "p2", api_only, None)
    assert isinstance(refused, str) and refused.startswith(f"Refused: {UI_NEEDS_PAGE}")
    assert "saved" not in api_only
