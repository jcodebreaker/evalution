"""Saving a case takes an approved plan: "Add to tests", or approve() after the admin said so."""

import asyncio
import json
import re
import subprocess
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
import yaml

from evalution import llm
from evalution.code import Code
from evalution.config import ModelConfig, load
from evalution.judging.rubric import INSTRUCTIONS, SCEPTIC_INSTRUCTIONS
from evalution.project import (
    areas,
    codemap,
    details,
    drift,
    filememory,
    knowledge,
    modules,
    slicing,
    understanding,
)
from evalution.project.profile import update_connection
from evalution.runs.estimate import suggest_seconds
from evalution.runs.runner import Runs
from evalution.suite.cases import CaseError, parse_case
from evalution.suite.store import case_text, load_case, save_set, write_case_text
from evalution.targets import Targets
from evalution.writer import agent as agent_module
from evalution.writer import areas as area_agents
from evalution.writer import learn
from evalution.writer.agent import Agent
from evalution.writer.prompts import INSTRUCTIONS as WRITER_INSTRUCTIONS
from evalution.writer.prompts import LINE_DRAFT_INSTRUCTIONS
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import AgentTurn

CASE = {"name": "Greets", "turns": [{"message": "hi"}]}
PLAN = {
    "steps": [{"kind": "message", "title": "Say hi"}],
    "tries": 3,
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


UNDERSTOOD = {
    "one_line": "A bot that greets.",
    "nodes": [
        {
            "id": "agent",
            "kind": "agent",
            "title": "The agent",
            "evidence": [{"kind": "code", "ref": "app.py"}],
        }
    ],
    "sections": [
        {
            "id": "can_do",
            "title": "Can do",
            "claims": [
                {
                    "id": "greets",
                    "text": "It greets.",
                    "evidence": [{"kind": "code", "ref": "app.py"}],
                },
                {
                    "id": "keeps",
                    "text": "It keeps a store.",
                    "evidence": [{"kind": "code", "ref": "lib/memory.py"}],
                },
            ],
        }
    ],
    "priorities": [],
    "starters": [],
}
#: A tiny app in three languages and an agent's instructions: what the code map is tried on.
APP = {
    "app.py": (
        "from lib.memory import MemoryStore\n\n"
        'SYSTEM_PROMPT = "You greet people."\n\n\n'
        "@tool\ndef greet_people(name):\n    return MemoryStore().remember(name)\n\n\n"
        '@app.post("/chat")\ndef chat(message):\n'
        "    return greet_people(message) + SYSTEM_PROMPT\n\n\n"
        "def _wrap(x):\n    return x\n\n\n"
        "PREFIX_BUDGET_SECONDS = 30\n\n\ndef build_instructions():\n    return SYSTEM_PROMPT\n"
    ),
    "lib/memory.py": "class MemoryStore:\n    def remember(self, name):\n        return name\n",
    "web/chat.ts": (
        "export function sendChat(text: string) {\n  return fetchReply(text);\n}\n"
        "function fetchReply(text: string) {\n  return text;\n}\n"
        'const MODE = process.env.CHAT_MODE ?? "local";\n'
    ),
    "agents/planner.md": "# Planner\nPlans the day.\n",
    "agents/worker.yaml": "model: small\nmax_steps: 4\ntools:\n  - search\n",
    "notes.txt": "x\n",
}


#: Prose of the app: its docs and a decision record.
DOCS = {
    "docs/guide.md": "# Guide\nHow the bot greets.\n",
    "docs/adr/0001-greet.md": "# Greet first\nThe bot greets before anything.\n",
}


def _git(folder, *args) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def _area_answer(body: dict, fail: frozenset, odd: Mapping[str, str]) -> Any:
    """An area's agent: its section names the area and cites its first file, with a node of its
    own. An area in *fail* never writes; one *odd* says ``cut`` writes a first answer cut at the
    output limit, ``parts`` writes its section in two parts."""
    first = body["messages"][0]["content"]
    first = first if isinstance(first, str) else first[-1]["text"]
    area = re.search(r"The area (\S+):", first)[1]
    if area.split("~")[0] in fail:
        return "I can't tell."
    if len(body["messages"]) > 1 and odd.get(area) not in ("cut", "cut-many"):
        return "Done."
    files = first.split("[Data: its files", 1)[1].split("\n", 1)[1]
    path = re.search(r"^(\S+) \(", files, re.M)[1]
    evidence = [{"kind": "code", "ref": path}]
    section = {
        "title": f"The {area} area",
        "claims": [{"id": "does", "text": f"{area} works.", "evidence": evidence}],
        "nodes": [{"id": "part", "kind": "agent", "title": area, "evidence": evidence}],
    }
    if odd.get(area) == "cut" and len(body["messages"]) == 1:
        return ("cut", "write_section", section)
    if odd.get(area) == "cut-many" and len(body["messages"]) == 1:
        # Two parts in one answer, the second cut short at the output limit.
        cut_short = {"title": section["title"], "claims": [{"id": "half", "text": "Cut sh"}]}
        return ("cut", [("write_section", {**section, "more": True}), ("write_section", cut_short)])
    if odd.get(area) == "cut-many":
        if len(body["messages"]) > 3:
            return "Done."
        second = {"id": "rest", "text": f"{area} also keeps.", "evidence": evidence}
        return ("write_section", {"title": section["title"], "claims": [second]})
    if odd.get(area) == "parts":
        second = {"id": "also", "text": f"{area} also keeps.", "evidence": evidence}
        return [
            ("write_section", {**section, "more": True}),
            ("write_section", {"title": section["title"], "claims": [second]}),
        ]
    return ("write_section", section)


OVERVIEW_ARGS = {
    "one_line": "A bot that greets.",
    "claims": [
        {"id": "greets", "text": "It greets.", "evidence": [{"kind": "code", "ref": "app.py"}]}
    ],
}


def _learning(
    tmp_path,
    kind: str = "anthropic",
    skip: frozenset = frozenset(),
    summarize: float | None = None,
    fail: frozenset = frozenset(),
    odd: Mapping[str, str] | None = None,
    docs: bool = False,
):
    """:data:`APP` in a repo, and a model: the light one summarizes the files, modules and app it
    is sent (never a path in *skip*); an area's agent writes a section naming its area (never one
    in *fail*), the overview its one line; the writer in a chat plays the script, ``(tool,
    arguments)`` or a closing text per call."""
    repo = tmp_path / "app"
    for rel, text in APP.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    if docs:
        for rel, text in DOCS.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "docs")
    code = {"path": "app", "include": ["**/*.py", "**/*.ts", "agents/**", "docs/**"]}
    if summarize is not None:
        code["summarize"] = summarize
    block = {"adapter": "http", "http": {"send": {"url": "x"}}, "code": code}
    sent: dict[str, list] = {
        "light": [],
        "modules": [],
        "app": [],
        "areas": [],
        "overview": [],
        "docs": [],
        "writer": [],
    }
    script: list = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = body["system"] if kind == "anthropic" else body["messages"][0]["content"]
        text = json.dumps(body["messages"])
        if system == filememory.INSTRUCTIONS:
            sent["light"].append(body)
            content = body["messages"][-1]["content"]
            content = content if isinstance(content, str) else content[-1]["text"]
            shown = re.findall(
                r"\[Data: (.+?)\. Material.*?\]\n(.*?)\n\[End of data\]", content, re.S
            )
            files = [
                {
                    "path": p,
                    "summary": f"Is: {p}, {len(t)} characters.",
                    "facts": [{"fact": f"It holds {len(t)} characters.", "line": 1}],
                }
                for p, t in shown
                if p not in skip
            ]
            said = json.dumps({"files": files})
        elif system == modules.INSTRUCTIONS:
            sent["modules"].append(body)
            folders = re.findall(r"\[Data: module (.+?)\. Material", text)
            said = json.dumps(
                {"modules": [{"folder": f, "summary": f"{f} does."} for f in folders]}
            )
        elif system == modules.APP_INSTRUCTIONS:
            sent["app"].append(body)
            said = json.dumps({"summary": "The app greets."})
        elif system == area_agents.INSTRUCTIONS:
            sent["areas"].append(body)
            said = _area_answer(body, fail, odd or {})
        elif system == learn.DOCS:
            sent["docs"].append(body)
            said = json.dumps({"summary": "The docs say it greets."})
        elif system == learn.OVERVIEW:
            sent["overview"].append(body)
            said = ("write_overview", OVERVIEW_ARGS)
        else:
            sent["writer"].append(body)
            said = script.pop(0)
        if kind != "anthropic":
            return httpx.Response(200, json={"choices": [{"message": {"content": said}}]})
        # ("cut", name, input): a call that stopped at the output limit.
        cut = isinstance(said, tuple) and said[0] == "cut"
        said = (said[1] if isinstance(said[1], list) else said[1:]) if cut else said
        calls = said if isinstance(said, list) else [said]
        blocks = (
            [{"type": "text", "text": said}]
            if isinstance(said, str)
            else [
                {
                    "type": "tool_use",
                    "id": f"t{sum(len(v) for v in sent.values())}-{n}",
                    "name": call[0],
                    "input": call[1],
                }
                for n, call in enumerate(calls)
            ]
        )
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}
        reply = {"content": blocks, "usage": usage, "model": "m"}
        if cut:
            reply["stop_reason"] = "max_tokens"
        return httpx.Response(200, json=reply)

    config = ModelConfig(
        kind=kind, base_url="http://model", api_key_env=None, models={"strong": "m", "light": "l"}
    )
    model = llm.LLM(config, transport=httpx.MockTransport(answer))
    agent = _agent(tmp_path, model)
    # _agent wrote a target with no code: this one has the repo.
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
    agent.targets.config = load(tmp_path / "evalution.yaml")
    return agent, repo, sent, script


