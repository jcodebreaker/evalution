"""What a learn of a large app behind a company gateway showed (docs/plans/next-level.md, "Seen on a
large app"): a gateway that cuts long calls (C8), a save past the profile's cap (C9), and a repo
whose areas are mostly not the agent (C7). The models are scripted as in
``test_agent_approval.py``."""

from __future__ import annotations

import asyncio

import pytest
from test_agent_approval import APP, _area_runs, _git, _learn, _learning

from evalution import code as code_module
from evalution import llm
from evalution.project import areas, profile, understanding
from evalution.project.skeleton import Entry
from evalution.writer import agent as agent_module
from evalution.writer import areas as area_agents
from evalution.writer import learn


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm, "_pause", pause)


async def test_a_gateway_that_cuts_long_calls_no_longer_fails_an_area(tmp_path) -> None:
    # Every area call of 4,096 output tokens is cut (504); asked for half, it passes.
    agent, _repo, sent, _script = _learning(tmp_path, gateway=2_048)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    assert sent["cut"], "the gateway cut the first calls"
    assert {b["max_tokens"] for b in sent["areas"]} == {2_048}
    assert all(a["outcome"] == "learned" for a in shown["usage"][-1]["areas"])
    assert "Not learned" not in shown["messages"][-1]["text"]
    sections = understanding.load(agent.targets, "bot")["sections"]
    assert any(s["id"].startswith(understanding.AREA_PREFIX) for s in sections)


async def test_an_area_the_gateway_always_cuts_says_why(tmp_path) -> None:
    agent, _repo, sent, _script = _learning(tmp_path, gateway=0)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    said = shown["messages"][-1]["text"]
    assert "the model's gateway timed out (504)" in said
    # Each try asked for less than the one before, never the same call again.
    asked = [b["max_tokens"] for b in sent["cut"]][:3]
    assert asked == [4_096, 2_048, 1_024]
    # The page names the cause on the area, not just "failed".
    failed = [
        a for a in understanding.progress(agent.targets, "bot")["areas"] if a["state"] == "failed"
    ]
    assert failed and all(
        a["reason"].startswith("the model's gateway timed out (504)") for a in failed
    )


# C9: a save past the profile's cap is cut to fit, heaviest areas first, and never raises.


def _area(n: int, claims: int, size: int) -> dict:
    return {
        "id": f"{understanding.AREA_PREFIX}a{n}",
        "title": f"Area {n}",
        "claims": [
            {
                "id": f"a{n}.c{k}",
                "text": f"Claim {k} of area {n}: " + "x" * size,
                "evidence": [{"kind": "code", "ref": f"a{n}/f.py"}],
            }
            for k in range(claims)
        ],
    }


def _doc(sections: list[dict], details: int = 0) -> dict:
    doc = {
        "one_line": "Sells shoes.",
        "nodes": [],
        "sections": [{"id": "what_it_is", "title": "What it is", "claims": []}, *sections],
        "priorities": [],
        "starters": [],
    }
    facts = [
        {"id": f"d{k}", "text": "fact " + "y" * 300, "evidence": [{"kind": "code", "ref": "f.py"}]}
        for k in range(details)
    ]
    return understanding.with_details(doc, facts)


def test_a_profile_past_the_cap_is_cut_heaviest_areas_first_and_says_so() -> None:
    sections = [_area(n, 10, 100 * (n + 1)) for n in range(6)]
    text, fit = understanding.fit_profile("bot", _doc(sections), [], 15_000)
    assert len(text) <= 15_000
    assert fit["of"] == 6 and 0 < fit["areas"] < 6
    # The heaviest went first: those left out are the last ones, heaviest first.
    assert fit["left_out"] == [f"Area {n}" for n in range(5, 5 - len(fit["left_out"]), -1)]
    assert "Left out of this profile to fit" in text and "Area 5" in text.split("Left out")[1]
    assert "## Area 0" in text


def test_details_are_not_part_of_the_profile() -> None:
    text, fit = understanding.fit_profile("bot", _doc([_area(0, 2, 10)], details=50), [], 10**6)
    assert "Details" not in text and "fact yyy" not in text
    assert fit == {"areas": 1, "of": 1, "left_out": []}


