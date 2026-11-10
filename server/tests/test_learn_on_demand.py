"""A learn from the skeleton, read on demand (ADR 0012, docs/plans/learn-on-demand.md "Phase 2"):
no file read in bulk, areas weighed by the skeleton and real use, the heaviest first, each kept
as it lands, and never the same code learned twice. The models are scripted, as in
``test_agent_approval.py``: the made-up app there is three areas the app uses (root, agents,
lib) and one it doesn't (web)."""

import asyncio
import contextlib
import json

import pytest
import yaml
from fastapi.testclient import TestClient
from test_agent_approval import (
    APP,
    FIRST_ARGS,
    OVERVIEW_ARGS,
    _area_runs,
    _first_text,
    _git,
    _learn,
    _learning,
)

from evalution.api.app import create_app
from evalution.config import ConfigError, load
from evalution.project import areas, drift, filememory, understanding
from evalution.project.skeleton import Entry
from evalution.writer import areas as area_agents
from evalution.writer import learn, saving


def _with_use(monkeypatch, tools: dict[str, int]) -> None:
    """Real use the learn reads: *tools* called so many times, and the drift check saw it move."""
    real = drift.take
    monkeypatch.setattr(learn, "_free_quiet", lambda _targets, _name: False)

    async def take(targets, name, sessions, **kw):
        found, taken = await real(targets, name, sessions, **kw)
        found["tools"] = {n: {"calls": c, "offered": True} for n, c in tools.items()}
        found["sessions"] = 5
        return found, taken

    monkeypatch.setattr(drift, "take", take)


def _order(monkeypatch) -> tuple[list[str], list[tuple[int, int]]]:
    """The areas' agents in the order they start, one at a time, and the steps and tokens each
    was given."""
    started: list[str] = []
    budgets: list[tuple[int, int]] = []
    real = area_agents.run_area

    async def run(area, ctx, before, budget=None, shown=None):
        started.append(area.id)
        budgets.append((budget.steps, budget.left))
        return await real(area, ctx, before, budget, shown)

    monkeypatch.setattr(area_agents, "run_area", run)
    monkeypatch.setattr(learn, "PARALLEL", 1)
    return started, budgets


async def test_areas_run_heaviest_first_and_real_use_weighs_and_moves_only_its_area(
    tmp_path, monkeypatch
) -> None:
    started, budgets = _order(monkeypatch)
    agent, _repo, sent, _script = _learning(tmp_path)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    weights = {a["id"]: a["weight"] for a in shown["learn"]["areas"]}
    # root: a prompt, a prompt builder, a tool and a route; agents: a prompt, an agent and its
    # tool; lib: a store; web: nothing of the skeleton.
    assert weights == {"root": 4, "agents": 3, "lib": 1, "web": 0}
    assert started == ["root", "agents", "lib"]
    assert not sent["light"], "no file read in bulk"
    # Its budget grows with its weight, from the floor.
    assert budgets == [area_agents.allowance(w) for w in (4, 3, 1)]
    assert budgets[-1] == (area_agents.STEPS, area_agents.BUDGET)
    assert budgets[0][1] > budgets[1][1] > budgets[2][1]
    # Its brief is its part of the skeleton, not summaries.
    root = next(b for b in sent["areas"] if "The area root:" in json.dumps(b))
    brief = _first_text(root)
    assert "[Data: its part of the app's skeleton" in brief
    assert "tool greet_people (app.py:6) args: name" in brief and "You greet people." in brief
    assert "read closely" not in brief

    # The search tool is used a lot now: agents is the heaviest, and its trace band moved, so it
    # alone is learned again, told how often it was used. The code didn't move.
    _with_use(monkeypatch, {"search": 40})
    started.clear()
    before = len(sent["areas"])
    shown = await _learn(agent, sid)
    assert started == ["agents"] and _area_runs(shown) == ["agents"]
    assert {a["id"]: a["weight"] for a in shown["learn"]["areas"]}["agents"] == 43
    told = _first_text(sent["areas"][before])
    assert "tool search (agents/worker.yaml:4); used 40 times lately" in told
    # A few more calls of the same tool stay in its band: nothing is learned again.
    _with_use(monkeypatch, {"search": 45})
    started.clear()
    calls = len(sent["areas"]) + len(sent["overview"])
    shown = await _learn(agent, sid)
    assert started == [] and len(sent["areas"]) + len(sent["overview"]) == calls
    assert shown["usage"][-1]["calls"] == 0


