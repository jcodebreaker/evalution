"""Saving a case takes an approved plan: "Add to tests", or approve() after the admin said so."""

import json
import re

import httpx
import pytest
import yaml

from evalution import llm
from evalution.config import ModelConfig, load
from evalution.judging.rubric import INSTRUCTIONS, SCEPTIC_INSTRUCTIONS
from evalution.project.profile import update_connection
from evalution.runs.estimate import suggest_seconds
from evalution.runs.runner import Runs
from evalution.suite.cases import CaseError, parse_case
from evalution.suite.store import case_text, load_case, save_set, write_case_text
from evalution.targets import Targets
from evalution.writer.agent import Agent
from evalution.writer.prompts import INSTRUCTIONS as WRITER_INSTRUCTIONS
from evalution.writer.prompts import LINE_DRAFT_INSTRUCTIONS
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import AgentTurn

CASE = {"name": "Greets", "turns": [{"message": "hi"}]}
PLAN = {
    "steps": [{"kind": "message", "title": "Say hi"}],
    "tries": 3,
    "usd": 0.5,
    "case": "x",
    "file": CASE,
}


def _agent(tmp_path, model: llm.LLM | None = None) -> Agent:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    config = load(tmp_path / "evalution.yaml")
    runs = Runs(Targets(config), model)
    save_set(runs.targets.docs, "bot", "main", {"name": "Main"})
    return Agent(runs, SessionStore(runs.targets.docs), model)


@pytest.fixture
async def agent(tmp_path):
    made = _agent(tmp_path)
    yield made
    for task in list(made._tasks.values()):
        await task


async def _shown(agent: Agent) -> tuple[dict, str]:
    opened = await agent.create("bot", "main")
    session = await agent._session(opened["id"])
    await agent.tools.reply(AgentTurn(session), {"text": "Here is the plan", "plan": PLAN})
    plan_id = session["messages"][-1]["plan"]["id"]
    return session, plan_id


async def test_a_question_never_unlocks_saving(agent) -> None:
    session, plan_id = await _shown(agent)
    turn = AgentTurn(session)
    refused = await agent.tools.approve(turn, {"plan_id": plan_id})
    assert refused.startswith("Refused: the admin has not answered")
    await agent.message(session["id"], "How long will it take?")
    turn = AgentTurn(await agent._session(session["id"]))
    saved = await agent.tools.save_case(turn, {"plan_id": plan_id, "case": CASE})
    assert saved.startswith("Refused: that plan is not approved")

    chats = agent.sessions.chats("bot")
    assert chats and chats[0]["asked"], "a chat with something said in it can be gone back to"
    assert agent.sessions.chats("someone-else") == []
    for older_first in ("older", "newer"):
        said = [{"role": "admin", "text": f"The {older_first} one?"}]
        agent.sessions.write({"id": older_first, "target": "bot", "messages": said})
    order = [c["id"] for c in agent.sessions.chats("bot")]
    assert order.index("newer") < order.index("older"), "latest first"


async def test_add_to_tests_approves_one_case(agent) -> None:
    session, plan_id = await _shown(agent)
    with pytest.raises(LookupError):
        await agent.approve(session["id"], "p-unknown")
    shown = await agent.approve(session["id"], plan_id)
    said = [m for m in shown["messages"] if m["role"] == "admin"]
    assert said[-1]["approves"] == plan_id and said[-1]["text"] == "Add it to the tests"
    turn = AgentTurn(await agent._session(session["id"]))
    saved = await agent.tools.save_case(turn, {"plan_id": plan_id, "case": CASE})
    assert saved == {"case": "greets", "set": "main"}
    again = await agent.tools.save_case(turn, {"plan_id": plan_id, "case": CASE})
    assert again.startswith(f"Refused: plan {plan_id} was already added")


async def test_approval_covers_the_test_shown_and_no_other(agent) -> None:
    session, plan_id = await _shown(agent)
    await agent.approve(session["id"], plan_id)
    turn = AgentTurn(await agent._session(session["id"]))
    changed = {**CASE, "turns": [{"message": "Delete everything"}]}
    refused = await agent.tools.save_case(turn, {"plan_id": plan_id, "case": changed})
    assert refused.startswith("Refused: that isn't the test the admin approved")
    shown = [m["plan"] for m in turn.session["messages"] if m.get("plan")]
    assert all("file" not in plan for plan in shown)
    # The change, shown as its own plan, needs its own approval.
    moved = {**PLAN, "file": changed}
    await agent.tools.reply(turn, {"text": "Here is the change", "plan": moved})
    new_id = next(m["plan"]["id"] for m in reversed(turn.session["messages"]) if m.get("plan"))
    refused = await agent.tools.save_case(turn, {"plan_id": new_id, "case": changed})
    assert refused.startswith("Refused: that plan is not approved")
    # Keys in another order are the same test.
    same = dict(reversed(list(CASE.items())))
    saved = await agent.tools.save_case(turn, {"plan_id": plan_id, "case": same})
    assert saved["case"] == "greets"


