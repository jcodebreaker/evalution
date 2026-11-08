"""The test writer's isolation tools, with a scripted model: it reads what was found, proposes
edits that must rest on evidence, rehearses only what the admin approved, revises from a failed
rehearsal's leaks, and stops after three failures to ask the admin. A proven plan is saved as a
new version, and undo goes back to the one before."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import yaml

from evalution import llm
from evalution.config import ModelConfig, load
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.runs.runner import Runs
from evalution.suite.store import save_set
from evalution.targets import Targets
from evalution.writer import isolating
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import AgentTurn

PG = "relay-evals-postgres-1"
LEAK = "volume:relay-evals_artifact-data/artifacts/memory_index/documents"
APP = {
    "deployment/entrypoint.py": 'import os\nURL = os.getenv("DATABASE_URL")\n',
    "engine/env.py": 'import os\nURL = os.environ["ATRIUM_DB_URL"]\nFALLBACK = "local.db"\n',
    "deployment/compose.yaml": (
        "services:\n"
        "  postgres:\n"
        "    image: postgres:16\n"
        "  engine:\n"
        "    image: engine\n"
        "    volumes:\n"
        "      - artifact-data:/data/artifacts\n"
        "volumes:\n"
        "  artifact-data:\n"
    ),
}


def _saved_plan() -> Plan:
    """relay-lite's plan with its memory volume missing: the database is reset, the traces
    left alone, and the memory index would leak."""
    seen = f"holds the canary: {PG}: atrium.engine.conversation_items"
    return Plan(
        "bot",
        stores=[
            PlannedStore(
                "postgres-atrium",
                "postgres",
                f"{PG}:atrium",
                moved_by=["DATABASE_URL", "ATRIUM_DB_URL"],
                strategy="relocate",
                evidence=["deployment/entrypoint.py:2", "engine/env.py:2"],
                seen=[seen],
                found_as=["env:DATABASE_URL", "env:ATRIUM_DB_URL", f"{PG}:atrium.engine"],
            )
        ],
        exclude=["atrium.phoenix.spans"],
        report="Drafted by rule from the code read and the probe: 1 store(s).",
    )


def _agent(tmp_path, model: llm.LLM | None = None, saved: bool = True) -> Agent:
    repo = tmp_path / "app"
    for rel, text in APP.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    block = {"adapter": "http", "http": {"send": {"url": "x"}}, "code": {"path": "app"}}
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
    config = load(tmp_path / "evalution.yaml")
    runs = Runs(Targets(config), model)
    save_set(runs.targets.docs, "bot", "main", {"name": "Main"})
    if saved:
        PlanStore(config.data_dir).save(_saved_plan())
    return Agent(runs, SessionStore(runs.targets.docs), model)


class FakeRehearsal:
    """``rehearse_plan`` that fails *failures* times naming the memory index, then proves."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.plans: list[Plan] = []

    async def __call__(self, config, target: str, plan: Plan, *, slot: int = 0) -> Plan:
        self.plans.append(plan)
        done = Plan.from_json(plan.to_json())
        if len(self.plans) <= self.failures:
            done.status = "failed"
            done.report = (
                f"ok  kept the starting state\nNO  probe: the canary reached {LEAK}\n"
                f"Not proven: {LEAK}"
            )
        else:
            done.status = "proven"
            done.report = "Proven: the plan resets everything the probe reached."
        return done


Step = tuple[str, dict] | str | Callable[[dict], Any]


def _model(script: list[Step], sessions: dict[str, dict]) -> llm.LLM:
    """A model whose every writer step is the next of *script*: a call, a closing text, or a
    function of the live session giving either."""
    count = [0]

    def answer(request: httpx.Request) -> httpx.Response:
        said = script.pop(0)
        if callable(said):
            said = said(next(iter(sessions.values())))
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


def _latest(session: dict) -> str:
    return isolating.state(session)["latest"]


