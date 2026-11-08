"""What the page says of a target's isolation outside the Isolation section: Home's card for
every status, the Run dialog's lines with rough timings, and the stores a try names."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api import isolation_words
from evalution.api.app import create_app
from evalution.config import load
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.storage import Database
from evalution.suite.store import import_sets
from evalution.targets import Targets

#: The stores relay-lite's proven plan names, as the probe found them.
POSTGRES = PlannedStore(
    "postgres-atrium",
    "postgres",
    "eval-relay-lite-0-postgres-1:atrium",
    moved_by=["DATABASE_URL", "ATRIUM_DB_URL"],
    strategy="relocate",
    seen=[
        "holds the canary: eval-relay-lite-0-postgres-1: atrium.engine.conversation_items",
        "holds the canary: eval-relay-lite-0-postgres-1: atrium.engine.round_events",
    ],
    found_as=["eval-relay-lite-0-postgres-1:atrium.engine.conversation_items"],
)
VOLUME = PlannedStore(
    "volume-artifact-data",
    "volume",
    "volume:artifact-data",
    strategy="relocate",
    seen=[
        "holds the canary: eval-relay-lite-0-engine-1: "
        "volume:eval-relay-lite-0_artifact-data/artifacts/memory_index/documents"
    ],
    found_as=["volume:artifact-data"],
)


@pytest.fixture
def config(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "targets": {
                    "relay-lite": {
                        "adapter": "http",
                        "http": {"send": {"url": "{url}/x"}},
                        "code": {"path": "app"},
                    }
                }
            }
        )
    )
    main = tmp_path / "sets" / "relay-lite" / "main"
    main.mkdir(parents=True)
    (main / "set.json").write_text(json.dumps({"name": "Main"}))
    (main / "greets.case.json").write_text(
        json.dumps({"name": "Greets", "turns": [{"message": "hi"}]})
    )
    found = load(tmp_path / "evalution.yaml")
    import_sets(Database.open(found.data_dir).docs(), tmp_path / "sets")
    return found


def _save(config, status="proven", **fields) -> Plan:
    plan = Plan(
        target="relay-lite",
        stores=[POSTGRES, VOLUME],
        status=status,
        report="Drafted.\n\nRehearsed on eval-relay-lite-0 in 41s:\nok  proven",
        **fields,
    )
    PlanStore(config.data_dir).save(plan)
    return plan


def test_stores_are_named_by_what_they_hold_not_by_the_copy() -> None:
    plan = Plan(target="relay-lite", stores=[POSTGRES, VOLUME])
    named = {s["id"]: s for s in isolation_words.store_words(plan)}
    assert named["postgres-atrium"] == {
        "id": "postgres-atrium",
        "kind": "postgres",
        "name": "atrium",
        "holds": "Conversations",
        "reset": True,
    }
    assert named["volume-artifact-data"]["name"] == "artifact-data"
    assert named["volume-artifact-data"]["holds"] == "Memory index"
    # "relay" in the copy's own name says nothing of what a store holds.
    bare = PlannedStore("dir-x", "dir", "/srv/eval-relay-lite-0/x", strategy="snapshot")
    (found,) = isolation_words.store_words(Plan(target="relay-lite", stores=[bare]))
    assert found["holds"] is None and found["name"] == "x"


def test_home_card_says_what_a_proven_plan_resets(config) -> None:
    _save(config, exclude=["atrium.phoenix.spans"])
    card = isolation_words.card(Targets(config), "relay-lite")
    assert card["status"] == "proven" and card["version"] == 1
    assert [s["holds"] for s in card["stores"]] == ["Conversations", "Memory index"]
    assert card["exclude"] == ["atrium.phoenix.spans"]
    assert isinstance(card["updated"], float)
    assert card["stale_files"] == [] and card["can_run"]


def test_home_card_for_each_other_status(config) -> None:
    targets = Targets(config)
    none = isolation_words.card(targets, "relay-lite")
    assert none["status"] == "none" and none["stores"] == []
    assert none["plain"] == "none" and "session id" in (none["note"] or "")

    _save(config, status="failed", leaks=["eval-relay-lite-0-engine-1:/data/cache"])
    failed = isolation_words.card(targets, "relay-lite")
    assert failed["status"] == "failed"
    assert failed["leaks"] == ["eval-relay-lite-0-engine-1:/data/cache"]

    stale = _save(config, status="stale")
    stale.report += "\n\nOut of date: a commit changed db/utils.py."
    PlanStore(config.data_dir).save(stale)
    found = isolation_words.card(targets, "relay-lite")
    assert found["status"] == "stale" and found["stale_files"] == ["db/utils.py"]


def test_a_plan_that_doesnt_read_says_so(config) -> None:
    folder = PlanStore(config.data_dir).root / "relay-lite"
    folder.mkdir(parents=True)
    (folder / "plan.json").write_text('{"version": 99, "target": "relay-lite"}')
    card = isolation_words.card(Targets(config), "relay-lite")
    assert card["status"] == "none" and "doesn't read" in card["problem"]


def _proven_run(start, restores):
    tries = [{"n": i + 1, "isolation": {"how": "proven", "restore_seconds": r}} for i, r in
             enumerate(restores)]  # fmt: skip
    return {
        "isolation": {"how": "proven", "plan_version": 1},
        "copy_start_seconds": start,
        "cases": {"a": {"tries": tries}},
    }


def test_timings_come_from_the_newest_run_on_copies_and_the_rehearsal() -> None:
    plan = Plan(target="t", report="Rehearsed on eval-t-0 in 41s:\nok")
    unmanaged = {"isolation": {"how": "none"}, "cases": {}}
    runs = [unmanaged, _proven_run(18.2, [7.0, 7.4, 6.9]), _proven_run(30.0, [12.0])]
    assert isolation_words.timings(plan, runs) == {
        "start_seconds": 18.2,
        "restore_seconds": 7.0,
        "rehearse_seconds": 41,
    }
    # An older run kept no start time; nothing seen, nothing said.
    older = {**_proven_run(None, []), "copy_start_seconds": None}
    assert isolation_words.timings(None, [older]) == {
        "start_seconds": None,
        "restore_seconds": None,
        "rehearse_seconds": None,
    }


def test_the_estimate_carries_the_plan_and_its_rough_timings(config) -> None:
    _save(config)
    with TestClient(create_app(config)) as client:
        found = client.post(
            "/v1/evals/runs/estimate", json={"target": "relay-lite", "set": "main"}
        ).json()
    isolation = found["isolation"]
    assert isolation["how"] == "proven" and isolation["plan_version"] == 1
    assert isolation["status"] == "proven"
    assert [s["name"] for s in isolation["stores"]] == ["atrium", "artifact-data"]
    # No run on copies yet: the rehearsal's time only.
    assert isolation["start_seconds"] is None and isolation["restore_seconds"] is None
    assert isolation["rehearse_seconds"] == 41
    assert found["tries"] >= 1 and found["chats"] >= 1


def test_the_estimate_without_a_plan_says_none(config) -> None:
    with TestClient(create_app(config)) as client:
        found = client.post(
            "/v1/evals/runs/estimate", json={"target": "relay-lite", "set": "main"}
        ).json()
    assert found["isolation"]["how"] == "none" and found["isolation"]["status"] == "none"
    assert found["isolation"]["stores"] == []


def test_a_try_names_the_plans_stores(config) -> None:
    targets = Targets(config)
    assert isolation_words.try_stores(targets, "relay-lite") == []
    _save(config)
    ids = [s["id"] for s in isolation_words.try_stores(targets, "relay-lite")]
    assert ids == ["postgres-atrium", "volume-artifact-data"]


#: A page map, so the target plays through its page (ADR 0010).
PAGE = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {"in": {"role": "log", "name": "Messages"}},
}


def test_a_page_run_refused_for_a_missing_piece_says_which_with_its_fix(
    config, tmp_path, monkeypatch
) -> None:
    """The Run dialog's refusal names what UI testing lacks, for the page to offer what fixes
    it: Set up testing for a copy, the command for a browser."""
    from evalution.runs.page import target as page_target

    file = tmp_path / "evalution.yaml"
    raw = yaml.safe_load(file.read_text())
    raw["targets"]["relay-lite"]["page"] = PAGE
    file.write_text(yaml.safe_dump(raw))
    found = load(file)
    asked = {"target": "relay-lite", "set": "main"}
    with TestClient(create_app(found)) as client:
        refused = client.post("/v1/evals/runs/estimate", json=asked)
    assert refused.status_code == 400
    body = refused.json()
    assert body["missing"]["part"] == "copy" and body["missing"]["command"] is None
    assert "evalution's own copy" in body["detail"]

    _save(found)
    parts = {"playwright": True, "browser": False, "ffmpeg": True}
    monkeypatch.setattr(page_target, "page_parts", lambda: parts)
    with TestClient(create_app(found)) as client:
        refused = client.post("/v1/evals/runs/estimate", json=asked)
        ready = client.get("/v1/settings/ui-testing").json()
    missing = refused.json()["missing"]
    assert missing["part"] == "browser" and missing["command"].endswith("install chromium")
    assert ready["ready"] is False and ready["missing"] == [missing]