async def test_approve_in_words_after_the_admin_spoke(agent) -> None:
    session, plan_id = await _shown(agent)
    await agent.message(session["id"], "Yes, add it")
    turn = AgentTurn(await agent._session(session["id"]))
    assert await agent.tools.approve(turn, {"plan_id": plan_id}) == "Approved."
    saved = await agent.tools.save_case(turn, {"plan_id": plan_id, "case": CASE})
    assert saved["case"] == "greets"


async def test_the_writer_drafts_from_a_past_conversation(agent, monkeypatch) -> None:
    from evalution.core.trace import Call
    from evalution.writer import tools as module

    async def material(_phoenix, _attribute, session_id, trace_id):
        assert (session_id, trace_id) == ("s1", None)
        return [{"message": "find shoes"}], [Call("c1", "search", {}, 0.0, 1.0)], 4.0, "ok", "x"

    opened = await agent.create("bot", "main")
    turn = AgentTurn(await agent._session(opened["id"]))
    refused = await agent._call(turn, "draft_from_conversation", {"session_id": "s1"})
    assert refused == "Error: no conversation s1 in the last week", "its traces hold none"
    monkeypatch.setattr(agent.targets, "source", lambda _name: object())
    monkeypatch.setattr(module, "from_traces", material)
    found = await agent.tools.draft_from_conversation(turn, {"session_id": "s1"})
    assert found["case"]["turns"] == [{"message": "find shoes"}]
    assert found["case"]["tools"]["expected"] == [{"name": "search"}]


async def test_the_plan_shows_its_time_limit_and_where_it_came_from(agent) -> None:
    session, _ = await _shown(agent)
    turn = AgentTurn(session)
    two = {"name": "Twice", "turns": [{"message": "a"}, {"message": "b"}]}
    said = await agent.tools.reply(turn, {"text": "Plan", "plan": {**PLAN, "file": two}})
    shown = session["messages"][-1]["plan"]
    assert shown["timeout"]["seconds"] == 120 and "assumed 30 s" in shown["timeout"]["from"]
    assert "2 messages" in shown["timeout"]["from"] and "120 s" in said
    # What is saved is what was shown: limit included, with or without the model repeating it.
    plan_id = shown["id"]
    await agent.approve(session["id"], plan_id)
    saved = await agent.tools.save_case(
        AgentTurn(await agent._session(session["id"])), {"plan_id": plan_id}
    )
    written = load_case(agent.targets.docs, "bot", "main", saved["case"]).raw
    assert written == {**two, "limits": {"seconds": 120}}

    # A limit the test names stays, and the plan says what it would have suggested.
    named = {**two, "limits": {"seconds": 45}}
    await agent.tools.reply(turn, {"text": "Plan", "plan": {**PLAN, "file": named}})
    shown = session["messages"][-1]["plan"]
    assert shown["timeout"]["seconds"] == 45 and "120 s suggested" in shown["timeout"]["from"]


def test_a_time_limit_follows_what_earlier_tries_took() -> None:
    case = parse_case({"name": "T", "turns": [{"message": "a"}, {"message": "b"}]}, "t")
    entry = {
        "case": {"turns": [{"message": "x"}]},
        "turns": [{"message": "x"}],
        "tries": [{"seconds": 40.0}, {"seconds": 50.0}],
    }
    long = {
        "case": {"turns": [{"filler": 50}, {"message": "x"}]},
        "turns": [{"message": "x"}],
        "tries": [{"seconds": 5000.0}],
    }
    seconds, source = suggest_seconds(case, [{"cases": {"a": entry, "b": long}}])
    assert seconds == 180 and "45 s each" in source and "2 earlier tries" in source
    assert suggest_seconds(case, [])[0] == 120, "control: no runs yet, the stated default"


