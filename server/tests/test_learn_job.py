"""A learn is a background job (docs/plans/next-level.md C10), and a first learn is the facts
level alone, the areas read when something asks for them (C1). The models are scripted, as in
``test_agent_approval.py``: the made-up app there is three areas the app uses (root, agents,
lib) and one it doesn't (web)."""

import asyncio
import contextlib
import json

import pytest
import yaml
from fastapi.testclient import TestClient
from test_agent_approval import FIRST_ARGS, _area_runs, _learn, _learning

from evalution.api.app import create_app
from evalution.config import ConfigError, load
from evalution.project import filememory, learning, understanding
from evalution.writer import agent as agent_module
from evalution.writer import areas as area_agents
from evalution.writer import learn
from evalution.writer.agent import Agent
from evalution.writer.tools import AgentTurn


def _full(tmp_path) -> None:
    """The target sets ``code.learn: full``."""
    path = tmp_path / "evalution.yaml"
    config = yaml.safe_load(path.read_text())
    config["targets"]["bot"]["code"]["learn"] = "full"
    path.write_text(yaml.safe_dump(config))


# C1: facts first, areas when needed


async def test_a_first_learn_runs_no_area_agent_and_saves_the_card_the_diagram_and_the_tools(
    tmp_path,
) -> None:
    agent, _repo, sent, _script = _learning(tmp_path)
    targets = agent.targets
    shown = await _learn(agent, everything=False)
    assert sent["areas"] == [] and sent["overview"] == [] and sent["docs"] == []
    assert len(sent["first"]) == 1, "one light call, for the card's line"
    usage = shown["usage"][-1]
    assert usage["calls"] == 1 and usage["areas_run"] == 0 and usage["facts_calls"] == 1
    said = shown["messages"][-1]["text"]
    assert said.startswith("I've learned bot from its code.") and learn.ON_DEMAND in said

    doc = understanding.load(targets, "bot")
    assert doc["one_line"] == FIRST_ARGS["one_line"]
    assert doc["overview"] == {"state": "facts", "basis": "the code"}
    nodes = {n["id"]: n for n in doc["nodes"]}
    assert {"person", "agent", "tools"} <= set(nodes)
    assert "greet_people" in nodes["tools"]["tools"] and "search" in nodes["tools"]["tools"]
    assert any(n["kind"] == "state" for n in doc["nodes"]), "its store"
    claims = {c["id"]: c["text"] for s in doc["sections"] for c in s["claims"]}
    assert "greet_people" in claims["facts-tools"]
    assert any("MemoryStore" in t for t in claims.values())
    # No area was learned: none has a section, and each the app uses waits for a need.
    assert not any(s["id"].startswith(understanding.AREA_PREFIX) for s in doc["sections"])
    view = understanding.view(targets, agent.tools.runs, "bot")
    assert view["card"]["one_line"] == FIRST_ARGS["one_line"]
    assert view["card"]["numbers"]["tools"] >= 2
    assert view["overview"]["state"] == "facts" and view["diagram"]["edges"]
    progress = understanding.progress(targets, "bot")
    assert progress["state"] == "done" and progress["overview"] == "facts"
    states = {a["id"]: a["state"] for a in progress["areas"]}
    assert states == {"root": "due", "agents": "due", "lib": "due", "web": "listed"}
    # The profile the writer reads names the tools.
    assert "greet_people" in understanding.writer_profile(targets, "bot")

    # "Learn again" with nothing moved answers for free.
    shown = await _learn(agent, everything=False)
    assert shown["messages"][-1]["text"].startswith("Nothing changed since")
    assert shown["usage"][-1]["calls"] == 0 and len(sent["first"]) == 1

    # "Learn everything" learns the areas, and only then is the overview written.
    shown = await _learn(agent)
    assert sorted(_area_runs(shown)) == ["agents", "lib", "root"]
    assert len(sent["overview"]) == 1 and len(sent["first"]) == 1
    assert understanding.load(targets, "bot")["overview"]["state"] == "final"