def test_even_an_overview_past_the_cap_is_cut_not_refused() -> None:
    doc = _doc([])
    doc["sections"][0]["claims"] = [
        {"id": f"c{k}", "text": "z" * 390, "evidence": [{"kind": "code", "ref": "f.py"}]}
        for k in range(100)
    ]
    text, fit = understanding.fit_profile("bot", doc, [], 5_000)
    assert len(text) <= 5_000 and fit["areas"] == fit["of"] == 0


async def test_a_learn_whose_sections_pass_the_cap_saves_and_says_how_many_are_in(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(profile, "PROFILE_CAP", 420)
    agent, _repo, _sent, _script = _learning(tmp_path)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    said = shown["messages"][-1]["text"]
    assert said.startswith("I've learned bot.") and "areas are in the profile" in said
    text = profile.read_profile(agent.targets, "bot")
    assert len(text) <= 420
    found = understanding.view(agent.targets, agent.runs, "bot")
    fit = found["profile"]
    assert fit["of"] >= 2 and fit["areas"] < fit["of"] and fit["left_out"]


# C7: the agent, not the whole repository.


def test_the_agents_scope_is_its_core_and_the_helpers_it_imports() -> None:
    entries = [
        Entry("prompt", "SYSTEM", "agent/prompts.py", 1),
        Entry("tool", "search", "agent/tools.py", 3, places=("agent/settings.yaml:4",)),
        Entry("model", "messages.create", "agent/run.py", 7),
        Entry("route", "POST /chat", "server/routes.py", 9),
        Entry("state", "sessions", "server/sessions.py", 4),
        Entry("entry", "create_app", "server/main.py", 1),
        Entry("route", "GET /items", "ui/page.tsx", 2),
        Entry("state", "users", "migrations/0001.sql", 1),
    ]
    edges = {
        "server/main.py": ["server/routes.py"],
        "server/routes.py": ["agent/run.py", "server/sessions.py"],
        "agent/run.py": ["agent/prompts.py", "agent/tools.py", "agent/util.py"],
        "agent/tools.py": ["lib/http.py"],
        "lib/http.py": ["lib/retry.py"],
        "lib/retry.py": ["lib/far.py"],
        "ui/page.tsx": ["ui/api.ts"],
    }
    files = {p for p in [*edges, *(h for hs in edges.values() for h in hs)]}
    files |= {"agent/settings.yaml", "migrations/0001.sql", "ios/App.swift"}
    found = areas.agent_scope(entries, edges, files)
    assert found == {
        # Where its prompts, tools and model calls are.
        "agent/prompts.py",
        "agent/tools.py",
        "agent/settings.yaml",
        "agent/run.py",
        # What they import, one step: the second only through a file that is itself the core.
        "agent/util.py",
        "lib/http.py",
    }, "routes, sessions and the app's entry point do not start a scope"
    # No core named: every file, as before.
    assert areas.agent_scope(entries[3:], edges, files) is None


def test_the_scope_keeps_a_packages_front_its_tool_registry_and_helpers_in_its_folders() -> None:
    tools = [Entry("tool", f"t{n}", f"agent/tools/t{n}.py", 1) for n in range(3)]
    entries = [
        *tools,
        Entry("agent", "AgentCache", "server/cache.py", 1, what="class"),
        Entry("agent", "_SUBAGENT_STATES", "server/states.py", 1, what="frozenset"),
        Entry("agent", "PlannerAgent", "agent/planner.py", 1, what="class"),
        Entry("route", "POST /tools", "server/routes.py", 1),
    ]
    edges = {
        "agent/manager.py": [e.path for e in tools],
        "server/routes.py": [e.path for e in tools],
        "agent/tools/t0.py": ["lib/mcp_client.py"],
        "lib/mcp_client.py": ["agent/tools/_schema.py", "lib/far.py"],
        "agent/tools/__init__.py": ["lib/everything.py"],
    }
    files = {p for p in [*edges, *(h for hs in edges.values() for h in hs)]}
    files |= {e.path for e in entries}
    found = areas.agent_scope(entries, edges, files)
    assert found == {
        *(e.path for e in tools),
        "agent/planner.py",
        "lib/mcp_client.py",
        # A helper's import, in a folder of the agent's own.
        "agent/tools/_schema.py",
        # The package's front, not what it imports.
        "agent/tools/__init__.py",
        # What imports three of its tools runs them; a route that does only serves them.
        "agent/manager.py",
    }, "a name that only says agent starts nothing"


def test_a_folder_with_one_subfolder_is_split_below_it() -> None:
    sizes = {f"server/pkg/{c}/m{k}.py": 10 for c in "abcdefgh" for k in range(5)}
    parts = areas.partition(sizes)
    assert [a.folders for a in parts] == [(f"server/pkg/{c}",) for c in "abcdefgh"]
    assert [a.id for a in parts] == [f"server-pkg-{c}" for c in "abcdefgh"]
    # A file of its own at a level stops the descent there: it is an area with the rest.
    with_top = areas.partition({**sizes, "server/main.py": 10})
    assert sorted(f for a in with_top for f in a.files) == sorted([*sizes, "server/main.py"])
    assert len(with_top) > 1


def test_an_outside_area_never_takes_the_name_of_an_area_learned_in_pieces() -> None:
    pieces = [areas.Area("server.1", ("server",), ("server/a.py",), 0, (), "server")]
    pieces.append(areas.Area("server.2", ("server",), ("server/b.py",), 0, (), "server"))
    taken = {a.id for a in pieces}
    beyond = areas.outside(["server.py", "server/x.py", "web/a.ts"], taken)
    assert [a.id for a in beyond] == ["root", "server~outside", "web"]


def test_an_area_of_prompts_tools_and_agents_weighs_more_than_one_of_plumbing() -> None:
    core = [Entry("prompt", "SYSTEM", "a.py", 1), Entry("tool", "search", "a.py", 2)]
    plumbing = [Entry("state", f"s{n}", "b.py", n) for n in range(5)]
    assert areas.weight(core, {}) > areas.weight(plumbing, {})
    assert areas.weight(core, {core[1].id: 4}) == areas.weight(core, {}) + 4, "uses add as before"
    assert areas.weight([], {}) == 0


#: A repository where the agent is a small part: a web client of 30 files calling its own
#: ``api.get``, a mobile project and migrations.
LARGE = {
    **{
        f"ui/components/c{n:02}.ts": (
            'import { api } from "../lib/api";\n'
            f'export const load{n} = () => api.get("/items/{n}");\n'
        )
        for n in range(30)
    },
    "ui/lib/api.ts": "export const api = { get: (p: string) => fetch(p) };\n",
    **{f"ios/App/View{n}.swift": f"struct View{n} {{}}\n" for n in range(10)},
    **{f"migrations/{n:04}_t.sql": f"CREATE TABLE t{n} (id int);\n" for n in range(10)},
}


async def test_a_repository_with_a_large_ui_learns_the_agent_alone(tmp_path) -> None:
    agent, _repo, sent, _script = _learning(tmp_path, extra=LARGE, include=False)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    ran = _area_runs(shown)
    assert ran and not any(a.startswith(("ui", "ios", "migrations")) for a in ran)
    assert not any("ui/components" in str(b["messages"][0]) for b in sent["areas"])
    states = {a["id"]: a["state"] for a in understanding.progress(agent.targets, "bot")["areas"]}
    assert {"ui", "ios", "migrations"} <= {a for a, s in states.items() if s == "outside"}
    said = shown["messages"][-1]["text"]
    assert "Not part of the agent (learned only when asked): ui" in said
    assert "Not part of the agent" in said and "ui" in said.split("Not part of the agent")[1]
    assert (await learn.area_state(agent.targets, "bot", "ui"))["state"] == "outside"
    # Asked for, it is learned like any other.
    await agent.learn_area(sid, "ios")
    await agent.learns.wait("bot")
    assert _area_runs(agent_module.public(agent.learns.get("bot").session)) == ["ios"]


async def test_a_first_learn_runs_at_most_so_many_areas_and_the_next_does_the_rest(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(learn, "MAX_LEARNED", 2)
    agent, _repo, _sent, _script = _learning(tmp_path)
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    first = _area_runs(shown)
    assert len(first) == 2
    said = shown["messages"][-1]["text"]
    assert "1 more area not learned yet (at most 2 in one go)" in said
    states = {a["id"]: a["state"] for a in understanding.progress(agent.targets, "bot")["areas"]}
    assert sorted(s for s in states.values() if s == "due") == ["due"]
    # The next learn takes the rest, with nothing moved.
    shown = await _learn(agent, sid)
    rest = _area_runs(shown)
    assert len(rest) == 1 and rest[0] not in first


# C12: a failed area is named by its folder and says why; a title is plain text.


def test_a_title_is_plain_text_and_a_failed_area_has_a_short_one() -> None:
    lib = areas.Area("lib", ("lib",), ("lib/a.py",), 0)
    assert area_agents._titled(lib, "Memory &amp;amp; recall") == "Memory & recall"
    assert area_agents.plain("Tools &amp; state") == "Tools & state"
    deep = areas.Area("x", ("server/evalution/writer", "server/evalution/project"), ("a",), 0)
    assert area_agents.short_title(deep) == "writer, project"
    piece = areas.Area("t.2", ("agents",), ("agents/t.yaml",), 0, (), "agents", "part 2 of 4: x")
    assert area_agents.short_title(piece) == "agents, part 2 of 4"
    top = areas.Area("root", ("",), ("a.py",), 0)
    assert area_agents.short_title(top) == "top folder"


async def test_a_failed_area_is_titled_by_its_folder_with_its_reason(tmp_path) -> None:
    agent, _repo, _sent, _script = _learning(tmp_path, fail=frozenset({"lib"}))
    sid = (await agent.create("bot", "main"))["id"]
    await _learn(agent, sid)
    lib = next(a for a in understanding.progress(agent.targets, "bot")["areas"] if a["id"] == "lib")
    assert lib["state"] == "failed" and lib["title"] == "lib"
    assert lib["reason"] == "out of budget" or lib["reason"].startswith("no section after")


# C3: a refresh, or any save, writes into the area's own section, so the next learn keeps it.


async def test_a_refreshed_claim_survives_the_next_learn(tmp_path) -> None:
    agent, repo, _sent, _script = _learning(tmp_path)
    targets = agent.targets
    sid = (await agent.create("bot", "main"))["id"]
    await _learn(agent, sid)
    doc = understanding.load(targets, "bot")
    root = next(s for s in doc["sections"] if s["id"] == f"{understanding.AREA_PREFIX}root")
    root["claims"][0] = {**root["claims"][0], "text": "Refreshed: it greets twice."}
    understanding.save(targets, "bot", doc, by="refresh")
    # Another area moves; root does not, and is not learned again.
    (repo / "lib/memory.py").write_text(APP["lib/memory.py"] + "\nLIMIT = 3\n")
    _git(repo, "commit", "-qam", "lib")
    shown = await _learn(agent, sid)
    assert _area_runs(shown) == ["lib"]
    after = understanding.load(targets, "bot")
    root = next(s for s in after["sections"] if s["id"] == f"{understanding.AREA_PREFIX}root")
    assert root["claims"][0]["text"] == "Refreshed: it greets twice."


# C4: the call that asks for names left out spends from the area's budget, kept back for it.


def test_the_names_left_out_are_asked_from_a_part_of_the_budget_kept_for_them() -> None:
    budget = area_agents.Budget.of(1)
    assert budget.reserve == area_agents.MISSING_RESERVE
    budget.left = area_agents.MISSING_RESERVE + 1_000
    budget.last = 900
    assert not budget.out()
    budget.last = 1_100
    assert budget.out(), "the steps stop before they reach what is kept back"
    assert budget.left > area_agents.MISSING_RESERVE


# C6: what the watch left out is said, in the reply and on the page.


async def test_files_past_the_watch_cap_are_said(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(code_module, "HASH_CAP", 4)
    monkeypatch.setattr(learn, "HASH_CAP", 4)
    agent, repo, _sent, _script = _learning(tmp_path)
    (repo / "big.py").write_text("x = 1\n" * 200_000)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "big")
    sid = (await agent.create("bot", "main"))["id"]
    shown = await _learn(agent, sid)
    said = shown["messages"][-1]["text"]
    assert "were not watched (at most 4 are)" in said and "over 1 MB were not read" in said
    left = understanding.progress(agent.targets, "bot")["left_out"]
    assert left["past_cap"] > 0 and left["too_large"] == 1


# C5: the profile the writer carries holds the areas the conversation names, in about 8k tokens.


def test_the_writers_profile_holds_the_areas_the_conversation_names() -> None:
    sections = [_area(n, 3, 20) for n in range(3)]
    sections[1]["claims"][0]["text"] = "Calls refund_order with the order id."
    doc = _doc(sections)
    nodes = [
        {
            "id": "shop",
            "kind": "tools",
            "title": "Shop",
            "sub": "",
            "tools": ["lookup_size"],
            "evidence": [{"kind": "code", "ref": "a2/f.py"}],
        }
    ]
    doc["nodes"] = nodes
    named = understanding.only_named(doc, "Write a test that refunds with refund_order.")
    claims = {s["id"]: len(s["claims"]) for s in named["sections"]}
    assert claims == {"what_it_is": 0, "area-a0": 0, "area-a1": 3, "area-a2": 0}
    assert understanding.ASK_FOR_IT in named["sections"][1]["title"]
    # A node's tool names the area whose files it rests on; so does a file.
    by_tool = understanding.only_named(doc, "check lookup_size")
    assert [len(s["claims"]) for s in by_tool["sections"]][1:] == [0, 0, 3]
    by_file = understanding.only_named(doc, "what does a0/f.py do?")
    assert [len(s["claims"]) for s in by_file["sections"]][1:] == [3, 0, 0]


def test_the_writers_profile_is_capped(tmp_path, monkeypatch) -> None:
    from test_profile_versions import WRITTEN

    from evalution.config import load
    from evalution.targets import Targets

    (tmp_path / "evalution.yaml").write_text(
        "targets:\n  bot:\n    adapter: http\n    http: {send: {url: x}}\n"
    )
    targets = Targets(load(tmp_path / "evalution.yaml"))
    big = {**WRITTEN, "nodes": WRITTEN["nodes"][:2], "starters": [], "step_labels": {}}
    big["sections"] = [*WRITTEN["sections"], *(_area(n, 15, 390) for n in range(12))]
    big["expected_paths"] = []
    written, problems = understanding.check(big, {"search"}, lambda _p, _n: True, set())
    assert problems == []
    understanding.save(targets, "bot", understanding.build(written, {}))
    text = understanding.writer_profile(targets, "bot")
    assert len(text) <= understanding.WRITER_CHARS < len(profile.read_profile(targets, "bot"))
    assert "Left out of this profile to fit" in text


def test_the_writer_is_given_the_claims_of_the_areas_its_conversation_names(tmp_path) -> None:
    from test_profile_versions import WRITTEN

    from evalution.config import load
    from evalution.runs.runner import Runs
    from evalution.targets import Targets
    from evalution.writer.agent import Agent
    from evalution.writer.sessions import SessionStore
    from evalution.writer.tools import AgentTurn, Tools, said_lately

    (tmp_path / "evalution.yaml").write_text(
        "targets:\n  bot:\n    adapter: http\n    http: {send: {url: x}}\n"
    )
    runs = Runs(Targets(load(tmp_path / "evalution.yaml")), None)
    doc = {**WRITTEN, "nodes": WRITTEN["nodes"][:2], "starters": [], "step_labels": {}}
    doc["sections"] = [*WRITTEN["sections"], _area(0, 2, 20), _area(1, 2, 20)]
    doc["expected_paths"] = []
    written, problems = understanding.check(doc, {"search"}, lambda _p, _n: True, set())
    assert problems == []
    understanding.save(runs.targets, "bot", understanding.build(written, {}))
    session = {
        "target": "bot",
        "set": "main",
        "messages": [{"role": "person", "text": "What does a1/f.py do when it refunds?"}],
    }
    assert said_lately(session) == "What does a1/f.py do when it refunds?"
    assert said_lately({"messages": []}) is None
    said = Agent(runs, SessionStore(runs.targets.docs), None)._instructions(session)
    assert "Claim 0 of area 1" in said and "Claim 0 of area 0" not in said
    assert understanding.ASK_FOR_IT in said
    tools = Tools(runs, None)  # type: ignore[arg-type]
    read = asyncio.run(tools.read_profile(AgentTurn(session), {}))
    assert "Claim 0 of area 1" in read and "Claim 0 of area 0" not in read
