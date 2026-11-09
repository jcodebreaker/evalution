"""A target written in full, its routes on the app's own address rather than ``{url}``, played on
evalution's own copy of the app (ADR 0009) by the real server and runner: every call goes to the
copy the run starts, and none reaches the person's app."""

import json
import sys

import pytest
import yaml
from test_acceptance import MADE_UP_APP, World, _target

from evalution.isolation.plan import Plan, PlannedStore, PlanStore


class CopyWorld(World):
    """The acceptance world, and a second target on the same app, ``full``: the same routes,
    written in full on the app's own port, played on a copy from a plan saved as proven."""

    def start(self) -> None:
        if not getattr(self, "copied", False):
            self.copied = True
            self._full_target()
        super().start()

    def _full_target(self) -> None:
        config = yaml.safe_load(self.config.read_text())
        url = f"http://127.0.0.1:{self.app_port}"
        block = _target(self.app_port)
        assert "{url}" not in json.dumps(block)
        config["targets"]["full"] = {**block, "url": url}
        self.config.write_text(yaml.safe_dump(config))
        cases = self.root / "sets" / "full" / "main"
        cases.mkdir(parents=True)
        (cases / "set.json").write_text(json.dumps({"name": "Main"}))
        exact = {
            "name": "Exact answer",
            "turns": [{"message": "What is 6 times 7?"}],
            "reply": {"contains": ["42"]},
            "limits": {"tries": 1, "seconds": 30},
        }
        (cases / "exact-answer.case.json").write_text(json.dumps(exact))
        self.import_sets()
        kept = self.root / "copy-data"
        kept.mkdir()
        plan = Plan(
            target="full",
            stores=[PlannedStore("scratch", "dir", str(kept), strategy="snapshot")],
            launch={
                "kind": "process",
                "command": [sys.executable, "-m", MADE_UP_APP, "{port}"],
                "cwd": str(self.root),
                "env": {"TRACES": f"http://127.0.0.1:{self.port}/v1/traces"},
                "roots": [str(kept)],
                "health": "/admin",
            },
            status="proven",
        )
        PlanStore(self.root / "data").save(plan)


@pytest.fixture
def world(tmp_path):
    made = CopyWorld(tmp_path)
    try:
        yield made
    finally:
        made.close()


def test_a_target_written_in_full_plays_on_the_copy_and_never_on_the_app(world) -> None:
    before = world.admin()["api_calls"]
    asked = {"target": "full", "set": "main", "cases": ["exact-answer"], "tries": 1}
    found = world.client.post("/v1/evals/runs/estimate", json=asked).json()
    assert "token" in found, found
    run = world.client.post("/v1/evals/runs", json={"token": found["token"]}).json()["run"]
    assert world.wait(run)["status"] == "done"
    doc = world.stored(run)
    assert doc["isolation"]["how"] == "proven"
    (attempt,) = doc["cases"]["exact-answer"]["tries"]
    assert attempt["isolation"]["how"] == "proven" and "42" in attempt["reply_text"]
    # The person's app heard nothing: every call went to the copy.
    assert world.admin()["api_calls"] == before
