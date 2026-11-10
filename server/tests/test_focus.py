"""A platform that hosts many agents (next-level C7, C11): connecting asks which one is tested,
the answer is kept as ``code.focus``, and the learn's scope and the skeleton page start from it.
No model anywhere."""

import time
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import CodeConfig, ConfigError, load
from evalution.connect import discover
from evalution.project import areas, focus
from evalution.project.skeleton import Entry, Skeleton
from evalution.project.skeleton_view import shown_for

NAMES = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")


def _platform(folder, names=NAMES):
    """A repository hosting one agent per name, each in its own settings file: its prompt, its
    own search tool and a tool they all share (beta one more). Alpha's tools are code, which
    imports a helper that imports another; a web client no agent reaches sits beside them."""
    (folder / "agents").mkdir(parents=True)
    (folder / "tools").mkdir()
    (folder / "lib").mkdir()
    (folder / "web").mkdir()
    for name in names:
        (folder / "agents" / f"{name}.yaml").write_text(
            f"name: {name}\nmodel: claude-x\nsystem_prompt: |\n"
            f"  You are the {name} agent, you help people with {name} things every day.\n"
            f"tools:\n  - search_{name}\n  - shared\n"
            + ("  - write_file\n" if name == "beta" else "")
        )
    (folder / "tools" / "common.py").write_text(
        "from langchain.tools import tool\n\nfrom lib.http import fetch\n\n\n"
        '@tool\ndef search_alpha(q: str) -> str:\n    """Search."""\n    return fetch(q)\n\n\n'
        '@tool\ndef shared(q: str) -> str:\n    """Shared."""\n    return q\n'
    )
    (folder / "lib" / "__init__.py").write_text("")
    (folder / "lib" / "http.py").write_text(
        "from lib.retry import again\n\n\ndef fetch(q):\n    return again(q)\n"
    )
    (folder / "lib" / "retry.py").write_text("def again(q):\n    return q\n")
    (folder / "web" / "client.ts").write_text("export const get = (p: string) => fetch(p);\n")
    return folder


def _prompt_id(found: Skeleton, name: str) -> str:
    return next(e.id for e in found.of("prompt") if e.name.startswith(f"{name} "))


# Which agents there are, and in what order they're offered


def test_six_agents_are_offered_most_used_first_then_by_tools(tmp_path) -> None:
    found, edges = focus.of_folder(_platform(tmp_path / "app"))
    assert sorted(a.name for a in focus.agents(found.entries)) == sorted(NAMES)
    assert focus.asks(found.entries)
    uses = {_prompt_id(found, "zeta"): 7, _prompt_id(found, "gamma"): 2}
    picked = focus.choices(found.entries, edges, uses)
    assert [c["value"] for c in picked] == [
        "zeta",
        "gamma",
        # No use: by how many tools each reaches, then by name.
        "beta",
        "alpha",
        "delta",
        "epsilon",
        focus.ALL,
    ]
    zeta = picked[0]
    assert zeta["label"] == "zeta" and zeta["where"] == "agents/zeta.yaml:1"
    assert zeta["uses"] == 7 and zeta["hint"] == "agents/zeta.yaml:1 · 7 uses · 2 tools"
    assert picked[-1]["label"] == "All of them"
    # Without traces, no use is said.
    assert all(c["uses"] is None for c in focus.choices(found.entries, edges, None))


def test_three_agents_or_fewer_are_not_asked_about(tmp_path) -> None:
    found, _ = focus.of_folder(_platform(tmp_path / "app", NAMES[:3]))
    assert len(focus.agents(found.entries)) == 3
    assert not focus.asks(found.entries)


# The learn's scope


def test_a_focus_narrows_the_scope_to_that_agent_and_what_it_reaches(tmp_path) -> None:
    found, edges = focus.of_folder(_platform(tmp_path / "app"))
    files = [*found.prints]
    every = areas.agent_scope(found.entries, edges, files)
    assert every is not None
    assert {f"agents/{n}.yaml" for n in NAMES} <= every
    assert "web/client.ts" not in every

    alpha = areas.agent_scope(found.entries, edges, files, "alpha")
    # One step through imports: lib/retry.py is a second, into a folder holding no core file.
    assert alpha == {"agents/alpha.yaml", "tools/common.py", "lib/http.py"}
    beta = areas.agent_scope(found.entries, edges, files, "beta")
    # Beta names the shared tool, which is defined with alpha's: that file and what it imports.
    assert beta == {"agents/beta.yaml", "tools/common.py", "lib/http.py"}


@pytest.mark.parametrize("answer", [None, "", focus.ALL, "nobody"])
def test_all_of_them_or_an_unknown_focus_leaves_the_scope_as_today(tmp_path, answer) -> None:
    found, edges = focus.of_folder(_platform(tmp_path / "app"))
    files = [*found.prints]
    assert areas.agent_scope(found.entries, edges, files, answer) == areas.agent_scope(
        found.entries, edges, files
    )
    assert focus.chosen(answer, found.entries) is None
    assert focus.chosen("gamma", found.entries) == "gamma"


