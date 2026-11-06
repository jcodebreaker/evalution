"""Saving a case takes an approved plan: "Add to tests", or approve() after the admin said so."""

import json
import re
import subprocess

import httpx
import pytest
import yaml

from evalution import llm
from evalution.code import Code
from evalution.config import ModelConfig, load
from evalution.judging.rubric import INSTRUCTIONS, SCEPTIC_INSTRUCTIONS
from evalution.project import filememory
from evalution.project.profile import update_connection
from evalution.runs.estimate import suggest_seconds
from evalution.runs.runner import Runs
from evalution.suite.cases import CaseError, parse_case
from evalution.suite.store import case_text, load_case, save_set, write_case_text
from evalution.targets import Targets
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
                }
            ],
        }
    ],
    "priorities": [],
    "starters": [],
}


def _git(folder, *args) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def _learning(tmp_path, kind: str = "anthropic", skip: frozenset = frozenset()):
    """An app with code, and a model: the light one summarizes what it is sent, the strong one
    plays the script, ``(tool, arguments)`` or a closing text per call. The light model never
    summarizes a path in *skip*, and says app.py defines ``greet_people``."""
    repo = tmp_path / "app"
    for rel, text in {
        "app.py": "def greet(): ...\n",
        "lib/memory.py": "STORE = {}\n",
        "notes.txt": "x\n",
    }.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    block = {
        "adapter": "http",
        "http": {"send": {"url": "x"}},
        "code": {"path": "app", "include": ["**/*.py"]},
    }
    sent: dict[str, list] = {"light": [], "writer": []}
    script: list = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = body["system"] if kind == "anthropic" else body["messages"][0]["content"]
        if system == filememory.INSTRUCTIONS:
            sent["light"].append(body)
            text = json.dumps(body["messages"])
            paths = re.findall(r"\[Data: (.+?)\. Material", text)
            defines = {"app.py": " Defines: `greet_people`."}
            said = json.dumps(
                {
                    "files": [
                        {"path": p, "summary": f"Is: {p} summarized.{defines.get(p, '')}"}
                        for p in paths
                        if p not in skip
                    ]
                }
            )
        else:
            sent["writer"].append(body)
            said = script.pop(0)
        if kind != "anthropic":
            return httpx.Response(200, json={"choices": [{"message": {"content": said}}]})
        blocks = (
            [{"type": "text", "text": said}]
            if isinstance(said, str)
            else [
                {
                    "type": "tool_use",
                    "id": f"t{len(sent['writer'])}",
                    "name": said[0],
                    "input": said[1],
                }
            ]
        )
        usage = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}
        return httpx.Response(200, json={"content": blocks, "usage": usage, "model": "m"})

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


async def test_learning_again_reads_only_what_changed(tmp_path, monkeypatch) -> None:
    agent, repo, sent, script = _learning(tmp_path)
    targets = agent.targets
    opened = await agent.create("bot", "main")
    sid = opened["id"]

    # The first learn: every included file summarized by the light model, in one call, and all of
    # them handed to the writer, which saves. What the summaries name and the understanding does
    # not is said back once, and only once.
    script += [("write_understanding", UNDERSTOOD), "Learned.", "Greeting covers it."]
    shown = await _learn(agent, sid)
    assert len(sent["light"]) == 1
    memory = filememory.load(targets, "bot")
    files = targets.code("bot").hashes()
    assert set(files) == {"app.py", "lib/memory.py"}, "include is honoured"
    assert {p: e["fingerprint"] for p, e in memory["summaries"].items()} == files
    first = sent["writer"][0]
    said = first["messages"][0]["content"][-1]["text"]
    assert "- memory.py: Is: lib/memory.py summarized." in said and "notes.txt" not in said
    reminded = sent["writer"][2]["messages"][-1]["content"][-1]["text"]
    assert reminded.startswith(learn.LEFT_OUT) and "app.py: greet_people" in reminded
    assert len(sent["writer"]) == 3 and shown["messages"][-1]["text"] == "Greeting covers it."
    # The writer's prefix is marked cacheable; the light call is not.
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(sent["light"])
    usage = shown["usage"][-1]
    assert usage["learn"] and usage["calls"] == 4 and usage["light_calls"] == 1
    assert usage["cache_read"] == 400 and usage["chars_in"] > len(said)

    # Nothing moved: the commit and a clean tree say so, with no file hashed and no model.
    def no_hashing(_self):
        raise AssertionError("hashed")

    monkeypatch.setattr(Code, "hashes", no_hashing)
    shown = await _learn(agent, sid)
    monkeypatch.undo()
    commit = targets.code("bot").commit()
    assert shown["messages"][-1]["text"] == f"Nothing changed since {commit[:7]}."
    assert shown["usage"][-1]["calls"] == 0 and len(sent["light"]) == 1 and len(sent["writer"]) == 3

    # One file changed: it alone is summarized again and named; a second read of it is not resent.
    (repo / "lib/memory.py").write_text("STORE = {'kept': True}\n")
    _git(repo, "commit", "-qam", "keep")
    script += [
        ("read_code", {"path": "lib/memory.py"}),
        ("read_code", {"path": "lib/memory.py"}),
        ("write_understanding", UNDERSTOOD),
        "Learned again.",
        "Still covered.",
    ]
    shown = await _learn(agent, sid)
    assert len(sent["light"]) == 2 and "lib/memory.py" in json.dumps(sent["light"][1])
    assert "app.py" not in json.dumps(sent["light"][1])
    # A learn starts from its brief alone: nothing of the thread's earlier turns is sent.
    started = sent["writer"][3]["messages"]
    assert len(started) == 1 and "Learned." not in json.dumps(started)
    said = started[0]["content"][-1]["text"]
    assert '"changed": ["lib/memory.py"]' in said and "It greets." in said
    results = [
        b["content"]
        for b in sent["writer"][5]["messages"][-1]["content"]
        if b["type"] == "tool_result"
    ]
    assert results[-1].startswith("lib/memory.py is unchanged since you read it at step")
    assert shown["usage"][-1]["light_calls"] == 1
    assert filememory.load(targets, "bot")["files"] == targets.code("bot").hashes()
    # The thread keeps what was asked and said, and none of a brief.
    thread = (await agent._session(sid))["history"]
    assert [m["content"] for m in thread if m["role"] == "user"] == ["Learn bot again."] * 3
    assert thread[-1]["content"] == "Learned again.\nStill covered."


async def test_a_file_left_unsummarized_is_tried_again_then_named(tmp_path) -> None:
    agent, _repo, sent, script = _learning(tmp_path, skip=frozenset({"lib/memory.py"}))
    opened = await agent.create("bot", "main")
    script += ["Nothing to save."]
    await _learn(agent, opened["id"])
    # The batch, then what it left out on its own: two calls, and the file kept as failed.
    assert len(sent["light"]) == 2 and "app.py" not in json.dumps(sent["light"][1]["messages"])
    failed = filememory.load(agent.targets, "bot")["failed"]
    assert failed["lib/memory.py"]["why"] == "left out of the answer"
    said = sent["writer"][0]["messages"][0]["content"][-1]["text"]
    assert "- memory.py (not summarized: left out of the answer)" in said


async def test_an_openai_compatible_model_gets_no_cache_marks(tmp_path) -> None:
    agent, _repo, sent, script = _learning(tmp_path, "openai")
    opened = await agent.create("bot", "main")
    script += ["Nothing to save."]
    await _learn(agent, opened["id"])
    assert sent["writer"] and "cache_control" not in json.dumps(sent["writer"])