async def _learn(agent: Agent, session_id: str) -> dict:
    await agent.message(session_id, "Learn bot again.", learn=True)
    await agent._tasks[session_id]
    return await agent.get(session_id)


async def _say(agent: Agent, session_id: str, text: str) -> dict:
    """A chat message to the writer, not a learn."""
    await agent.message(session_id, text)
    await agent._tasks[session_id]
    return await agent.get(session_id)


def _first_text(body: dict) -> str:
    return body["messages"][0]["content"][-1]["text"]


def _facts_of(targets: Targets) -> dict[str, list[str]]:
    """The Details' texts by the file they cite."""
    out: dict[str, list[str]] = {}
    for c in understanding.details_of(understanding.load(targets, "bot")):
        out.setdefault(c["evidence"][0]["ref"].split(":")[0], []).append(c["text"])
    return out


def _area_runs(shown: dict) -> list[str]:
    return [a["area"] for a in shown["usage"][-1]["areas"]]


async def test_a_learn_is_split_into_areas_merged_and_kept(tmp_path, monkeypatch) -> None:
    agent, repo, sent, _script = _learning(tmp_path, summarize=0.2)
    targets = agent.targets
    sid = (await agent.create("bot", "main"))["id"]

    # The first learn: every area understood by its own agent, each call bounded, then one
    # bounded call for the overview; the merge keeps the schema, its ids under each area.
    shown = await _learn(agent, sid)
    parts = areas.partition(
        learn._sizes(
            codemap.build(targets, "bot", targets.code("bot")).map, filememory.load(targets, "bot")
        )
    )
    assert sorted(_area_runs(shown)) == sorted(a.id for a in parts)
    # One call an area: what to cover is named up front, so nothing is said back after.
    assert len(sent["areas"]) == len(parts)
    root = next(b for b in sent["areas"] if "The area root:" in json.dumps(b))
    assert "to cover" in json.dumps(root) and "greet_people" in json.dumps(root)
    assert all(b["max_tokens"] == area_agents.TOKENS for b in sent["areas"])
    assert len(sent["overview"]) == 1 and sent["overview"][0]["max_tokens"] == learn.OVERVIEW_TOKENS
    assert not sent["writer"], "no single giant write"
    doc = understanding.load(targets, "bot")
    assert doc["one_line"] == "A bot that greets."
    ids = [s["id"] for s in doc["sections"]]
    assert ids[0] == "what_it_is" and ids[-1] == understanding.DETAILS
    first = next(s for s in doc["sections"] if s["id"].startswith(understanding.AREA_PREFIX))
    prefix = area_agents.namespace(first["id"].removeprefix(understanding.AREA_PREFIX))
    assert first["claims"][0]["id"] == f"{prefix}.does"
    assert len({n["id"] for n in doc["nodes"]}) == len(parts), "no two areas clash"
    assert shown["messages"][-1]["text"] == "I've learned bot. Check what I understood."
    usage = shown["usage"][-1]
    assert usage["areas_total"] == len(parts) == usage["areas_run"] and usage["overview_calls"] == 1
    assert all(a["outcome"] == "learned" and a["calls"] == 1 for a in usage["areas"])
    # An area reads its own files' facts; the assembled Details reaches no model.
    assert "It holds" not in json.dumps(sent["overview"])
    assert understanding.DETAILS not in json.dumps([sent["areas"], sent["overview"]])
    links = knowledge.load(targets, "bot")["links"]
    assert f"claim:{first['claims'][0]['id']}" in links, "an area's claims are linked to code"

    # Nothing moved: no file hashed, no session read and no model.
    def never(*_args, **_kwargs):
        raise AssertionError("read")

    monkeypatch.setattr(Code, "hashes", never)
    monkeypatch.setattr(drift, "take", never)
    shown = await _learn(agent, sid)
    monkeypatch.undo()
    assert shown["messages"][-1]["text"].startswith("Nothing changed since")
    assert shown["usage"][-1]["calls"] == 0

    # One file changed: its area alone is understood again; its section came out the same, so the
    # overview stands.
    (repo / "lib/memory.py").write_text(
        "class MemoryStore:\n    def remember(self, name):\n        return name.strip()\n"
    )
    _git(repo, "commit", "-qam", "keep")
    before = len(sent["areas"])
    shown = await _learn(agent, sid)
    lib = next(a.id for a in parts if "lib/memory.py" in a.files)
    assert _area_runs(shown) == [lib] and len(sent["areas"]) - before <= 2
    assert len(sent["overview"]) == 1 and shown["usage"][-1]["overview_calls"] == 0
    assert understanding.load(targets, "bot")["sections"][:-1] == doc["sections"][:-1]