def test_budgets_scale_with_weight_from_a_floor() -> None:
    assert area_agents.allowance(0) == (area_agents.STEPS, area_agents.BUDGET)
    assert area_agents.allowance(1) == (area_agents.STEPS, area_agents.BUDGET)
    full = (area_agents.MAX_STEPS, area_agents.MAX_BUDGET)
    assert area_agents.allowance(area_agents.FULL_WEIGHT) == full
    assert area_agents.allowance(10 * area_agents.FULL_WEIGHT) == full
    steps = [area_agents.allowance(w)[0] for w in range(1, area_agents.FULL_WEIGHT + 1)]
    assert steps == sorted(steps) and steps[0] < steps[-1]
    budget = area_agents.Budget.of(area_agents.FULL_WEIGHT)
    assert (budget.steps, budget.left) == full


def test_an_areas_key_moves_with_its_files_and_its_trace_bands_only() -> None:
    area = areas.Area("lib", ("lib",), ("lib/a.py", "lib/b.py"), 0)
    entry = Entry("tool", "search", "lib/a.py", 3)
    prints = {"lib/a.py": "1", "lib/b.py": "2", "other.py": "3"}
    key = areas.key(area, prints, [entry], {entry.id: 4})
    assert areas.key(area, {**prints, "other.py": "9"}, [entry], {entry.id: 4}) == key
    assert areas.key(area, prints, [entry], {entry.id: 9}) == key, "1 to 9 is one band"
    assert areas.key(area, prints, [entry], {entry.id: 10}) != key
    assert areas.key(area, prints, [entry], {}) != key, "none is a band of its own"
    assert areas.key(area, {**prints, "lib/b.py": "5"}, [entry], {entry.id: 4}) != key
    assert [areas.band(n) for n in (0, 1, 9, 10, 99, 100, 5000)] == [0, 1, 1, 2, 2, 3, 3]
    assert areas.weight([entry], {entry.id: 4}) == 5


async def test_a_learn_stopped_after_two_areas_keeps_both_and_the_next_learns_only_the_rest(
    tmp_path, monkeypatch
) -> None:
    agent, _repo, sent, _script = _learning(tmp_path)
    sid = (await agent.create("bot", "main"))["id"]
    real = area_agents.run_area
    started: list[str] = []

    async def stopped_at_the_third(area, ctx, before, budget=None, shown=None):
        started.append(area.id)
        if len(started) == 3:
            await asyncio.sleep(0.2)  # the second area's section is being kept
            agent._tasks[sid].cancel()
            await asyncio.sleep(10)
        return await real(area, ctx, before, budget, shown)

    monkeypatch.setattr(area_agents, "run_area", stopped_at_the_third)
    monkeypatch.setattr(learn, "PARALLEL", 1)
    await agent.message(sid, "Learn bot.", learn=True)
    with contextlib.suppress(asyncio.CancelledError):
        await agent._tasks[sid]
    assert started == ["root", "agents", "lib"]
    kept = filememory.load(agent.targets, "bot")
    assert sorted(kept["areas"]) == ["agents", "root"] and kept["incomplete"] is True
    # The understanding was saved as each area landed: the first overview and those two.
    doc = understanding.load(agent.targets, "bot")
    assert [s["id"] for s in doc["sections"]] == [
        "what_it_is",
        f"{understanding.AREA_PREFIX}root",
        f"{understanding.AREA_PREFIX}agents",
    ]
    assert doc["overview"]["state"] == "first_pass"
    progress = understanding.progress(agent.targets, "bot")
    assert progress["state"] == "stopped" and progress["overview"] == "first_pass"
    states = {a["id"]: a["state"] for a in progress["areas"]}
    assert states == {"root": "done", "agents": "done", "lib": "due", "web": "listed"}
    assert len(understanding.versions(agent.targets, "bot")) == 1, "one version a learn"

    monkeypatch.undo()
    before = len(sent["areas"])
    shown = await _learn(agent, sid)
    assert _area_runs(shown) == ["lib"] and len(sent["areas"]) == before + 1
    states = {a["id"]: a["state"] for a in shown["learn"]["areas"]}
    assert states == {"root": "kept", "agents": "kept", "lib": "learned", "web": "listed"}
    sections = {s["id"] for s in understanding.load(agent.targets, "bot")["sections"]}
    assert {f"{understanding.AREA_PREFIX}{a}" for a in ("root", "agents", "lib")} <= sections
    # Still no final overview, so a first one is due, but of the same skeleton and areas: the one
    # kept, with no call. The final one replaces it.
    assert len(sent["first"]) == 1
    progress = understanding.progress(agent.targets, "bot")
    states = {a["id"]: a["state"] for a in progress["areas"]}
    assert states == {"root": "current", "agents": "current", "lib": "done", "web": "listed"}
    assert progress["state"] == "done" and progress["overview"] == "final"