async def test_the_writer_asking_for_an_area_learns_exactly_that_area(tmp_path) -> None:
    agent, _repo, sent, _script = _learning(tmp_path)
    await _learn(agent, everything=False)
    assert sent["areas"] == []
    session = await agent.create("bot", "main")
    turn = AgentTurn(await agent.raw(session["id"]))
    # A tool's name: the area holding it is learned, and waited for.
    said = await agent.tools.read_understanding(turn, {"area": "greet_people"})
    assert len(sent["areas"]) == 1 and "The area root:" in json.dumps(sent["areas"][0])
    assert "The root area" in said and "root works" in said
    job = agent.learns.get("bot")
    assert job.state == "done" and job.areas == ["root"] and job.by == "writer"
    states = {a["id"]: a["state"] for a in understanding.progress(agent.targets, "bot")["areas"]}
    assert states["root"] == "done" and states["lib"] == "due" and states["agents"] == "due"
    # The overview is written now that a section is in.
    assert len(sent["overview"]) == 1
    # Asked again, it is current: no learn, no call.
    said = await agent.tools.read_understanding(turn, {"area": "root"})
    assert len(sent["areas"]) == 1 and "root works" in said
    # A file path names its area too; one that names nothing says which there are.
    await agent.tools.read_understanding(turn, {"area": "lib/memory.py"})
    assert len(sent["areas"]) == 2 and "The area lib:" in json.dumps(sent["areas"][1])
    with pytest.raises(LookupError, match="the areas are: root"):
        await agent.tools.read_understanding(turn, {"area": "nowhere"})


async def test_code_learn_full_learns_every_area_at_most_so_many_a_go(
    tmp_path, monkeypatch
) -> None:
    agent, _repo, sent, _script = _learning(tmp_path)
    _full(tmp_path)
    agent.targets.config = load(tmp_path / "evalution.yaml")
    monkeypatch.setattr(learn, "MAX_LEARNED", 2)
    shown = await _learn(agent, everything=False)
    assert len(_area_runs(shown)) == 2
    assert "1 more area not learned yet (at most 2 in one go)" in shown["messages"][-1]["text"]
    shown = await _learn(agent, everything=False)
    assert len(_area_runs(shown)) == 1 and len(sent["areas"]) == 3


def test_code_learn_is_on_demand_or_full(tmp_path) -> None:
    block = {"adapter": "http", "http": {"send": {"url": "x"}}, "code": {"path": "app"}}
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
    code = load(tmp_path / "evalution.yaml").targets["bot"].code
    assert code is not None and code.learn == "on_demand"
    block["code"]["learn"] = "sometimes"
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
    with pytest.raises(ConfigError, match=r"code\.learn"):
        load(tmp_path / "evalution.yaml")


# C10: a learn is a job, not a chat turn


async def test_a_learn_job_outlives_its_chat_and_the_chats_clock(tmp_path, monkeypatch) -> None:
    agent, _repo, _sent, _script = _learning(tmp_path)
    monkeypatch.setattr(agent_module, "MAX_SECONDS", 0.05)
    real = area_agents.run_area

    async def slow(area, ctx, before, budget=None, shown=None):
        await asyncio.sleep(0.1)  # past the chat's limit, several times over
        return await real(area, ctx, before, budget, shown)

    monkeypatch.setattr(area_agents, "run_area", slow)
    _full(tmp_path)
    agent.targets.config = load(tmp_path / "evalution.yaml")
    sid = (await agent.create("bot", "main"))["id"]
    # The chat asks: it answers one line at once and does not wait.
    chat = await agent.message(sid, "Learn bot again.", learn=True)
    assert chat["messages"][-1]["text"] == "Learning bot. Follow it on What it learned."
    assert chat["status"] == "idle" and not agent.working(sid)
    assert agent.learning("bot") and agent.learns.get("bot").by == "chat"
    # Asked again while it goes: the same job.
    again = agent.start_learn("bot")
    assert again is agent.learns.get("bot")
    # The chat is stopped and reset: the learn goes on.
    await agent.stop(sid)
    sid = (await agent.create("bot", "main"))["id"]
    job = await agent.learns.wait("bot")
    assert job.state == "done" and not job.interrupted
    assert job.reply.startswith("I've learned bot.") and "go on" not in job.reply.lower()
    assert len(_area_runs(agent_module.public(job.session))) == 3
    # Its usage is on its own session, not the chat's.
    assert not (await agent.get(sid))["usage"]
    view = learning.read(agent.targets, "bot").view()
    assert view["state"] == "done" and view["reply"] == job.reply and view["ended"]