def test_the_areas_are_the_same_for_the_same_files() -> None:
    sizes = {f"pkg{n}/mod{m}/f{k}.py": 900 for n in range(3) for m in range(4) for k in range(3)}
    sizes |= {"main.py": 500, "README.md": 300}
    parts = areas.partition(sizes)
    assert areas.MIN_AREAS <= len(parts) <= areas.MAX_AREAS
    assert sorted(f for a in parts for f in a.files) == sorted(sizes), "each file in one area"
    assert areas.partition(dict(reversed(list(sizes.items())))) == parts, "order makes no area"
    edited = areas.partition({**sizes, "pkg0/mod0/f0.py": 30_000})
    assert [a.files for a in edited] == [a.files for a in parts], "an edit moves no file"
    assert [a.id for a in edited] == [a.id for a in parts]
    # A folder that shapes nothing joins its neighbour; one that does stays an area of its own.
    shaping = {p for p in sizes if not p.startswith("pkg2/")}
    folded = areas.partition(sizes, shaping)
    assert len(folded) < len(parts)
    assert any(len(a.folders) > 1 and any(f.startswith("pkg2") for f in a.folders) for a in folded)
    assert areas.partition(sizes, shaping) == folded
    many = areas.partition({f"one/{n}/x{k}.py": 1 for n in range(20) for k in range(30)})
    assert len(many) == areas.MAX_AREAS