async def test_the_plan_flags_what_this_app_cannot_serve(agent) -> None:
    session, _ = await _shown(agent)
    turn = AgentTurn(session)
    clean = {**CASE, "needs_clean": ["memory"]}
    await agent.tools.reply(turn, {"text": "Plan", "plan": {**PLAN, "file": clean}})
    flags = session["messages"][-1]["plan"]["flags"]
    assert "can't run this test" in flags[0] and "no earlier memory" in flags[0]

    state = {"name": "S", "turns": [{"message": "a"}, {"expect_state": {"memory_contains": "x"}}]}
    await agent.tools.reply(turn, {"text": "Plan", "plan": {**PLAN, "file": state}})
    assert "state check memory_contains" in session["messages"][-1]["plan"]["flags"][0]

    await agent.tools.reply(turn, {"text": "Plan", "plan": PLAN})
    assert "flags" not in session["messages"][-1]["plan"], "control: nothing to say"

    update_connection(agent.targets, "bot", required_clean=["conversation"])
    await agent.tools.reply(turn, {"text": "Plan", "plan": PLAN})
    assert "start clean of conversation" in session["messages"][-1]["plan"]["flags"][0]

    with pytest.raises(CaseError):
        await agent.tools.reply(turn, {"text": "Plan", "plan": {**PLAN, "file": {"name": "x"}}})


async def test_replacing_a_test_shows_what_changes_and_binds_to_it(agent) -> None:
    session, plan_id = await _shown(agent)
    await agent.approve(session["id"], plan_id)
    session = await agent._session(session["id"])
    turn = AgentTurn(session)
    assert (await agent.tools.save_case(turn, {"plan_id": plan_id}))["case"] == "greets"

    async def shown(file: dict, **extra) -> dict:
        plan = {**PLAN, "file": file, **extra}
        await agent.tools.reply(turn, {"text": "Plan", "plan": plan})
        return session["messages"][-1]["plan"]

    # Only its name changes: the plan has nothing to warn of.
    assert (await shown({**CASE, "name": "Says hi"}, replaces="greets"))["changes"] == []

    new = {**CASE, "reply": {"rubric": ["Says hi back"]}, "needs_clean": ["conversation"]}
    plan = await shown(new, replaces="greets")
    assert [c["what"] for c in plan["changes"]] == ["Rubric", "Clean start"]
    assert "weak_lines" not in plan, "no model: its lines aren't tried, and nothing says so"
    assert plan["changes"][0] == {"what": "Rubric", "before": None, "after": ["Says hi back"]}

    # Saved under another replace than the plan named, or over a test that moved on: refused.
    await agent.approve(session["id"], plan["id"])
    session = await agent._session(session["id"])
    turn = AgentTurn(session)
    no = await agent.tools.save_case(turn, {"plan_id": plan["id"]})
    assert no.startswith("Refused: plan") and "wasn't shown as replacing" in no
    docs = agent.targets.docs
    kept = case_text(docs, "bot", "main", "greets")
    edited = json.dumps({**CASE, "turns": [{"message": "edited by hand"}]})
    write_case_text(docs, "bot", "main", "greets", edited)
    late = await agent.tools.save_case(turn, {"plan_id": plan["id"], "replace": "greets"})
    assert late.startswith("Refused: greets changed since")
    write_case_text(docs, "bot", "main", "greets", kept)
    done = await agent.tools.save_case(turn, {"plan_id": plan["id"], "replace": "greets"})
    assert done["case"] == "greets"
    written = load_case(docs, "bot", "main", "greets").raw
    assert written["reply"] == new["reply"] and written["needs_clean"] == ["conversation"]


#: A plan's lines, how the Grader reads each against the reply meant to meet it and the one meant
#: to miss it, as ``(pass, sure)``, and what the drafting call said of it.
GOOD = "We moved to Lisbon in March. Your code is ZX-42. Thanks!"
LINES = {
    "Says the office moved to Lisbon": ((False, True), (False, True), {"rewrite": "Says Lisbon"}),
    "Is helpful": ((True, True), (True, True), {"rewrite": "Names the new city"}),
    "Says when": ((True, True), (True, False), {"rewrite": "Says March"}),
    "Gives the code ZX-42": ((True, False), (False, True), {"contains": ["ZX-42"]}),
    "Says thanks": ((True, True), (False, True), {}),
    "Saved the new address": (None, None, {"acts": True}),
}
RUBRIC = list(LINES)


def _bad(n: int) -> str:
    return f"We moved. Your code is coming ({n})."