async def test_a_restart_marks_a_learn_interrupted_and_go_on_learns_only_the_rest(
    tmp_path, monkeypatch
) -> None:
    agent, _repo, sent, _script = _learning(tmp_path)
    real = area_agents.run_area
    started: list[str] = []
    cut = asyncio.Event()

    async def cut_at_the_third(area, ctx, before, budget=None, shown=None):
        started.append(area.id)
        if len(started) == 3:
            cut.set()
            await asyncio.sleep(60)  # the server stops here
        return await real(area, ctx, before, budget, shown)

    monkeypatch.setattr(area_agents, "run_area", cut_at_the_third)
    monkeypatch.setattr(learn, "PARALLEL", 1)
    agent.start_learn("bot", everything=True)
    await asyncio.wait_for(cut.wait(), 10)
    await asyncio.sleep(0.2)  # the second area's section is being kept
    kept = learning.read(agent.targets, "bot")
    assert kept.state == "running" and kept.progress["stage"] == "areas"

    # Another server on the same data: the record is read as cut, its area in flight waiting.
    after = Agent(agent.runs, agent.sessions, agent.llm)
    assert after.learns.interrupted() == ["bot"]
    job = after.learns.get("bot")
    assert job.state == "stopped" and job.interrupted and job.everything
    assert {a["id"]: a["state"] for a in job.progress["areas"]}["lib"] == "waiting"
    task = agent.learns._tasks["bot"]
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert sorted(filememory.load(agent.targets, "bot")["areas"]) == ["agents", "root"]

    # "Go on": a new job asking what the cut one asked, which learns only the rest.
    monkeypatch.undo()
    before = len(sent["areas"])
    after.start_learn("bot", job.areas, everything=job.everything)
    done = await after.learns.wait("bot")
    assert done.state == "done" and not done.interrupted
    assert _area_runs(agent_module.public(done.session)) == ["lib"]
    assert len(sent["areas"]) == before + 1


def test_the_learn_routes_start_follow_and_stop_a_job(tmp_path) -> None:
    agent, _repo, _sent, _script = _learning(tmp_path)

    async def cut() -> None:
        # A job a server left running.
        learning.keep(agent.targets, learning.Job("bot", everything=True))

    asyncio.run(cut())
    config = load(tmp_path / "evalution.yaml")
    with TestClient(create_app(config)) as client:
        # The server starting marks it interrupted: the page offers "Go on".
        found = client.get("/v1/targets/bot/learn").json()
        assert found["state"] == "stopped" and found["interrupted"] is True
        assert found["everything"] is True and found["error"] == learning.INTERRUPTED
        # Starting one needs a model, which this server has none of.
        refused = client.post("/v1/targets/bot/learn", json={})
        assert refused.status_code == 409 and "No model" in refused.json()["detail"]
        assert client.post("/v1/targets/bot/learn", json={"areas": "root"}).status_code == 400
        assert client.post("/v1/targets/bot/learn", json={"force": "yes"}).status_code == 400
        stopped = client.post("/v1/targets/bot/learn/stop")
        assert stopped.status_code == 200 and stopped.json()["state"] == "stopped"


def test_a_target_never_learned_has_no_job(tmp_path) -> None:
    _learning(tmp_path)
    config = load(tmp_path / "evalution.yaml")
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/targets/bot/learn").json() is None
