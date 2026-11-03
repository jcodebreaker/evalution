"""The writer's prompt, built end to end. A shadowed name in ``Agent._instructions`` once got past
a merge because nothing ran it, and the loop turns any error there into a chat message, so this
checks the model was really called with it."""

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


async def test_the_run_a_question_is_about_goes_beside_the_words(make_rig) -> None:
    rig = make_rig()
    model = WriterModel()
    agent = writer.Agent(rig.runs, SessionFiles(rig.config.data_dir), model)
    opened = await agent.create(TARGET, SET)
    shown = await agent.message(opened["id"], "About Run 2: why?", "20261102-013042-c33e")
    for task in list(agent._tasks.values()):
        await task
    assert shown["messages"][0]["text"] == "About Run 2: why?"
    assert "20261102-013042-c33e" in model.heard[-1]