def test_a_focus_keeps_its_scope_without_other_agents_reached_by_imports() -> None:
    entries = [
        # Agents that are one (defined, not only named so): only those start a scope.
        Entry("agent", "planner", "agents/planner.py", 3, what="definition"),
        Entry("agent", "writer", "agents/writer.py", 3, what="definition"),
        Entry("route", "POST /chat", "server/routes.py", 5),
    ]
    edges = {
        "agents/planner.py": ["agents/registry.py"],
        "agents/registry.py": ["agents/writer.py"],
        "server/routes.py": ["agents/planner.py"],
    }
    files = [*edges, "agents/writer.py"]
    # The registry it imports is in, the writer it reaches through it is not; the route that
    # serves it starts nothing (plumbing).
    assert areas.agent_scope(entries, edges, files, "planner") == {
        "agents/planner.py",
        "agents/registry.py",
    }


# What the pages show


def test_the_skeleton_page_lists_only_the_agent_tested(tmp_path) -> None:
    found, edges = focus.of_folder(_platform(tmp_path / "app"))
    scope = areas.agent_scope(found.entries, edges, [*found.prints], "gamma")
    page = shown_for(found, None, "gamma", scope)
    assert [e["name"] for e in page["entries"] if e["kind"] == "agent"] == ["gamma"]
    assert page["focus"] == "gamma" and page["outside_agents"] == 5
    assert not any(e["path"] == "agents/beta.yaml" for e in page["entries"])
    whole = shown_for(found, None, None, None)
    assert whole["outside_agents"] == 0
    assert len([e for e in whole["entries"] if e["kind"] == "agent"]) == 6


def test_the_diagram_draws_only_the_agent_tested() -> None:
    nodes = [
        {"id": "person", "kind": "person", "label": "Person"},
        {"id": "gamma", "kind": "agent", "label": "gamma"},
        {"id": "beta", "kind": "agent", "label": "beta"},
        {"id": "zeta", "kind": "agent", "label": "zeta"},
        {"id": "tools", "kind": "tools", "label": "Tools"},
    ]
    kept, left = focus.limit_nodes(nodes, "gamma")
    assert [n["id"] for n in kept] == ["person", "gamma", "tools"] and left == 2
    assert focus.limit_nodes(nodes, None) == (nodes, 0)
    assert focus.limit_nodes(nodes, "nobody") == (nodes, 0)


def test_the_learn_starts_its_scope_from_the_target_focus(tmp_path) -> None:
    """``writer.learn.scope_of`` hands the target's ``code.focus`` to :func:`areas.agent_scope`."""
    from evalution.writer.learn import scope_of

    folder = _platform(tmp_path / "app")
    found, edges = focus.of_folder(folder)
    cmap = SimpleNamespace(files=list(found.prints), imports=edges, defs={})

    def targets(code: CodeConfig) -> SimpleNamespace:
        return SimpleNamespace(conf=lambda name: SimpleNamespace(code=code))

    alpha = scope_of(targets(CodeConfig(path=folder, focus="alpha")), "app", cmap, found)
    # One step through imports: lib/retry.py is a second, into a folder holding no core file.
    assert alpha == {"agents/alpha.yaml", "tools/common.py", "lib/http.py"}
    every = scope_of(targets(CodeConfig(path=folder)), "app", cmap, found)
    assert every is not None and {f"agents/{n}.yaml" for n in NAMES} <= every


# Connecting


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def check(conf, overlay, store):
        return {
            "adapter": {"ok": True, "detail": ""},
            "phoenix": {"ok": False, "detail": "", "sessions_seen": 0, "reached": True},
            "code": {"ok": True, "detail": "", "files_matched": 6},
        }

    monkeypatch.setattr(discover.connect, "check", check)
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {}}))
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as made:
        yield made


def _discover(client, folder):
    started = client.post("/v1/targets/discover", json={"path": str(folder)})
    assert started.status_code == 202, started.text
    deadline = time.time() + 20
    while (found := client.get(f"/v1/targets/discover/{started.json()['id']}").json())[
        "status"
    ] == "working":
        assert time.time() < deadline
        time.sleep(0.02)
    return found


