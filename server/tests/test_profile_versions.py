"""The understanding is honest (evidence on every node and claim, tools only from traces or
cited code), computed from traces and runs, steered by the admin, and the profile it renders
is versioned."""

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load
from evalution.project import library_fit, understanding
from evalution.project.profile import profile_versions, profile_view, write_json, write_profile
from evalution.results.store import FileStore
from evalution.runs.runner import Runs
from evalution.targets import Targets

LIBRARY = Path(__file__).resolve().parents[2] / "library"
TRACES = [{"kind": "traces", "ref": "9 asks"}]
WRITTEN = {
    "one_line": "Sells shoes and remembers your size.",
    "nodes": [
        {"id": "you", "kind": "person", "title": "You", "evidence": TRACES},
        {"id": "bot", "kind": "agent", "title": "Shop bot", "sub": "answers", "evidence": TRACES},
        {
            "id": "shop",
            "kind": "tools",
            "title": "Shop tools",
            "tools": ["search", "refund"],
            "evidence": [{"kind": "code", "ref": "tools/refund.py"}],
        },
        {"id": "helper", "kind": "worker", "title": "Helper", "evidence": TRACES},
    ],
    "sections": [
        {
            "id": "what_it_is",
            "title": "What it is",
            "claims": [
                {"id": "c1", "text": "Keeps a memory of your size.", "evidence": TRACES},
                {"id": "c2", "text": "Answers in one line.", "evidence": TRACES},
            ],
        }
    ],
    "priorities": [{"id": "p1", "title": "Remembering sizes"}],
    "starters": [
        {
            "id": "s1",
            "title": "Recalls the size",
            "what": "Say a size, ask later",
            "why": "It's the point",
            "usd_per_try": 0.3,
            "priority": "p1",
            "library": "memory-recalls-fact",
        }
    ],
    "step_labels": {"search": "Searches", "refund": "Refunds"},
    "expected_paths": [["Refunds"]],
}
DIGEST = {
    "asks": 4,
    "tools": {
        "search": {"calls": 3, "offered": True},
        "teleport": {"calls": 0, "offered": True},
    },
    "typical": {"seconds": {"median": 31.0}, "usd": {"median": 0.04}},
    "per_ask": [
        {"tools": ["search", "search"], "all": ["search"], "delegated": False},
        {"tools": ["search"], "all": ["search"], "delegated": True},
        {"tools": [], "all": [], "delegated": False},
        {"tools": ["search"], "all": ["search"], "delegated": False},
    ],
}


TRY = {"tools": 1.0, "reply": 1.0, "seconds": 1.0, "tokens": 10, "usd": 0.1}


def _in_code(path: str, name: str) -> bool:
    return path == "tools/refund.py" and name == "refund"


def _write(targets: Targets, body: dict) -> None:
    written, problems = understanding.check(body, {"search"}, _in_code, {"memory-recalls-fact"})
    assert problems == []
    understanding.save(targets, "bot", understanding.build(written, DIGEST))


def test_claims_and_nodes_need_evidence_and_known_tools() -> None:
    bad = json.loads(json.dumps(WRITTEN))
    bad["sections"][0]["claims"][0]["evidence"] = []
    bad["nodes"][2]["tools"].append("teleport")
    bad["starters"][0]["library"] = "nope"
    _written, problems = understanding.check(bad, {"search"}, _in_code, {"memory-recalls-fact"})
    assert problems == [
        "nodes[2]: teleport is not in the traces' tools nor in tools/refund.py",
        "sections[0].claims[0]: needs evidence (where you saw it)",
        "starters[0]: no library test nope",
    ]


def test_paths_share_the_asks_and_add_expected_ones() -> None:
    found = understanding.paths(DIGEST["per_ask"], WRITTEN["step_labels"], [["Refunds"]])
    assert found == [
        {"share": 0.75, "steps": ["Searches"], "seen": True},
        {"share": 0.25, "steps": ["Answers directly"], "seen": True},
        {"share": 0.0, "steps": ["Refunds"], "seen": False},
    ]


@pytest.fixture
def target(tmp_path) -> Path:
    code = tmp_path / "app"
    (code / "tools").mkdir(parents=True)
    (code / "pyproject.toml").write_text('dependencies = ["fastapi", "anthropic"]')
    (code / "web").mkdir()
    (code / "web" / "package.json").write_text(json.dumps({"dependencies": {"react": "19"}}))
    (code / "tools" / "refund.py").write_text("def refund(): ...")
    bot = {
        "adapter": "http",
        "http": {"send": {"url": "x"}},
        # An include from before everything was watched still loads, and is not read.
        "code": {"path": "app", "include": ["**/*prompt*", "tools/**"]},
    }
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"library_dir": str(LIBRARY), "targets": {"bot": bot}})
    )
    main = tmp_path / "sets" / "bot" / "main"
    main.mkdir(parents=True)
    (main / "set.json").write_text(json.dumps({"name": "Main"}))
    for stem, source in (("greets", None), ("recalls", "library: memory-recalls-fact")):
        case = {"name": stem.title(), "turns": [{"message": "hi"}]}
        (main / f"{stem}.case.json").write_text(json.dumps({**case, "source": source}))
    config = load(tmp_path / "evalution.yaml")
    FileStore(config.data_dir).write(
        {
            "run": "r1",
            "target": "bot",
            "set": "main",
            "started_at": 1.0,
            "status": "done",
            "cases": {"greets": {"name": "Greets", "tries": [{"passed": True, **TRY}]}},
        }
    )
    _write(Targets(config), WRITTEN)
    return tmp_path