async def test_areas_run_a_few_at_a_time_and_one_failing_never_blocks(
    tmp_path, monkeypatch
) -> None:
    # The fake model answers at once: concurrency is seen through run_area itself.
    running, most = 0, 0
    real = area_agents.run_area

    async def counted(area, ctx, before, budget=None, shown=None):
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.01)
        try:
            return await real(area, ctx, before, budget, shown)
        finally:
            running -= 1

    monkeypatch.setattr(area_agents, "run_area", counted)
    agent, _repo, _sent, _script = _learning(tmp_path, summarize=0.2, fail=frozenset({"web"}))
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    assert 1 < most <= learn.PARALLEL
    # web failed: tried, tried again, then in halves (it has one file: no halves); the others
    # were learned, the reply names it, and its runs' last steps are kept.
    runs = [a for a in shown["usage"][-1]["areas"] if a["area"].startswith("web")]
    assert [r["outcome"] for r in runs] == [f"no section after {area_agents.STEPS} steps"] * 2
    assert all(r["calls"] <= area_agents.STEPS for r in runs)
    said = shown["messages"][-1]["text"]
    assert said.startswith("I've learned bot.") and "Not learned: web (no section after" in said
    assert any(f.get("area") == "web" for f in shown["failed_learns"])
    sections = understanding.load(agent.targets, "bot")["sections"]
    assert not any(s["id"] == f"{understanding.AREA_PREFIX}web" for s in sections)
    # The page's progress: every area at its end state, with what it took.
    progress = {a["id"]: a for a in shown["learn"]["areas"]}
    assert progress["web"]["state"] == "failed" and progress["web"]["calls"] > 0
    assert all(a["state"] == "learned" for k, a in progress.items() if k != "web")
    assert shown["learn"]["stage"] == "saving"


