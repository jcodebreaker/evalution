"""The writer's prompt, built end to end. A shadowed name in ``Agent._instructions`` once got past
a merge because nothing ran it, and the loop turns any error there into a chat message, so this
checks the model was really called with it."""

import asyncio

from scripted_agent import SET, TARGET

from evalution.core.case import CASE_SUFFIX
from evalution.llm import Reply
from evalution.project.profile import update_connection, write_json, write_profile
from evalution.writer import agent as writer
from evalution.writer.prompts import INSTRUCTIONS
from evalution.writer.sessions import SessionFiles


class WriterModel:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.heard: list[str] = []

    async def chat(self, key, system, messages, tools=(), max_tokens=0) -> Reply:
        self.calls.append(system)
        self.heard += [str(m["content"]) for m in messages if m["role"] == "user"]
        return Reply(text="Nothing to do.")


async def test_the_writer_builds_its_prompt_and_calls_the_model(make_rig, monkeypatch) -> None:
    rig = make_rig()
    rig.add({"name": "Greets", "turns": [{"message": "hi"}]})
    (rig.set_root / SET / f"broken{CASE_SUFFIX}").write_text("{not a case")
    write_profile(rig.targets, TARGET, "Plans trips for families [admin]")
    write_json(rig.targets, TARGET, "drift.json", {"changes": [{"id": "c1"}]})

    model = WriterModel()
    agent = writer.Agent(rig.runs, SessionFiles(rig.config.data_dir), model)
    opened = await agent.create(TARGET, SET)
    shown = await agent.message(opened["id"], "What can you test?")
    for task in list(agent._tasks.values()):
        await task

    said = (await agent.get(shown["id"]))["messages"]
    assert [m["text"] for m in said if m["role"] == "agent"] == ["Nothing to do."]
    (prompt,) = model.calls
    assert prompt.startswith(INSTRUCTIONS)
    assert f"target is {TARGET!r} (adapter 'scripted_agent'" in prompt
    assert "capabilities: conversation_break, isolation, reset, sections, state" in prompt
    assert "a try starts clean of" in prompt
    assert "asks every test to start clean" not in prompt, "by default it asks for nothing"
    assert "Spent $0.00" in prompt and "Drift: 1 change(s)" in prompt
    assert "what the person sends, what should happen, and what must not happen" in " ".join(
        prompt.split()
    )
    assert "Plans trips for families" in prompt
    assert '"name": "Greets"' in prompt
    assert "broken: does not load" in prompt

    update_connection(rig.targets, TARGET, required_clean=["memory"])
    asking = agent._instructions(await agent._session(shown["id"]))
    assert "asks every test to start clean of memory" in asking

    # Past the room for them, the cases go by name.
    monkeypatch.setattr(writer, "CASES_CHARS", 10)
    shorter = agent._instructions(await agent._session(shown["id"]))
    assert "greets: Greets" in shorter and '"name": "Greets"' not in shorter


async def test_a_turn_always_stops_and_a_plain_question_is_heard(make_rig, monkeypatch) -> None:
    rig = make_rig()
    model = WriterModel()
    agent = writer.Agent(rig.runs, SessionFiles(rig.config.data_dir), model)
    opened = await agent.create(TARGET, SET)
    shown = await agent.message(opened["id"], "About Run 2: why?", "20261102-013042-c33e")
    for task in list(agent._tasks.values()):
        await task
    assert shown["messages"][0]["text"] == "About Run 2: why?"
    assert "20261102-013042-c33e" in model.heard[-1]
    assert model.calls and agent_said(await agent._session(opened["id"])) == "Nothing to do."

    class Replies:
        """Whatever *answer* gives."""

        def __init__(self, answer) -> None:
            self.answer = answer

        async def chat(self, key, system, messages, tools=(), max_tokens=0) -> Reply:
            return await self.answer()

    async def played(answer) -> dict:
        agent = writer.Agent(rig.runs, SessionFiles(rig.config.data_dir), Replies(answer))
        opened = await agent.create(TARGET, SET)
        await agent.message(opened["id"], "Go")
        await asyncio.wait_for(asyncio.gather(*agent._tasks.values()), 5)
        return await agent._session(opened["id"])

    def ran(session: dict) -> int:
        return sum(m["role"] == "tool" for m in session["history"])

    # One reply asking for far too many calls runs only up to the limit.
    monkeypatch.setattr(writer, "MAX_STEPS", 5)

    async def many() -> Reply:
        calls = [{"id": f"c{n}", "name": "no_such", "arguments": {"n": n}} for n in range(50)]
        return Reply(text="", tool_calls=calls)

    session = await played(many)
    assert ran(session) == 5 and session["status"] == "idle"
    assert agent_said(session) == "I stopped after 5 steps. Say go on."

    # The same call again and again stops before the third runs, across replies.
    monkeypatch.setattr(writer, "MAX_STEPS", 40)
    made = 0

    async def same() -> Reply:
        nonlocal made
        made += 1
        return Reply(text="", tool_calls=[{"id": f"c{made}", "name": "no_such", "arguments": {}}])

    session = await played(same)
    assert ran(session) == 2 and made == 3 and "repeating" in agent_said(session)

    # Control: different calls aren't a repeat, and go on to the step limit.
    made = 0

    async def different() -> Reply:
        nonlocal made
        made += 1
        return Reply(
            text="", tool_calls=[{"id": f"c{made}", "name": "no_such", "arguments": {"n": made}}]
        )

    session = await played(different)
    assert ran(session) == 40 and "40 steps" in agent_said(session)

    # A model that never answers is given up on, and the session can go on.
    monkeypatch.setattr(writer, "MAX_SECONDS", 0.05)

    async def never() -> Reply:
        await asyncio.sleep(60)
        raise AssertionError

    session = await played(never)
    assert session["status"] == "idle" and "minutes" in agent_said(session)


def agent_said(session: dict) -> str:
    return [m for m in session["messages"] if m["role"] == "agent"][-1]["text"]


def test_a_long_chat_loses_its_oldest_exchanges_and_says_so(monkeypatch) -> None:
    monkeypatch.setattr(writer, "HISTORY_CHARS", 300)
    history = [
        message
        for n in range(6)
        for message in (
            {"role": "user", "content": f"ask {n}"},
            {"role": "assistant", "content": "x" * 100, "tool_calls": []},
        )
    ]
    writer._trim(history)
    assert history[0]["content"] == writer.EARLIER_CUT
    assert history[-2]["content"] == "ask 5", "the newest exchange stays"
    assert [m["role"] for m in history[:2]] == ["user", "user"]
    kept = len(history)
    history += [{"role": "user", "content": "ask 6"}, {"role": "assistant", "content": "x" * 100}]
    writer._trim(history)
    assert sum(m["content"] == writer.EARLIER_CUT for m in history) == 1
    assert len(history) <= kept + 2
    # Control: under the limit, nothing is touched.
    short = [{"role": "user", "content": "hi"}]
    writer._trim(short)
    assert short == [{"role": "user", "content": "hi"}]