def test_connecting_a_platform_asks_which_agent_in_use_order(client, tmp_path, monkeypatch):
    async def agent_uses(self, job, found):
        return {_prompt_id(found, "delta"): 4, _prompt_id(found, "alpha"): 1}

    monkeypatch.setattr(discover.Discoveries, "agent_uses", agent_uses)
    found = _discover(client, _platform(tmp_path / "platform"))
    assert found["status"] == "ready", found
    asked = {q["key"]: q for q in found["questions"]}
    question = asked["focus"]
    assert question["text"] == "Which agent do you want to test?"
    values = [c["value"] for c in question["choices"]]
    assert values[:2] == ["delta", "alpha"] and values[-1] == focus.ALL
    assert {c["value"] for c in question["choices"]} == {*NAMES, focus.ALL}
    assert all({"value", "label", "hint"} <= set(c) for c in question["choices"])
    # A text question keeps its shape.
    assert "choices" not in asked["chat"]
    assert any(s["text"] == "Found 6 agents in the code" for s in found["steps"])

    made = client.post(
        f"/v1/targets/discover/{found['id']}/accept",
        json={"answers": {"chat": "/chat", "focus": "gamma"}},
    )
    assert made.status_code == 201, made.text
    assert made.json()["code"]["focus"] == "gamma"
    name = made.json()["name"]
    assert load(tmp_path / "evalution.yaml").targets[name].code.focus == "gamma"

    # Changed later, as any code setting: a body without it keeps it; null clears it.
    body = {**client.get(f"/v1/targets/{name}").json(), "secrets": {}}
    body["code"] = {k: v for k, v in body["code"].items() if k != "focus"}
    kept = client.put(f"/v1/targets/{name}", json=body)
    assert kept.status_code == 200, kept.text
    assert kept.json()["code"]["focus"] == "gamma"
    body["code"]["focus"] = "beta"
    assert client.put(f"/v1/targets/{name}", json=body).json()["code"]["focus"] == "beta"

    agents = client.get(f"/v1/targets/{name}/agents")
    assert agents.status_code == 200, agents.text
    assert agents.json()["focus"] == "beta" and agents.json()["asks"]
    assert {c["value"] for c in agents.json()["choices"]} == {*NAMES, focus.ALL}

    body["code"]["focus"] = None
    cleared = client.put(f"/v1/targets/{name}", json=body).json()
    assert cleared["code"]["focus"] is None
    assert client.get(f"/v1/targets/{name}/agents").json()["focus"] is None


def test_all_of_them_or_no_answer_keeps_every_agent(client, tmp_path):
    found = _discover(client, _platform(tmp_path / "platform"))
    assert "focus" in {q["key"] for q in found["questions"]}
    made = client.post(
        f"/v1/targets/discover/{found['id']}/accept",
        json={"answers": {"chat": "/chat", "focus": focus.ALL}},
    )
    assert made.status_code == 201, made.text
    assert made.json()["code"]["focus"] is None

    again = _discover(client, _platform(tmp_path / "other"))
    # The focus question has a default: left out, it's every agent.
    made = client.post(
        f"/v1/targets/discover/{again['id']}/accept", json={"answers": {"chat": "/chat"}}
    )
    assert made.status_code == 201, made.text
    assert made.json()["code"]["focus"] is None


def test_three_agents_connect_without_the_question(client, tmp_path):
    found = _discover(client, _platform(tmp_path / "small", NAMES[:3]))
    assert found["status"] == "ready"
    assert "focus" not in {q["key"] for q in found["questions"]}
    assert not any("agents in the code" in s["text"] for s in found["steps"])


def test_code_focus_must_be_a_name(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    for bad in (3, "", "  "):
        block = {"adapter": "http", "http": {"send": {"url": "x"}}}
        block["code"] = {"path": "app", "focus": bad}
        (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
        with pytest.raises(ConfigError, match=r"code\.focus"):
            load(tmp_path / "evalution.yaml")


def test_a_save_from_the_page_keeps_code_learn_and_focus(client, tmp_path):
    found = _discover(client, _platform(tmp_path / "platform"))
    made = client.post(
        f"/v1/targets/discover/{found['id']}/accept",
        json={"answers": {"chat": "/chat", "focus": "gamma"}},
    )
    assert made.status_code == 201, made.text
    name = made.json()["name"]
    body = {**client.get(f"/v1/targets/{name}").json(), "secrets": {}}
    assert body["code"]["learn"] == "on_demand"

    body["code"]["learn"] = "full"
    saved = client.put(f"/v1/targets/{name}", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["code"]["learn"] == "full"
    assert load(tmp_path / "evalution.yaml").targets[name].code.learn == "full"

    # The page sends back what it read: both carried through a save.
    again = client.put(f"/v1/targets/{name}", json={**saved.json(), "secrets": {}}).json()
    assert again["code"]["learn"] == "full" and again["code"]["focus"] == "gamma"
    # A body that leaves them out keeps them too.
    body["code"] = {k: v for k, v in body["code"].items() if k not in ("learn", "focus")}
    kept = client.put(f"/v1/targets/{name}", json=body).json()
    assert kept["code"]["learn"] == "full" and kept["code"]["focus"] == "gamma"
    body["code"]["learn"] = "sometimes"
    assert client.put(f"/v1/targets/{name}", json=body).status_code in (400, 422)