def _show(session: dict) -> tuple[str, dict]:
    return ("reply", {"text": "Here is the change.", "isolation": _latest(session)})


def _cite_leak(session: dict) -> tuple[str, dict]:
    """Add the memory volume, citing the rehearsal's finding that names it."""
    history = session["history"]
    read = next(m for m in reversed(history) if m["role"] == "tool" and "findings" in m["content"])
    found = json.loads(read["content"].split("\n", 1)[1].rsplit("\n", 1)[0])
    leak = next(f for f in found["findings"] if "memory_index" in f["what"])
    new = {"id": "volume-artifact-data", "kind": "volume", "locator": "volume:artifact-data"}
    return ("propose_isolation_plan", {"changes": [{"op": "add", "new": new, "cite": leak["id"]}]})


def _tool_results(session: dict) -> list[str]:
    return [m["content"] for m in session["history"] if m["role"] == "tool"]


async def _turn(agent: Agent, sid: str) -> dict:
    await agent._tasks[sid]
    return await agent._session(sid)


async def test_read_propose_approve_rehearse_until_proven(tmp_path, monkeypatch) -> None:
    script: list[Step] = []
    fake = FakeRehearsal(failures=2)
    monkeypatch.setattr(isolating, "rehearse_plan", fake)
    agent = _agent(tmp_path)
    agent.llm = _model(script, agent._live)
    plans = PlanStore(agent.targets.config.data_dir)
    sid = (await agent.create("bot", "main"))["id"]

    unsupported = {"id": "redis-cache", "kind": "redis", "locator": "env:REDIS_URL"}
    script += [
        ("read_isolation", {}),
        (
            "propose_isolation_plan",
            {"changes": [{"op": "add", "new": unsupported, "cite": "c999"}]},
        ),
        (
            "propose_isolation_plan",
            {"changes": [{"op": "set", "store": "postgres-atrium", "moved_by": ["MEM_PATH"]}]},
        ),
        (
            "propose_isolation_plan",
            {
                "changes": [{"op": "set", "store": "postgres-atrium", "keyed_by": "person"}],
                "why": "Each conversation row belongs to a person.",
            },
        ),
        ("rehearse_isolation", {}),
        _show,
        "Waiting for the admin.",
    ]
    await agent.message(sid, "Are the tries kept apart?")
    session = await _turn(agent, sid)
    read, unsupported_said, unknown, valid, early, shown = _tool_results(session)
    assert "No isolation plan yet" not in read and '"stale": []' in read
    assert "atrium.phoenix.spans" in read, "what the probe found is shown"
    assert "env:DATABASE_URL" in read and "ATRIUM_DB_URL" in read
    assert '"local.db"' in read, "a literal the probe never reached is listed as dropped"
    assert unsupported_said.startswith("Refused, nothing proposed.")
    assert "nothing found supports it" in unsupported_said
    assert unknown.startswith("Refused, nothing proposed.") and "MEM_PATH" in unknown
    assert "postgres-atrium is kept per person" in valid and '"status": "draft"' in valid
    assert early.startswith("Refused: proposal") and "not approved" in early
    assert "isolation plan" in shown
    said = session["messages"][-1]
    assert said["isolation"]["changes"] == ["postgres-atrium is kept per person (was not known)."]
    assert "What changes in the isolation plan (from the saved plan)" in said["text"]
    # Proposing touched nothing saved.
    assert len(plans.history("bot")) == 1
    assert plans.load("bot").status == "draft" and plans.load("bot").stores[0].keyed_by is None
    assert fake.plans == []

    # Approved on the page: rehearsed, fails naming the memory index, and the writer adds it.
    script += [
        lambda s: ("rehearse_isolation", {"plan_id": _latest(s)}),
        ("read_isolation", {}),
        _cite_leak,
        _show,
        "Shown.",
    ]
    first = _latest(session)
    await agent.approve(sid, first)
    session = await _turn(agent, sid)
    asked = [m for m in session["messages"] if m["role"] == "admin"][-1]
    assert asked["text"] == "Rehearse this isolation plan" and asked["approves"] == first
    results = _tool_results(session)
    failed, reread, added = results[-4], results[-3], results[-2]
    assert '"status": "failed"' in failed and "memory_index" in failed
    assert '"left": 2' in failed
    assert "rehearsal 1:" in reread
    assert "Adds volume-artifact-data: volume at volume:artifact-data" in added
    assert plans.load("bot").status == "draft", "a failed rehearsal saves nothing"

    # Fails again; the writer tries once more, then it holds.
    script += [
        ("rehearse_isolation", {}),
        (
            "propose_isolation_plan",
            {"changes": [{"op": "set", "store": "volume-artifact-data", "keyed_by": "person"}]},
        ),
        _show,
        "Shown.",
    ]
    await agent.approve(sid, _latest(session))
    session = await _turn(agent, sid)
    assert '"failed_rehearsals": 2' in _tool_results(session)[-3]

    script += [("rehearse_isolation", {}), ("reply", {"text": "It holds now."}), "Done."]
    await agent.approve(sid, _latest(session))
    session = await _turn(agent, sid)
    proven = _tool_results(session)[-2]
    assert '"status": "proven"' in proven and '"saved_as_version": 2' in proven

    assert len(fake.plans) == 3
    assert [s.id for s in fake.plans[0].stores] == ["postgres-atrium"]
    assert "volume-artifact-data" in [s.id for s in fake.plans[1].stores]
    now = plans.load("bot")
    assert now.status == "proven"
    assert {s.id: s.keyed_by for s in now.stores} == {
        "postgres-atrium": "person",
        "volume-artifact-data": "person",
    }
    volume = next(s for s in now.stores if s.id == "volume-artifact-data")
    assert volume.strategy == "relocate" and LEAK in volume.seen[0]
    assert now.exclude == ["atrium.phoenix.spans"]
    # Undo goes back to the plan before the proposals.
    back = plans.undo("bot")
    assert back is not None and back.status == "draft"
    assert [s.id for s in back.stores] == ["postgres-atrium"]