async def test_a_failing_area_is_split_in_halves(tmp_path, monkeypatch) -> None:
    agent, _repo, _sent, _script = _learning(tmp_path, summarize=0.2, fail=frozenset({"root"}))
    (tmp_path / "app" / "extra.py").write_text("def extra():\n    return 1\n")
    _git(tmp_path / "app", "add", "-A")
    _git(tmp_path / "app", "commit", "-qm", "extra")
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    runs = [a["area"] for a in shown["usage"][-1]["areas"] if a["area"].startswith("root")]
    assert runs == ["root", "root", "root~1", "root~2"]
    assert "Not learned: root (" in shown["messages"][-1]["text"]


def test_the_slice_takes_what_shapes_behaviour(tmp_path, monkeypatch) -> None:
    targets, code = _mapped(tmp_path)
    (tmp_path / "app/lib/util.py").write_text("def add(a, b):\n    return a + b\n")
    cmap = codemap.build(targets, "bot", code).map
    monkeypatch.setattr(slicing, "TOP_RANKED", 0)
    chosen = slicing.choose(cmap, {})
    # A prompt, tool and route; an agent's instructions and settings; state; a variable read.
    assert set(chosen) == {
        "app.py",
        "agents/planner.md",
        "agents/worker.yaml",
        "lib/memory.py",
        "web/chat.ts",
    }, "a plain helper and a note are left to their cards"
    assert chosen[0] == "app.py"
    assert slicing.choose(cmap, {}, 0.2) == chosen[:2], "the cap keeps the best"
    churned = slicing.choose(cmap, {"web/chat.ts": 40}, 0.2)
    assert "web/chat.ts" in churned, "what changes often comes first among them"
    monkeypatch.setattr(slicing, "TOP_RANKED", 10)
    assert "lib/util.py" in slicing.choose(cmap, {}), "the most used are read whatever they do"


def test_the_code_wins_over_what_the_docs_say() -> None:
    cmap = codemap.CodeMap(
        {
            "a.py": {
                "fingerprint": "f1",
                "defs": [],
                "refs": [],
                "facts": [
                    {
                        "kind": "constant",
                        "text": "MAX_NEEDS = 5",
                        "symbols": ["MAX_NEEDS"],
                        "line": 3,
                    }
                ],
            },
            "docs/adr/0001.md": {"fingerprint": "f2", "defs": [], "refs": [], "facts": []},
        }
    )
    drawn = [
        {"fact": "MAX_NEEDS is 3.", "symbols": ["MAX_NEEDS"], "line": 4},
        {"fact": "Replies stay short.", "symbols": [], "line": 9},
    ]
    memory = {"summaries": {"docs/adr/0001.md": {"fingerprint": "f2", "facts": drawn}}}
    claims = details.assemble(cmap, memory, ["a.py", "docs/adr/0001.md"])
    assert [c["text"] for c in claims] == ["MAX_NEEDS = 5", "The docs say: Replies stay short."]
    assert claims[1]["evidence"] == [{"kind": "docs", "ref": "docs/adr/0001.md:9"}]
    stale = {"summaries": {"docs/adr/0001.md": {"fingerprint": "old", "facts": drawn}}}
    assert len(details.assemble(cmap, stale, ["docs/adr/0001.md"])) == 0, "read of another text"


def _mapped(tmp_path) -> tuple[Targets, Code]:
    repo = tmp_path / "app"
    for rel, text in APP.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    block = {"adapter": "http", "http": {"send": {"url": "x"}}, "code": {"path": "app"}}
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
    targets = Targets(load(tmp_path / "evalution.yaml"))
    return targets, targets.code("bot")


