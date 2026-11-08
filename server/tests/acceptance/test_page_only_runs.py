"""A target reached through the made-up app's page alone (``adapter: page``, ADR 0010 amended),
played by the real server and runner in a real browser, on evalution's own copy of the app: no API
adapter sends, waits or reads anything. With the page as it should be, the try passes on what the
page showed, and every view says it was played through the page only, a reply-only pass. With the
page dropping the reply, the try fails on the page: never the adapter's (there is none), and the
traces add that the agent did answer.

Skipped without the page extra or a browser, as ``test_page_runs.py`` is.
"""

import json
import sys

import pytest
import yaml

pytest.importorskip("playwright", reason="page runs need the page extra: uv sync --extra page")

from test_acceptance import MADE_UP_APP
from test_page_runs import CASE, PAGE, PASSWORD_ENV, PageWorld, _a_browser

from evalution.core.judgement import Outcome
from evalution.core.rules import current, layers
from evalution.isolation.plan import Plan, PlannedStore, PlanStore

if not _a_browser():
    pytest.skip("no browser for page runs", allow_module_level=True)

#: The made-up app's page, reached through it alone: it opens /chat, clicks New chat, and reads
#: the chat's id from its address.
ALONE = {
    **{key: value for key, value in PAGE.items() if key != "open_session"},
    "session_id": {"from": "url", "pattern": r"/chat/(?P<id>[\w-]+)"},
}


class AloneWorld(PageWorld):
    """The page world, and a third target on the same app reached through its page alone."""

    def _page_target(self) -> None:
        super()._page_target()
        config = yaml.safe_load(self.config.read_text())
        config["targets"]["alone"] = {
            "adapter": "page",
            "page": ALONE,
            "page_accounts": [{"email": "person0@made-up.test", "password_env": PASSWORD_ENV}],
        }
        self.config.write_text(yaml.safe_dump(config))
        cases = self.root / "sets" / "alone" / "main"
        cases.mkdir(parents=True, exist_ok=True)
        (cases / "set.json").write_text(json.dumps({"name": "Main"}))
        (cases / "answer-shown.case.json").write_text(json.dumps(CASE))
        self.import_sets()
        self.alone_switches()

    def alone_switches(self, *names: str) -> None:
        """The plan the page-only target's copy starts from, with these switches on."""
        kept = self.root / "alone-data"
        kept.mkdir(exist_ok=True)
        plan = Plan(
            target="alone",
            stores=[PlannedStore("scratch", "dir", str(kept), strategy="snapshot")],
            launch={
                "kind": "process",
                "command": [sys.executable, "-m", MADE_UP_APP, "{port}"],
                "cwd": str(self.root),
                "env": {
                    "TRACES": f"http://127.0.0.1:{self.port}/v1/traces",
                    "PAGE_SWITCHES": ",".join(names),
                },
                "roots": [str(kept)],
                "health": "/admin",
            },
            status="proven",
        )
        PlanStore(self.root / "data").save(plan)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv(PASSWORD_ENV, "made-up-0")
    made = AloneWorld(tmp_path)
    try:
        yield made
    finally:
        made.close()


def _try(doc: dict) -> dict:
    (attempt,) = doc["cases"]["answer-shown"]["tries"]
    return attempt


def _checks(attempt: dict) -> dict[str, str]:
    return {c.id: c.outcome.value for c in current(attempt).checks}


def test_a_target_reached_through_its_page_alone(world) -> None:
    # It says how it plays before anything runs.
    target = world.client.get("/v1/targets/alone").json()
    assert target["adapter_label"] == "Its page only" and target["page"]["only"] is True
    said = {row["what"]: row["where"] for row in target["page"]["map"]}
    assert "Done when" in said and said["Session id"].startswith("the page's address")

    # As it should be: the page's reply passes, and every view says it is the page's alone.
    doc = world.play("alone")
    assert doc["status"] == "done", doc.get("error")
    assert doc["plays"] == "page_only" and doc["isolation"]["how"] == "proven"
    attempt = _try(doc)
    found = current(attempt)
    assert found.outcome is Outcome.PASSED, _checks(attempt)
    assert layers(found) == {"stored": "absent", "answered": "pass", "shown": "pass"}
    (evidence,) = world.evidence(doc["run"])
    assert evidence.page is not None and evidence.page.page_only
    (turn,) = evidence.turns
    assert turn.shown is not None and turn.reply.text == turn.shown.text and "42" in turn.reply.text
    assert evidence.spans, "the spans were found by the chat's id and the try's headers"
    summary = world.client.get(f"/v1/evals/runs/{doc['run']}").json()
    assert summary["plays"] == "page_only"
    view = world.client.get(f"/v1/evals/runs/{doc['run']}/cases/answer-shown/compare").json()
    assert view["page"]["page_only"] is True and view["page"]["without_page"] is None

    # The reply never shows: the try fails on the page, never the adapter's, and the traces say
    # the agent did answer.
    world.alone_switches("drop_reply")
    doc = world.play("alone")
    assert doc["status"] == "done", doc.get("error")
    attempt = _try(doc)
    found = current(attempt)
    assert found.outcome is Outcome.FAILED, _checks(attempt)
    assert _checks(attempt)["shown:1"] == "failed"
    assert not any(c.unjudged is not None for c in found.checks if c.role.value == "must")
    (evidence,) = world.evidence(doc["run"])
    assert evidence.failure is not None and evidence.failure.value == "app"
    assert "the page showed no reply" in (evidence.error or "")
    assert "answered, not shown" in (evidence.error or ""), evidence.error