def test_numbers_come_from_the_digest_runs_and_library(target) -> None:
    config = load(target / "evalution.yaml")
    targets = Targets(config)
    found = understanding.view(targets, Runs(targets, FileStore(config.data_dir), None), "bot")
    rows = library_fit.for_target(targets, "bot")
    assert found["card"]["numbers"] == {
        "tools": 3,
        "tools_seen": 1,
        "state_kinds": ["memory"],
        "ask_seconds": 31.0,
        "ask_usd": 0.04,
        "tests": 2,
        "tests_passing": 1,
        "library_covered": 1,
        "library_total": sum(r["fit"]["verdict"] != "not_applicable" for r in rows),
    }
    card = found["card"]
    assert card["stack"] == ["Python · FastAPI", "React web", "Claude models", "HTTP adapter"]
    assert card["status"] == "up_to_date" and card["watched"] == ["code"]
    edges = {(e["from"], e["to"]): e["asks"] for e in found["diagram"]["edges"]}
    assert edges == {("you", "bot"): 4, ("bot", "shop"): 3, ("bot", "helper"): 1}
    assert "tools" not in found["diagram"]["nodes"][0]


def test_steers_round_trip_into_the_profile_and_card(target) -> None:
    with TestClient(create_app(load(target / "evalution.yaml"))) as client:
        url = "/v1/targets/bot/understanding"
        assert client.post(
            f"{url}/steer", json={"claim": "c1", "fix": "Keeps shoe sizes."}
        ).is_success
        assert client.post(f"{url}/steer", json={"node": "helper", "verdict": "wrong"}).is_success
        assert client.post(f"{url}/steer", json={"priority": {"title": "Refunds"}}).is_success
        unknown = client.post(f"{url}/steer", json={"claim": "nope", "verdict": "right"})
        assert unknown.status_code == 404
        assert client.post(f"{url}/steer", json={"claim": "c2"}).status_code == 400
        found = client.get(url).json()
        assert found["sections"][0]["claims"][0]["fix"] == "Keeps shoe sizes."
        assert found["diagram"]["nodes"][3]["verdict"] == "wrong"
        assert found["priorities"][-1] == {
            "id": "admin-refunds",
            "title": "Refunds",
            "picked": True,
            "source": "admin",
        }
        profile = (target / "targets" / "bot" / "profile.md").read_text()
        assert "- Keeps shoe sizes. [admin]" in profile
        assert "- Not true, says the admin: **Helper** (worker). [admin]" in profile
        card = client.get("/v1/targets/bot/card.md").text
        assert card.startswith("# bot\n\nSells shoes and remembers your size.")
        assert "- 2 tests, 1 pass" in card and "Watched: code · Up to date" in card

    # Once the agent rewrites the claim, the fix counts as honoured.
    targets = Targets(load(target / "evalution.yaml"))
    rewritten = json.loads(json.dumps(WRITTEN))
    rewritten["sections"][0]["claims"][0]["text"] = "Keeps shoe sizes."
    _write(targets, rewritten)
    claim = understanding.written(targets, "bot")["sections"][0]["claims"][0]
    assert "fix" not in claim and len(understanding.versions(targets, "bot")) == 2


def test_each_write_is_a_version_with_its_changes(tmp_path) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    targets = Targets(load(tmp_path / "evalution.yaml"))
    write_json(targets, "bot", "fingerprint.json", {"taken_at": 1, "tools": {"search": []}})
    write_profile(targets, "bot", "Sells shoes. [admin]\n- `search` [trace]", by="agent")
    write_profile(targets, "bot", "Sells shoes. [admin]\n- `find` [trace]", by="agent")
    view = profile_view(targets, "bot")
    assert view["version"]["version"] == 2 and view["version"]["fingerprint"]
    assert view["changes"] == [
        {"kind": "removed", "text": "- `search` [trace]"},
        {"kind": "added", "text": "- `find` [trace]"},
    ]
    assert len(profile_versions(targets, "bot")) == 2


def test_suggested_tests_mark_what_already_exists(target) -> None:
    targets = Targets(load(target / "evalution.yaml"))
    body = json.loads(json.dumps(WRITTEN))
    more = {"what": "w", "why": "y", "usd_per_try": 0.1, "selected": True}
    body["starters"] += [
        {"id": "s2", "title": "greets", **more},  # the same name as a test
        {"id": "s3", "title": "Refunds once", **more},
    ]
    _write(targets, body)
    with TestClient(create_app(load(target / "evalution.yaml"))) as client:
        url = "/v1/targets/bot/understanding"
        starters = client.get(url).json()["starters"]
        # s1 comes from a library test the set already has.
        assert [(s["id"], s["exists"], s["selected"]) for s in starters] == [
            ("s1", True, False),
            ("s2", True, False),
            ("s3", False, True),
        ]
        refused = client.post(f"{url}/plan", json={"starters": ["s3", "s2"]})
        assert refused.status_code == 409 and refused.json()["detail"] == "Already a test: greets"