def test_the_code_map_parses_once_ranks_behaviour_and_walks(tmp_path) -> None:
    targets, code = _mapped(tmp_path)
    made = codemap.build(targets, "bot", code)
    kinds = {d.name: d.kind for d in made.map.defs.values()}
    assert kinds["SYSTEM_PROMPT"] == "prompt" and kinds["greet_people"] == "tool"
    assert kinds["chat"] == "route" and kinds["MemoryStore"] == "state"
    assert kinds["sendChat"] == "function" and kinds["Planner"] == "prompt"
    assert kinds["PREFIX_BUDGET_SECONDS"] == "constant"
    assert kinds["build_instructions"] == "prompt-builder"
    assert made.parsed == 6
    facts = {p: [f["kind"] for f in made.map.facts(p)] for p in made.map.files}
    assert facts["app.py"] == ["prompt", "tool", "route", "constant"]
    assert facts["web/chat.ts"] == ["constant", "env"]
    assert facts["agents/worker.yaml"] == ["config", "config"]
    assert made.map.card("app.py").startswith("app.py (routes): SYSTEM_PROMPT, greet_people")
    # Kept by fingerprint: nothing parsed again until a file moves, and then that one alone.
    assert codemap.build(targets, "bot", code).parsed == 0
    (tmp_path / "app/web/chat.ts").write_text(APP["web/chat.ts"] + "export const X = 1;\n")
    again = codemap.build(targets, "bot", code)
    assert again.parsed == 1
    cmap = again.map
    # Ranked: what the app's behaviour rests on before plain helpers.
    order = [cmap.defs[k].name for k in cmap.rank()]
    assert order.index("SYSTEM_PROMPT") < order.index("fetchReply")
    assert order.index("greet_people") < order.index("_wrap")
    assert order.index("build_instructions") < order.index("_wrap")
    assert order.index("PREFIX_BUDGET_SECONDS") < order.index("fetchReply")
    assert "PREFIX_BUDGET_SECONDS = 30" in cmap.repo_map(2_000), "a constant shows its value"
    # The map fits its budget, and a larger budget holds more.
    small, large = cmap.repo_map(200), cmap.repo_map(2_000)
    assert 0 < len(small) <= 200 < len(large) <= 2_000 and "SYSTEM_PROMPT" in small
    walked = cmap.walk("greet_people", 1, code.read)
    assert "return MemoryStore().remember(name)" in walked
    assert "lib/memory.py:2 def remember(self, name):" in walked.split("used by:")[0]
    assert "app.py:11 def chat(message):" in walked.split("used by:")[1]
    assert "two steps away" in cmap.walk("greet_people", 2, code.read)
    assert cmap.walk("nothing_here", 1, code.read).startswith("(no definition")


async def test_a_file_left_unsummarized_is_tried_again_then_named(tmp_path) -> None:
    agent, _repo, sent, script = _learning(tmp_path, skip=frozenset({"app.py"}), summarize=0.4)
    opened = await agent.create("bot", "main")
    script += ["Nothing to save."]
    await _learn(agent, opened["id"])
    # The slice's batch, then what it left out on its own: two calls, the file kept as failed.
    assert len(sent["light"]) == 2
    assert re.findall(r"\[Data: (\S+)\. Material", json.dumps(sent["light"][1])) == ["app.py"]
    failed = filememory.load(agent.targets, "bot")["failed"]
    assert failed["app.py"]["why"] == "left out of the answer"


async def test_an_openai_compatible_model_gets_no_cache_marks(tmp_path) -> None:
    agent, _repo, sent, script = _learning(tmp_path, "openai")
    opened = await agent.create("bot", "main")
    script += ["Nothing to save."]
    await _say(agent, opened["id"], "What does it do?")
    assert sent["writer"] and "cache_control" not in json.dumps(sent["writer"])


async def test_a_save_cut_at_the_output_limit_is_not_run(tmp_path) -> None:
    """A write_understanding that came cut (a long understanding past the output limit) loses its
    end, its nodes first: it is not run, and the writer is told to save in two steps."""
    agent, _repo, sent, script = _learning(tmp_path, summarize=0.2)
    sid = (await agent.create("bot", "main"))["id"]
    script += [
        ("cut", "write_understanding", {"one_line": "A bot.", "sections": []}),
        ("write_understanding", UNDERSTOOD),
        "Learned.",
        "The rest is plumbing.",
    ]
    await _say(agent, sid, "Write what you understood.")
    told = json.dumps(sent["writer"][1]["messages"][-1])
    assert "cut off at the output limit" in told
    assert sent["writer"][0]["max_tokens"] == agent_module.WRITER_TOKENS
    assert understanding.load(agent.targets, "bot") is not None