async def test_a_listed_area_is_learned_on_request_and_a_current_one_only_when_forced(
    tmp_path,
) -> None:
    agent, repo, sent, _script = _learning(tmp_path)
    sid = (await agent.create("bot", "main"))["id"]
    await _learn(agent, sid)
    targets = agent.targets
    assert filememory.load(targets, "bot")["listed"] == ["web"]
    web = await learn.area_state(targets, "bot", "web")
    assert web["state"] == "listed" and web["weight"] == 0 and web["learned_at"] is None
    root = await learn.area_state(targets, "bot", "root")
    assert root["state"] == "current" and root["learned_at"] and root["weight"] == 4

    # "Learn this area": web alone, and the overview again since a section was added.
    before = len(sent["areas"])
    await agent.learn_area(sid, "web")
    await agent._tasks[sid]
    shown = await agent.get(sid)
    assert _area_runs(shown) == ["web"] and len(sent["areas"]) == before + 1
    assert shown["messages"][-2]["text"] == "Learn the area web."
    assert shown["messages"][-1]["text"] == "I've learned bot. Check what I understood."
    assert (await learn.area_state(targets, "bot", "web"))["state"] == "current"
    sections = {s["id"] for s in understanding.load(targets, "bot")["sections"]}
    assert f"{understanding.AREA_PREFIX}web" in sections
    # Learned once, it stays learned: no learn lists it again.
    assert filememory.load(targets, "bot")["listed"] == []

    # Unchanged: said current, with no model call, unless asked again anyway.
    calls = len(sent["areas"])
    await agent.learn_area(sid, "web")
    await agent._tasks[sid]
    said = (await agent.get(sid))["messages"][-1]["text"]
    assert said.startswith("web is current, learned ") and len(sent["areas"]) == calls
    await agent.learn_area(sid, "web", force=True)
    await agent._tasks[sid]
    assert len(sent["areas"]) == calls + 1
    assert (await agent.get(sid))["messages"][-2]["text"] == "Learn the area web again."

    # web's file moved: it was learned, so it is kept current like any other.
    (repo / "web/chat.ts").write_text(APP["web/chat.ts"] + "export const X = 2;\n")
    _git(repo, "commit", "-qam", "web")
    assert (await learn.area_state(targets, "bot", "web"))["state"] == "moved"
    shown = await _learn(agent, sid)
    assert _area_runs(shown) == ["web"]


async def test_an_area_asked_for_while_a_learn_goes_is_learned_before_its_overview(
    tmp_path, monkeypatch
) -> None:
    agent, _repo, _sent, _script = _learning(tmp_path)
    sid = (await agent.create("bot", "main"))["id"]
    real = area_agents.run_area

    async def asks_for_web(area, ctx, before, budget=None, shown=None):
        if area.id == "root":
            await agent.learn_area(sid, "web")
        return await real(area, ctx, before, budget, shown)

    monkeypatch.setattr(area_agents, "run_area", asks_for_web)
    shown = await _learn(agent, sid)
    assert _area_runs(shown)[-1] == "web" and len(_area_runs(shown)) == 4
    assert "Listed" not in shown["messages"][-1]["text"]
    states = {a["id"]: a["state"] for a in shown["learn"]["areas"]}
    assert states["web"] == "learned"


async def test_every_skeleton_name_must_be_named_by_some_section(tmp_path) -> None:
    agent, _repo, _sent, _script = _learning(tmp_path, odd={"lib": "quiet"})
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    said = shown["messages"][-1]["text"]
    assert said.endswith("Named in no section: MemoryStore.")
    assert shown["usage"][-1]["uncovered"] == 1