@pytest.fixture
async def offline(tmp_path):
    made = _agent(tmp_path)
    yield made
    for task in list(made._tasks.values()):
        await task


async def _approved(agent: Agent, session: dict, changes: list[dict]) -> tuple[str, dict]:
    """A proposal shown and approved on the page: its id, and the session as it is now."""
    turn = AgentTurn(session)
    found = await agent.tools.propose_isolation_plan(turn, {"changes": changes})
    assert isinstance(found, dict), found
    await agent.tools.reply(turn, {"text": "This one.", "isolation": found["proposal"]})
    await agent.approve(session["id"], found["proposal"])
    await agent._tasks[session["id"]]
    return found["proposal"], await agent._session(session["id"])


async def test_three_failed_rehearsals_and_the_admin_is_asked(offline, monkeypatch) -> None:
    fake = FakeRehearsal(failures=99)
    monkeypatch.setattr(isolating, "rehearse_plan", fake)
    agent = offline
    sid = (await agent.create("bot", "main"))["id"]
    session = await agent._session(sid)
    for n, who in enumerate(["person", "account", "member"], 1):
        pid, session = await _approved(
            agent, session, [{"op": "set", "store": "postgres-atrium", "keyed_by": who}]
        )
        found = await agent.tools.rehearse_isolation(AgentTurn(session), {"plan_id": pid})
        assert found["status"] == "failed" and found["failed_rehearsals"] == n
    assert "Ask the admin" in found["next"] and "memory_index" in found["next"]
    assert "kept per account" in found["next"], "what was tried, in plain words"

    pid, session = await _approved(
        agent, session, [{"op": "set", "store": "postgres-atrium", "keyed_by": "owner"}]
    )
    refused = await agent.tools.rehearse_isolation(AgentTurn(session), {"plan_id": pid})
    assert refused.startswith("Refused: 3 rehearsals failed in this session")
    assert "Ask the admin" in refused and LEAK in refused
    assert len(fake.plans) == 3
    assert PlanStore(agent.targets.config.data_dir).load("bot").status == "draft"

    # Another chat on the same target starts its own count.
    other = await agent._session((await agent.create("bot", "main"))["id"])
    change = {"op": "set", "store": "postgres-atrium", "keyed_by": "x"}
    pid, other = await _approved(agent, other, [change])
    found = await agent.tools.rehearse_isolation(AgentTurn(other), {"plan_id": pid})
    assert found["status"] == "failed" and found["failed_rehearsals"] == 1