async def test_a_save_refused_the_same_way_says_why(tmp_path) -> None:
    agent, _repo, _sent, script = _learning(tmp_path, summarize=0.2)
    sid = (await agent.create("bot", "main"))["id"]
    wrong = {**UNDERSTOOD, "nodes": [{**UNDERSTOOD["nodes"][0], "evidence": []}]}
    script += [("write_understanding", wrong)] * 3
    shown = await _say(agent, sid, "Write what you understood.")
    said = shown["messages"][-1]["text"]
    assert said.startswith("I stopped: I kept repeating the same step.")
    assert "It was refused: Refused, nothing saved." in said and "needs evidence" in said


async def test_prose_gets_no_area_and_is_summed_up_once(tmp_path) -> None:
    agent, _repo, sent, _script = _learning(tmp_path, summarize=0.2, docs=True)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    assert not any(a["area"].startswith("docs") for a in shown["usage"][-1]["areas"])
    assert "docs/guide.md" not in json.dumps(sent["areas"]), "no area agent reads the docs"
    assert len(sent["docs"]) == 1 and "The bot greets before anything" not in json.dumps(
        sent["docs"]
    ), "the docs' cards, not their text"
    assert "The docs say it greets." in json.dumps(sent["overview"])
    details = understanding.details_of(understanding.load(agent.targets, "bot"))
    assert details, "the docs keep their facts in Details"


async def test_a_section_comes_in_parts_and_a_cut_one_in_smaller_parts(tmp_path) -> None:
    odd = {"lib": "parts", "web": "cut"}
    agent, _repo, sent, _script = _learning(tmp_path, summarize=0.2, odd=odd)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    sections = {s["id"]: s for s in understanding.load(agent.targets, "bot")["sections"]}
    lib = sections[f"{understanding.AREA_PREFIX}lib"]
    assert [c["id"] for c in lib["claims"]] == ["lib.does", "lib.also"], "two parts, one section"
    web = [b for b in sent["areas"] if "The area web:" in json.dumps(b)]
    assert len(web) == 2, "the cut call was not run; the next was asked for smaller parts"
    told = json.dumps(web[1]["messages"][-1])
    assert "cut at the output limit" in told and "at most 7 claims" in told
    assert f"{understanding.AREA_PREFIX}web" in sections
    assert all(a["outcome"] == "learned" for a in shown["usage"][-1]["areas"])


async def test_an_area_spends_no_more_than_its_budget(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(area_agents, "BUDGET", 200)
    agent, _repo, _sent, _script = _learning(tmp_path, summarize=0.2, fail=frozenset({"web"}))
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    runs = [a for a in shown["usage"][-1]["areas"] if a["area"].startswith("web")]
    # A call costs 115 tokens: after one, what is left covers no other, so no retry follows.
    assert [r["outcome"] for r in runs] == ["out of budget"]
    assert sum(r["tokens_in"] + r["tokens_out"] for r in runs) <= 200
    assert "Not learned: web (out of budget)" in shown["messages"][-1]["text"]


async def test_a_cut_answer_keeps_its_whole_calls_and_asks_for_one_part_an_answer(tmp_path) -> None:
    """The output limit holds for a whole answer: an area that put several parts in one answer
    was cut every time however few claims each had. The whole calls before the cut are taken, and
    the next answer is asked for one part."""
    agent, _repo, sent, _script = _learning(tmp_path, summarize=0.2, odd={"web": "cut-many"})
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    web = next(
        s
        for s in understanding.load(agent.targets, "bot")["sections"]
        if s["id"] == f"{understanding.AREA_PREFIX}web"
    )
    assert [c["id"] for c in web["claims"]] == ["web.does", "web.rest"], "nothing of the cut one"
    told = json.dumps([b for b in sent["areas"] if "The area web:" in json.dumps(b)][1])
    assert "Write ONE write_section call per answer" in told
    assert all(a["outcome"] == "learned" for a in shown["usage"][-1]["areas"])