def test_the_api_says_an_unchanged_area_is_current_and_spends_nothing(tmp_path) -> None:
    agent, _repo, _sent, _script = _learning(tmp_path)

    async def first() -> None:
        sid = (await agent.create("bot", "main"))["id"]
        await _learn(agent, sid)

    asyncio.run(first())
    config = load(tmp_path / "evalution.yaml")
    with TestClient(create_app(config)) as client:
        current = client.post("/v1/targets/bot/areas/root/learn", json={})
        assert current.status_code == 200
        body = current.json()
        assert body["session"] is None and body["area"]["state"] == "current"
        assert body["area"]["learned_at"] > 0
        # Anything that would spend needs a model, which this server has none of.
        for area, asked in (("root", {"force": True}), ("web", {})):
            refused = client.post(f"/v1/targets/bot/areas/{area}/learn", json=asked)
            assert refused.status_code == 409 and "No model" in refused.json()["detail"]
        assert client.post("/v1/targets/bot/areas/nowhere/learn", json={}).status_code == 404
        bad = client.post("/v1/targets/bot/areas/root/learn", json={"force": "yes"})
        assert bad.status_code == 400


def test_an_areas_parallel_setting_is_checked(tmp_path) -> None:
    block = {"adapter": "http", "http": {"send": {"url": "x"}}, "code": {"path": "app"}}
    for bad in (0, 33, True, "8"):
        block["code"]["area_parallel"] = bad
        (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
        with pytest.raises(ConfigError, match="area_parallel"):
            load(tmp_path / "evalution.yaml")
    block["code"]["area_parallel"] = 2
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
    code = load(tmp_path / "evalution.yaml").targets["bot"].code
    assert code is not None and code.area_parallel == 2


def _states(progress: dict) -> dict[str, str]:
    return {a["id"]: a["state"] for a in progress["areas"]}


async def test_the_understanding_shows_a_first_overview_then_each_area_as_it_lands(
    tmp_path, monkeypatch
) -> None:
    """Phase 3 and the server half of phase 4: a first learn writes a first overview from the
    skeleton before any area, saves the understanding again as each area lands, and says how it
    goes area by area; the final overview replaces the first."""
    agent, _repo, sent, _script = _learning(tmp_path)
    targets = agent.targets
    sid = (await agent.create("bot", "main"))["id"]
    real_run, real_partial = area_agents.run_area, saving.keep_partial
    seen: dict[str, dict] = {}
    partial: list[tuple[list[str], str | None]] = []

    async def run(area, ctx, before, budget=None, shown=None):
        seen[area.id] = {
            "progress": understanding.progress(targets, "bot"),
            "doc": understanding.load(targets, "bot"),
            "view": understanding.view(targets, agent.tools.runs, "bot"),
        }
        return await real_run(area, ctx, before, budget, shown)

    def keep_partial(targets_, name, written, found, replace):
        ids = [s["id"] for s in written["sections"]]
        partial.append((ids, (written.get("overview") or {}).get("state")))
        return real_partial(targets_, name, written, found, replace)

    monkeypatch.setattr(area_agents, "run_area", run)
    monkeypatch.setattr(saving, "keep_partial", keep_partial)
    monkeypatch.setattr(learn, "PARALLEL", 1)
    shown = await _learn(agent, sid)

    # One call more than before ADR 0012's phase 3: the first overview, by the light model, from
    # the skeleton and the areas alone.
    assert len(sent["first"]) == 1 and sent["first"][0]["model"] == "l"
    assert sent["first"][0]["max_tokens"] == learn.OVERVIEW_TOKENS
    brief = _first_text(sent["first"][0])
    assert "tool greet_people (app.py:6)" in brief
    assert "root (" in brief and "web (" in brief and "nothing in it" in brief
    assert "[Data: the areas' sections" not in brief
    assert len(sent["overview"]) == 1 and len(sent["areas"]) == 3
    usage = shown["usage"][-1]
    assert usage["first_overview_calls"] == 1 and usage["overview_calls"] == 1

    # Saved before any area started: the first pass alone, said as such.
    first = seen["root"]["doc"]
    assert first["one_line"] == FIRST_ARGS["one_line"]
    assert [s["id"] for s in first["sections"]] == ["what_it_is"]
    assert first["overview"] == {"state": "first_pass", "basis": "the code's skeleton"}
    view = seen["root"]["view"]
    assert view["card"]["one_line"] == FIRST_ARGS["one_line"] and view["diagram"]["nodes"] == []
    assert view["overview"]["state"] == "first_pass"
    assert view["progress"]["overview"] == "first_pass"
    progress = seen["root"]["progress"]
    assert progress["state"] == "learning" and progress["overview"] == "first_pass"
    assert progress["started_at"] > 0
    assert [a["id"] for a in progress["areas"]] == ["root", "agents", "lib", "web"], "heaviest"
    assert _states(progress) == {
        "root": "learning",
        "agents": "due",
        "lib": "due",
        "web": "listed",
    }
    root = progress["areas"][0]
    assert root == {
        "id": "root",
        "title": "the top folder",
        "state": "learning",
        "weight": 4,
        "files": root["files"],
        "learned_at": None,
    }
    assert root["files"] >= 1
    assert _states(seen["lib"]["progress"])["lib"] == "learning"
    assert _states(seen["lib"]["progress"])["web"] == "listed"

    # Saved again as each area landed, the sections growing, the first pass standing meanwhile.
    assert partial[0] == (["what_it_is"], "first_pass")
    counts = [len(ids) for ids, _ in partial[1:]]
    assert counts == sorted(counts) and counts[0] >= 2
    area_ids = {f"{understanding.AREA_PREFIX}{a}" for a in ("root", "agents", "lib")}
    assert area_ids <= set(partial[-1][0])
    assert all(state == "first_pass" for _, state in partial)

    # Done: the final overview replaced the first, and every area says how it went.
    doc = understanding.load(targets, "bot")
    assert doc["one_line"] == OVERVIEW_ARGS["one_line"]
    assert doc["overview"] == {"state": "final", "basis": "3 learned areas and the code's skeleton"}
    progress = understanding.progress(targets, "bot")
    assert progress["state"] == "done" and progress["overview"] == "final"
    assert _states(progress) == {"root": "done", "agents": "done", "lib": "done", "web": "listed"}
    learned = {a["id"]: a for a in progress["areas"]}
    assert all(learned[a]["learned_at"] for a in ("root", "agents", "lib"))
    assert learned["root"]["title"] == "The root area", "its section's title once learned"
    assert learned["web"]["learned_at"] is None
    assert len(understanding.versions(targets, "bot")) == 1, "one version a learn"


async def test_no_first_overview_when_nothing_or_little_moved(tmp_path) -> None:
    agent, repo, sent, _script = _learning(tmp_path)
    targets = agent.targets
    sid = (await agent.create("bot", "main"))["id"]
    await _learn(agent, sid)
    assert len(sent["first"]) == 1 and len(sent["overview"]) == 1
    doc = understanding.load(targets, "bot")

    # Nothing moved: no call, the overview and the progress untouched.
    progress = understanding.progress(targets, "bot")
    shown = await _learn(agent, sid)
    assert shown["messages"][-1]["text"].startswith("Nothing changed since")
    assert len(sent["first"]) == 1 and understanding.progress(targets, "bot") == progress
    assert understanding.load(targets, "bot")["version"] == doc["version"]

    # A small change (one area of three) with an overview: no first pass; the area learned again
    # came out the same, so the overview stands, with no call.
    (repo / "lib/memory.py").write_text(
        "class MemoryStore:\n    def remember(self, name):\n        return name.strip()\n"
    )
    _git(repo, "commit", "-qam", "keep")
    shown = await _learn(agent, sid)
    assert _area_runs(shown) == ["lib"]
    assert len(sent["first"]) == 1 and len(sent["overview"]) == 1
    assert shown["usage"][-1]["first_overview_calls"] == 0
    progress = understanding.progress(targets, "bot")
    assert progress["state"] == "done" and progress["overview"] == "final"
    states = _states(progress)
    assert states == {"root": "current", "agents": "current", "lib": "done", "web": "listed"}
    after = understanding.load(targets, "bot")
    assert after["overview"]["state"] == "final" and after["one_line"] == doc["one_line"]
    assert len(understanding.versions(targets, "bot")) == 2, "one version a learn"

    # Most of it moved (two areas of three): a first overview again, then the final one.
    (repo / "app.py").write_text(APP["app.py"] + "\nGREETING = 1\n")
    (repo / "agents/planner.md").write_text("# Planner\nPlans the week.\n")
    _git(repo, "commit", "-qam", "most")
    shown = await _learn(agent, sid)
    assert sorted(_area_runs(shown)) == ["agents", "root"]
    assert len(sent["first"]) == 2 and shown["usage"][-1]["first_overview_calls"] == 1
    assert understanding.load(targets, "bot")["overview"]["state"] == "final"


def test_an_understanding_from_before_progress_reads_as_no_learn(tmp_path) -> None:
    """Old understandings have no progress: ``state: null``. One left learning by an earlier
    server says stopped, its area in flight due again."""
    agent, _repo, _sent, _script = _learning(tmp_path)
    targets = agent.targets
    assert understanding.progress(targets, "bot") == {
        "state": None,
        "started_at": None,
        "areas": [],
        "overview": None,
        "left_out": {},
    }

    async def first() -> None:
        sid = (await agent.create("bot", "main"))["id"]
        await _learn(agent, sid)

    asyncio.run(first())
    config = load(tmp_path / "evalution.yaml")
    with TestClient(create_app(config)) as client:
        body = client.get("/v1/targets/bot/understanding").json()
        assert body["progress"]["state"] == "done" and body["progress"]["overview"] == "final"
        assert [a["id"] for a in body["progress"]["areas"]] == ["root", "agents", "lib", "web"]
        assert set(body["progress"]["areas"][0]) == {
            "id",
            "title",
            "state",
            "weight",
            "files",
            "learned_at",
        }
        assert body["overview"]["state"] == "final"

        understanding.keep_progress(targets, "bot", {})
        body = client.get("/v1/targets/bot/understanding").json()
        assert body["progress"] == {
            "state": None,
            "started_at": None,
            "areas": [],
            "overview": None,
            "left_out": {},
        }

        cut = {
            "state": "learning",
            "started_at": 1.0,
            "overview": "first_pass",
            "areas": [
                {"id": "root", "title": "r", "state": "learning", "weight": 4, "files": 1},
                {"id": "lib", "title": "l", "state": "done", "weight": 1, "files": 1},
            ],
        }
        understanding.keep_progress(targets, "bot", cut)
        body = client.get("/v1/targets/bot/understanding").json()
        assert body["progress"]["state"] == "stopped"
        assert [a["state"] for a in body["progress"]["areas"]] == ["due", "done"]


# A heavy area, learned in pieces (Phase 5's run: one area of 48 tools named only in agents'
# settings ran out of its budget before it wrote anything).

#: Sixty tools the agents' settings list, in one file, beside a few code files.
TEAM = "tools:\n" + "".join(f"  - tool_{n:02}\n" for n in range(1, 61))
HEAVY = {
    "agents/team.yaml": TEAM,
    "agents/router.py": "@tool\ndef route_call(x):\n    return x\n",
    "agents/handoff.py": "@tool\ndef hand_off(x):\n    return x\n",
}


def _tools(path: str, count: int, first: int = 1) -> list[Entry]:
    return [Entry("tool", f"tool_{n:02}", path, n + 1) for n in range(first, first + count)]


def test_a_heavy_area_splits_by_folder_then_file_then_entries_the_same_way_twice() -> None:
    files = (
        "agents/handoff.py",
        "agents/readme_helpers.py",
        "agents/settings/team.yaml",
        "agents/tools/a.py",
        "agents/tools/b.py",
    )
    area = areas.Area("agents", ("agents",), files, 500)
    found = [
        Entry("tool", "hand_off", "agents/handoff.py", 1),
        *_tools("agents/settings/team.yaml", 60),
        *[Entry("tool", f"a_{n}", "agents/tools/a.py", n) for n in range(1, 11)],
        *[Entry("tool", f"b_{n}", "agents/tools/b.py", n) for n in range(1, 21)],
        Entry("tool", "elsewhere", "lib/x.py", 1),
    ]
    piece, ceiling = area_agents.FULL_WEIGHT, area_agents.SPLIT_WEIGHT
    assert ceiling == 36 and piece == 24
    pieces = areas.split(area, found, piece, ceiling, {p: 100 for p in files})
    assert areas.split(area, found, piece, ceiling, {p: 100 for p in files}) == pieces
    shape = [(p.id, p.files, len(areas.entries_of(p, found))) for p in pieces]
    assert shape == [
        # The top folder (a file with no entry in it) and, packed after it while it fits, the
        # first file of a folder of 30, which is over a piece and so goes by its files.
        ("agents.1", ("agents/handoff.py", "agents/readme_helpers.py", "agents/tools/a.py"), 11),
        # One file of 60 tools: by its entries, in three near-equal pieces of it alone.
        ("agents.2", ("agents/settings/team.yaml",), 20),
        ("agents.3", ("agents/settings/team.yaml",), 20),
        ("agents.4", ("agents/settings/team.yaml",), 20),
        ("agents.5", ("agents/tools/b.py",), 20),
    ]
    assert all(p.of == "agents" and p.size == 100 * len(p.files) for p in pieces)
    assert (
        pieces[1].label == "part 2 of 5: 20 tools in agents/settings/team.yaml (tool_01 to tool_20)"
    )
    assert pieces[0].label == "part 1 of 5: 11 tools in 3 files (hand_off to a_10)"
    # Every entry of the area in one piece, none twice; each within a full budget.
    held = [e.id for p in pieces for e in areas.entries_of(p, found)]
    assert sorted(held) == sorted(e.id for e in areas.entries_of(area, found))
    assert all(areas.weight(areas.entries_of(p, found), {}) <= piece for p in pieces)
    # Up to the ceiling it is learned whole.
    light = [e for e in found if e.path != "agents/settings/team.yaml"]
    assert len(areas.entries_of(area, light)) == 31 <= ceiling
    assert areas.split(area, light, piece, ceiling) == [area]


async def test_a_heavy_area_is_learned_in_pieces_each_within_its_budget_and_kept_apart(
    tmp_path, monkeypatch
) -> None:
    started, budgets = _order(monkeypatch)
    agent, repo, sent, _script = _learning(tmp_path, extra=HEAVY)
    targets = agent.targets
    sid = (await agent.create("bot", "main"))["id"]
    seen: list[dict] = []
    real = area_agents.run_area

    async def run(area, ctx, before, budget=None, shown=None):
        seen.append(understanding.progress(targets, "bot"))
        return await real(area, ctx, before, budget, shown)

    monkeypatch.setattr(area_agents, "run_area", run)
    shown = await _learn(agent, sid)
    weights = {a["id"]: a["weight"] for a in shown["learn"]["areas"]}
    # agents: the 60 tools of its settings in three pieces, and the rest of it in one.
    assert weights == {
        "agents.2": 20,
        "agents.3": 20,
        "agents.4": 20,
        "agents.1": 5,
        "root": 4,
        "lib": 1,
        "web": 0,
    }
    assert started == ["agents.2", "agents.3", "agents.4", "agents.1", "root", "lib"]
    assert budgets == [area_agents.allowance(weights[a]) for a in started]
    usage = shown["usage"][-1]
    for run_of in usage["areas"]:
        steps, tokens = area_agents.allowance(weights[run_of["area"]])
        assert run_of["outcome"] == "learned" and run_of["calls"] <= steps
        assert run_of["tokens_in"] + run_of["tokens_out"] <= tokens
    assert usage["uncovered"] == 0 and usage["areas_total"] == 7
    # Each piece is told it is one, and given its own names only.
    told = _first_text(next(b for b in sent["areas"] if "The area agents.3:" in json.dumps(b)))
    assert "It is a piece of the area agents, part 3 of 4: 20 tools in agents/team.yaml" in told
    assert "tool_21" in told and "tool_40" in told
    assert "tool_20" not in told and "tool_41" not in told and "route_call" not in told
    # The page shows each piece as an area, titled by what it covers until it is learned.
    titles = {a["id"]: a["title"] for a in seen[0]["areas"]}
    assert (
        titles["agents.2"]
        == "agents/, part 2 of 4: 20 tools in agents/team.yaml (tool_01 to tool_20)"
    )
    assert titles["root"] == "the top folder"
    assert _states(seen[0])["agents.2"] == "learning" and _states(seen[0])["agents.3"] == "due"
    progress = understanding.progress(targets, "bot")
    assert progress["state"] == "done"
    assert _states(progress) == {a: "done" for a in started} | {"web": "listed"}
    assert {a["id"]: a["title"] for a in progress["areas"]}["agents.3"] == "The agents.3 area"
    # Their sections merged like any other's, ids under each piece.
    sections = [s["id"] for s in understanding.load(targets, "bot")["sections"]]
    for n in (1, 2, 3, 4):
        assert f"{understanding.AREA_PREFIX}agents.{n}" in sections

    # The same app splits the same way: nothing is learned again.
    started.clear()
    shown = await _learn(agent, sid)
    assert shown["messages"][-1]["text"].startswith("Nothing changed since") and not started
    # One piece's file moved: that piece alone is learned again.
    (repo / "agents/router.py").write_text(HEAVY["agents/router.py"] + "\nLIMIT = 3\n")
    _git(repo, "commit", "-qam", "router")
    assert (await learn.area_state(targets, "bot", "agents.1"))["state"] == "moved"
    assert (await learn.area_state(targets, "bot", "agents.2"))["state"] == "current"
    shown = await _learn(agent, sid)
    assert started == ["agents.1"] and _area_runs(shown) == ["agents.1"]


def _calls_of(shown: dict, area: str) -> list[dict]:
    return [a for a in shown["usage"][-1]["areas"] if a["area"] == area]


async def test_a_section_that_leaves_names_out_gets_one_more_step_naming_them(tmp_path) -> None:
    agent, _repo, sent, _script = _learning(tmp_path, odd={"root": "forgets"})
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    root = [b for b in sent["areas"] if "The area root:" in json.dumps(b)]
    assert len(root) == 2, "one more step, once"
    asked = json.dumps(root[1]["messages"][-1])
    assert "Your section names none of these of this area: greet_people, POST /chat." in asked
    assert "SYSTEM_PROMPT" not in asked.split("Your section names")[1]
    # Both ended covered: nothing is reported, and the section names them.
    usage = shown["usage"][-1]
    assert usage["uncovered"] == 0 and usage["follow_ups"] == 1
    assert (
        _calls_of(shown, "root")[0]["calls"] == 2 and _calls_of(shown, "root")[0]["follow_up"] == 2
    )
    assert all(a["follow_up"] == 0 for a in usage["areas"] if a["area"] != "root")
    assert "Named in no section" not in shown["messages"][-1]["text"]
    stages = {s["stage"]: s["calls"] for s in usage["stages"]}
    assert stages["areas"] == len(sent["areas"]) == 4
    section = next(
        s
        for s in understanding.load(agent.targets, "bot")["sections"]
        if s["id"] == f"{understanding.AREA_PREFIX}root"
    )
    said = json.dumps(section)
    assert "greet_people" in said and "/chat" in said and "SYSTEM_PROMPT" in said


async def test_the_follow_up_is_never_repeated_and_asked_alone_past_the_budget(
    tmp_path, monkeypatch
) -> None:
    # Told, it still names nothing: asked once, then reported as before.
    agent, _repo, sent, _script = _learning(tmp_path / "a", odd={"lib": "stubborn"})
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    assert len([b for b in sent["areas"] if "The area lib:" in json.dumps(b)]) == 2
    assert shown["messages"][-1]["text"].endswith("Named in no section: MemoryStore.")
    assert shown["usage"][-1]["follow_ups"] == 1

    # No step left, or no tokens for one more like the last: one small call of its own, carrying
    # the names left out and nothing of the area's reading, answered with write_section alone.
    real = area_agents.allowance
    for allowance in ((1, 10**6), (4, 200)):
        monkeypatch.setattr(area_agents, "allowance", lambda _w, given=allowance: given)
        agent, _repo, sent, _script = _learning(
            tmp_path / str(allowance[0]), odd={"lib": "forgets"}
        )
        sid = (await agent.create("bot", "main"))["id"]
        shown = await _learn(agent, sid)
        bodies = [b for b in sent["areas"] if "The area lib:" in json.dumps(b)]
        assert len(bodies) == 2
        first, alone = bodies
        assert len(alone["messages"]) == 1 and "names none of these" in json.dumps(alone)
        said = json.dumps(alone["messages"])
        assert "what the skeleton says of them" in said and "MemoryStore" in said
        assert "its part of the app's skeleton" in json.dumps(first["messages"])
        assert "its part of the app's skeleton" not in said
        assert [t["name"] for t in alone["tools"]] == ["write_section"]
        assert shown["usage"][-1]["follow_ups"] == 1
        assert "Named in no section" not in shown["messages"][-1]["text"]
    monkeypatch.setattr(area_agents, "allowance", real)


def test_a_section_titled_by_its_id_is_titled_by_what_it_covers() -> None:
    Area = areas.Area
    piece = Area(
        "tools.1",
        ("tools",),
        ("tools/a.py",),
        0,
        (),
        "tools",
        "part 1 of 2: 20 tools in tools/a.py",
    )
    covered = "tools/, part 1 of 2: 20 tools in tools/a.py"
    for given in ("tools.1", "TOOLS.1", "tools", " ", ""):
        assert area_agents._titled(piece, given) == covered
    assert area_agents._titled(piece, "The OS tools") == "The OS tools"
    half = Area("tools.1~2", piece.folders, piece.files, 0, (), "tools", piece.label)
    assert area_agents._titled(half, "tools.1") == covered
    lib = Area("lib", ("lib",), ("lib/a.py",), 0)
    assert area_agents._titled(lib, "") == area_agents._titled(lib, "LIB") == "lib/"
    assert area_agents._titled(Area("top", ("",), ("a.py",), 0), "top") == "the top folder"