async def test_edits_must_rest_on_evidence(offline) -> None:
    agent = offline
    session = await agent._session((await agent.create("bot", "main"))["id"])
    turn = AgentTurn(session)

    async def propose(*changes: dict) -> Any:
        return await agent.tools.propose_isolation_plan(turn, {"changes": list(changes)})

    read = await agent.tools.read_isolation(turn, {})
    by_locator = {c["locator"]: c["id"] for c in read["candidates"]}

    refused = await propose({"op": "exclude", "entry": "atrium.engine.round_events"})
    assert "the probe never found atrium.engine.round_events" in refused
    refused = await propose({"op": "include", "entry": "atrium.phoenix.spans"})
    assert "trace table; it stays excluded" in refused
    refused = await propose({"op": "remove", "store": "postgres-atrium"})
    assert "found the canary in it" in refused
    # A citation about something else doesn't support a store.
    wrong = {"id": "redis", "kind": "redis", "locator": "env:REDIS_URL"}
    refused = await propose({"op": "add", "new": wrong, "cite": by_locator["env:DATABASE_URL"]})
    assert "is about env:DATABASE_URL, not env:REDIS_URL" in refused
    # One bad edit refuses the whole proposal: the admin approves exactly what is shown.
    refused = await propose(
        {"op": "set", "store": "postgres-atrium", "keyed_by": "person"},
        {"op": "set", "store": "postgres-atrium", "strategy": "snapshot"},
    )
    assert refused.startswith("Refused, nothing proposed.") and "snapshot needs a path" in refused
    assert isolating.state(session)["proposals"] == {}

    # The code read's candidate supports a store, its setting moves it.
    volume = {"id": "artifacts", "kind": "volume", "locator": "volume:artifact-data"}
    ok = await propose({"op": "add", "new": volume, "cite": by_locator["volume:artifact-data"]})
    assert ok["changes"][0].startswith("Adds artifacts: volume at volume:artifact-data")
    # A second proposal builds on the first.
    ok = await propose({"op": "set", "store": "artifacts", "keyed_by": "person"})
    assert ok["version"] == 2 and ok["from"] == "proposal 1"
    with pytest.raises(ValueError, match=r"changes\.op"):
        await propose({"op": "drop"})

    # Approving an isolation plan never saves a case with it.
    change = {"op": "set", "store": "artifacts", "keyed_by": "x"}
    pid, session = await _approved(agent, session, [change])
    saved = await agent.tools.save_case(AgentTurn(session), {"plan_id": pid})
    assert saved.startswith("Refused: that is an isolation plan")


async def test_with_no_plan_the_writer_says_how_to_make_one(tmp_path) -> None:
    agent = _agent(tmp_path, saved=False)
    session = await agent._session((await agent.create("bot", "main"))["id"])
    turn = AgentTurn(session)
    read = await agent.tools.read_isolation(turn, {})
    assert read["plan"] is None and "`evalution isolate bot`" in read["note"]
    assert any(c["locator"] == "env:DATABASE_URL" for c in read["candidates"])
    change = {"op": "set", "store": "x", "keyed_by": "person"}
    refused = await agent.tools.propose_isolation_plan(turn, {"changes": [change]})
    assert (
        refused.startswith("Refused: bot has no isolation plan") and "evalution isolate" in refused
    )