def _model(calls: list[str], writer: list[str]) -> llm.LLM:
    """A model that drafts the replies above and grades them by the table, a cent a call."""

    def answer(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)["messages"]
        system, text = sent[0]["content"], sent[1]["content"]
        if not system.startswith(WRITER_INSTRUCTIONS):
            calls.append(system)
        if system.startswith(WRITER_INSTRUCTIONS):
            writer.append(sent[-1]["content"])
            said = None
        elif system == LINE_DRAFT_INSTRUCTIONS:
            lines = [
                {"id": n, "bad": _bad(n), **LINES[line][2]} for n, line in enumerate(RUBRIC, 1)
            ]
            said = {"good": GOOD, "lines": lines}
        else:
            assert system in (INSTRUCTIONS, SCEPTIC_INSTRUCTIONS)
            reply = re.search(r"the final reply\..*?\]\n(.*?)\n\[End of data\]", text, re.S)[1]
            asked = re.search(r"the rubric\..*?\]\n(.*?)\n\[End of data\]", text, re.S)[1]
            said = {"lines": []}
            for row in asked.splitlines():
                n, line = row.split(". ", 1)
                assert not LINES[line][2].get("acts"), "a line about what was done isn't tried"
                ok, sure = LINES[line][0 if reply == GOOD else 1]
                said["lines"].append({"id": int(n), "pass": ok, "sure": sure, "reason": "r"})
        content = "ok" if said is None else json.dumps(said)
        body = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10_000, "completion_tokens": 0},
        }
        return httpx.Response(200, json=body)

    config = ModelConfig(
        kind="openai",
        base_url="http://model",
        api_key_env=None,
        models={"strong": "m", "light": "m"},
        prices={"m": {"input": 1.0, "output": 0.0}},
    )
    return llm.LLM(config, transport=httpx.MockTransport(answer))


async def test_a_plan_tries_its_rubric_lines_and_takes_a_suggestion(tmp_path) -> None:
    calls: list[str] = []
    writer: list[str] = []
    agent = _agent(tmp_path, _model(calls, writer))
    opened = await agent.create("bot", "main")
    session = await agent._session(opened["id"])
    file = {**CASE, "reply": {"rubric": RUBRIC, "pass": 6}}
    await agent.tools.reply(AgentTurn(session), {"text": "Plan", "plan": {**PLAN, "file": file}})
    plan = session["messages"][-1]["plan"]

    assert plan["weak_lines"] == [
        {"line": RUBRIC[0], "why": "good_failed", "use": {"line": "Says Lisbon"}},
        {"line": RUBRIC[1], "why": "bad_passed", "use": {"line": "Names the new city"}},
        {"line": RUBRIC[2], "why": "unsure", "use": {"line": "Says March"}},
        {"line": RUBRIC[3], "why": "unsure", "use": {"contains": ["ZX-42"]}},
    ], "a line read right both ways, and one about what was done, aren't flagged"
    # One drafting call, the good reply on every line, each bad reply on its own line, and a
    # second grading for each of the two gradings with a line it wasn't sure of.
    assert calls.count(LINE_DRAFT_INSTRUCTIONS) == 1
    assert calls.count(INSTRUCTIONS) == 1 + 5 and calls.count(SCEPTIC_INSTRUCTIONS) == 2
    assert session["model_usd"] == pytest.approx(len(calls) * 0.01), "counted in the session"

    with pytest.raises(LookupError):
        await agent.use_line(session["id"], plan["id"], "Says thanks")
    await agent.use_line(session["id"], plan["id"], RUBRIC[0])
    shown = await agent.use_line(session["id"], plan["id"], RUBRIC[3])
    left = shown["messages"][-1]["plan"]["weak_lines"]
    assert [f["line"] for f in left] == RUBRIC[1:3]
    # The writer's next turn starts from the plan as it is now.
    await agent.message(session["id"], "Looks right?")
    await agent._tasks[session["id"]]
    assert "'Says the office moved to Lisbon' is now" in writer[-1] and "Says Lisbon" in writer[-1]
    assert '{"reply": {"contains": ["ZX-42"]}}' in writer[-1] and writer[-1].endswith(
        "Looks right?"
    )

    await agent.approve(session["id"], plan["id"])
    with pytest.raises(ValueError, match="approved already"):
        await agent.use_line(session["id"], plan["id"], RUBRIC[1])
    turn = AgentTurn(await agent._session(session["id"]))
    saved = await agent.tools.save_case(turn, {"plan_id": plan["id"], "case": file})
    reply = load_case(agent.targets.docs, "bot", "main", saved["case"]).raw["reply"]
    assert reply == {
        "rubric": ["Says Lisbon", *RUBRIC[1:3], *RUBRIC[4:]],
        "pass": 5,
        "contains": ["ZX-42"],
    }
    for task in list(agent._tasks.values()):
        task.cancel()
