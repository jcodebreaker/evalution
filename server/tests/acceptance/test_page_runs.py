"""A test played through the made-up app's page by the real server and runner, in a real browser
(ADR 0010): on evalution's own copy of the app, which the run starts from a proven plan, signed in
with the slot's test account. With the page as it should be, every layer holds and the try keeps a
screenshot, and its video is deleted once it passes. Each switch that breaks the page gives the
result it should: a reply that never showed is shown ✕ while stored and answered hold, the try keeps
its video and the page can play it, and the API alone passes; an error drawing a reply is the
page's health, with the TypeError in the console; a renamed Send is a broken page map, which ends
the run after one try.

Skipped without the page extra or a browser: ``uv sync --extra page``, and Google Chrome (or
``EVALUTION_CHROME``) or ``playwright install chromium``.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("playwright", reason="page runs need the page extra: uv sync --extra page")

from test_acceptance import HERE, World, _target

from evalution.config import load
from evalution.core.evidence import SHOTS_OFF, VIDEO, VIDEO_ON_FAILURE, is_shot
from evalution.core.judgement import Outcome
from evalution.core.rules import PAGE_MAP_BROKEN, current, layers
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.project.pagemaps import KeptMap, PageMapStore
from evalution.results.store import RunStore
from evalution.runs.page import learn
from evalution.runs.page.map import PageMap
from evalution.runs.page.target import NO_VIDEO, system_chrome
from evalution.storage import Database
from evalution.targets import Targets


def _a_browser() -> bool:
    if system_chrome() is not None:
        return True
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).is_file()
    except Exception:
        return False


if not _a_browser():
    pytest.skip(
        "no browser for page runs: install Google Chrome, set EVALUTION_CHROME, or run "
        "`playwright install chromium`",
        allow_module_level=True,
    )

PASSWORD_ENV = "MADE_UP_PASSWORD_0"
#: The made-up app's page, as its module's docstring names what is on it.
PAGE = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "sign_in": {
        "form": {
            "url": "{copy_page}/login",
            "fill": [
                {"role": "textbox", "name": "Email", "value": "{account.email}"},
                {"role": "textbox", "name": "Password", "value": "{account.password}"},
            ],
            "submit": {"role": "button", "name": "Sign in"},
            "done": {"url_matches": "/chat"},
        }
    },
    "accounts": "seed",
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "new_chat": {"click": {"role": "button", "name": "New chat"}},
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {
        "in": {"role": "log", "name": "Messages"},
        "item": {"role": "article"},
        "mine": {"attr": "data-role", "equals": "assistant"},
    },
    "busy": [{"role": "button", "name": "Stop"}, {"attr": "aria-busy", "equals": "true"}],
    "catch_up_seconds": 10,
}
#: The page's checks decide, so the API alone passing and the page failing read apart.
CASE = {
    "name": "Answer shown",
    "turns": [{"message": "What is 6 times 7?"}],
    "reply": {"contains": ["42"]},
    "page": {"decides": True},
    "limits": {"tries": 1, "seconds": 60},
}


class PageWorld(World):
    """The acceptance world, and a second target on the same app played through its page: on a
    copy of the made-up app that the run starts itself, from a plan saved as proven."""

    def start(self) -> None:
        if not getattr(self, "paged", False):
            self.paged = True
            self._page_target()
        super().start()

    def _page_target(self) -> None:
        config = yaml.safe_load(self.config.read_text())
        # Written against {url}, so the http adapter plays on the copy the run starts.
        block = _target(self.app_port)
        url = f"http://127.0.0.1:{self.app_port}"
        block = json.loads(json.dumps(block).replace(f"{url}/api", "{url}/api"))
        config["targets"]["paged"] = {
            **block,
            "url": url,
            "page": PAGE,
            "page_accounts": [{"email": "person0@made-up.test", "password_env": PASSWORD_ENV}],
        }
        self.config.write_text(yaml.safe_dump(config))
        for target in ("paged", "bot"):
            cases = self.root / "sets" / target / "main"
            cases.mkdir(parents=True, exist_ok=True)
            (cases / "set.json").write_text(json.dumps({"name": "Main"}))
            (cases / "answer-shown.case.json").write_text(json.dumps(CASE))
        self.import_sets()
        self.switches()

    def switches(self, *names: str) -> None:
        """The plan the run's copy starts from, with these page switches on from its start."""
        kept = self.root / "copy-data"
        kept.mkdir(exist_ok=True)
        plan = Plan(
            target="paged",
            stores=[PlannedStore("scratch", "dir", str(kept), strategy="snapshot")],
            launch={
                "kind": "process",
                "command": [sys.executable, str(HERE / "made_up_app.py"), "{port}"],
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

    def page_map(self, **changes) -> None:
        """The paged target's map with *changes*, and the server started again to read it. The
        check before a first run on a new setup is off: its message would meet what the try is
        to show."""
        config = yaml.safe_load(self.config.read_text())
        config["targets"]["paged"]["page"] = {**PAGE, **changes}
        config["check_adapters"] = False
        self.config.write_text(yaml.safe_dump(config))
        self.kill()
        self.start()

    def play(self, target: str) -> dict:
        asked = {"target": target, "set": "main", "cases": ["answer-shown"], "tries": 1}
        found = self.client.post("/v1/evals/runs/estimate", json=asked).json()
        assert "token" in found, found
        run = self.client.post("/v1/evals/runs", json={"token": found["token"]}).json()["run"]
        self.wait(run, 240)
        return self.stored(run)

    def evidence(self, run: str) -> list:
        return RunStore(Database.open(self.root / "data").docs()).evidence(run)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv(PASSWORD_ENV, "made-up-0")
    made = PageWorld(tmp_path)
    try:
        yield made
    finally:
        made.close()


def _try(doc: dict) -> dict:
    (attempt,) = doc["cases"]["answer-shown"]["tries"]
    return attempt


def _view(world: PageWorld, run: str) -> dict:
    """The try view the page reads."""
    found = world.client.get(f"/v1/evals/runs/{run}/cases/answer-shown/compare")
    assert found.status_code == 200, found.text
    return found.json()


def _checks(attempt: dict) -> dict[str, str]:
    return {c.id: c.outcome.value for c in current(attempt).checks}


def test_a_test_plays_through_the_page_and_each_broken_page_reads_as_what_broke(world) -> None:
    # As it should be: every layer it has holds, and the try keeps what the page showed.
    doc = world.play("paged")
    assert doc["status"] == "done", doc.get("error")
    assert doc["isolation"]["how"] == "proven"
    attempt = _try(doc)
    found = current(attempt)
    assert found.outcome is Outcome.PASSED, _checks(attempt)
    assert layers(found) == {"stored": "absent", "answered": "pass", "shown": "pass"}
    (evidence,) = world.evidence(doc["run"])
    page = evidence.page
    assert page is not None and not page.interrupted
    # Without Playwright's ffmpeg the try plays on, and says why it has no video.
    video = page.video_missing != NO_VIDEO
    assert evidence.turns[0].shown is not None and "42" in evidence.turns[0].shown.text
    (turn,) = page.turns
    assert turn.shot == "turn-1.png" and turn.shown_at is not None
    kept = world.root / "data" / "files" / doc["run"] / "answer-shown.1"
    assert (kept / "turn-1.png").read_bytes()[:4] == b"\x89PNG"
    if video:
        # Recorded, then deleted once the try passed: the map keeps video only when one fails.
        assert page.video_missing is None and page.video_policy == "failed"
        assert VIDEO in page.files and not (kept / VIDEO).exists()
        shown = _view(world, doc["run"])["page"]
        assert shown["video"] is None and shown["video_missing"] == VIDEO_ON_FAILURE

    # The test keeps no screenshots of its own (page.proof): none are taken, and the try says so.
    proof = "/v1/evals/sets/main/cases/answer-shown/proof"
    said = world.client.put(proof, params={"target": "paged"}, json={"screenshots": "off"})
    assert said.status_code == 200 and said.json()["proof"]["screenshots"] == "off", said.text
    asked = {"target": "paged", "set": "main", "cases": ["answer-shown"], "tries": 1}
    plays = world.client.post("/v1/evals/runs/estimate", json=asked).json()["plays"]
    assert [line["kept"] for line in plays] == ["no screenshots, video when a try fails"]
    doc = world.play("paged")
    assert current(_try(doc)).outcome is Outcome.PASSED
    (evidence,) = world.evidence(doc["run"])
    page = evidence.page
    assert page is not None and page.screenshot_policy == "off"
    assert page.turns[0].shot is None and not any(is_shot(name) for name in page.files)
    assert not (
        world.root / "data" / "files" / doc["run"] / "answer-shown.1" / "turn-1.png"
    ).exists()
    assert _view(world, doc["run"])["page"]["shots_missing"] == SHOTS_OFF
    world.client.put(proof, params={"target": "paged"}, json={"screenshots": None})

    # The reply never shows, though the app stored and answered it; the API alone passes.
    world.switches("drop_reply")
    doc = world.play("paged")
    attempt = _try(doc)
    found = current(attempt)
    assert layers(found) == {"stored": "absent", "answered": "pass", "shown": "fail"}
    assert found.outcome is Outcome.FAILED and _checks(attempt)["shown:1"] == "failed"
    if video:
        # The failed try keeps its video, and the page plays it from the file route.
        (evidence,) = world.evidence(doc["run"])
        assert evidence.page is not None and VIDEO in evidence.page.files
        made = world.root / "data" / "files" / doc["run"] / "answer-shown.1" / VIDEO
        assert made.stat().st_size > 1000 and made.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"
        shown = _view(world, doc["run"])["page"]
        assert shown["video_missing"] is None and shown["video"]["url"]
        served = world.client.get(shown["video"]["url"])
        assert served.status_code == 200 and served.headers["content-type"] == "video/webm"
        assert served.content == made.read_bytes()
    api_only = current(_try(world.play("bot")))
    assert api_only.outcome is Outcome.PASSED, "through the API alone this would have passed"
    assert layers(api_only)["shown"] == "absent"

    # Drawing the reply throws: the page's health fails, with the error in the console.
    world.switches("throw_on_reply")
    doc = world.play("paged")
    attempt = _try(doc)
    assert _checks(attempt)["page_health"] == "failed"
    (evidence,) = world.evidence(doc["run"])
    assert any("TypeError" in line.text.text for line in evidence.page.console)

    # Send is renamed: the map names what the page lacks, and the run ends after one try.
    world.switches("rename_send")
    doc = world.play("paged")
    assert doc["status"] == "failed" and doc["failure"] == PAGE_MAP_BROKEN
    attempt = _try(doc)
    found = current(attempt)
    assert found.outcome is Outcome.UNJUDGED
    assert any(c.unjudged is not None and c.unjudged.value == "page_map" for c in found.checks)
    # The try says what wasn't there, with the page as it was; no layer of the app's is blamed.
    view = _view(world, doc["run"])
    broken = view["page"]["broken_map"]
    assert broken["missing"] == "button 'Send'" and broken["shot_url"]
    assert world.client.get(broken["shot_url"]).content[:4] == b"\x89PNG"
    assert layers(found)["answered"] == "absent"


async def test_the_writers_page_tools_reach_the_copy_and_only_the_rehearsal_sends(world) -> None:
    """What the test writer's page tools do, for real: each starts evalution's copy from the
    proven plan and takes it down after; trying Send finds it and clicks nothing, and the
    rehearsal's one message shows its reply, with a screenshot kept."""
    config = load(world.config)
    targets = Targets(config)
    capabilities = targets.adapter("paged").capabilities
    page = PageMap.model_validate(PAGE)
    assert learn.refusal(config, "paged", capabilities) is None

    looked = await learn.look(config, "paged", capabilities, "{copy_page}/chat", PAGE["sign_in"])
    assert looked["at"] == "/chat" and 'button "Send"' in looked["snapshot"]
    send = await learn.try_entry(config, "paged", capabilities, page, "actions.send")
    assert send["found"] is True and send["clicked"] is False and send["blocked"] == []
    typed = await learn.try_entry(config, "paged", capabilities, page, "actions.type")
    assert typed["found"] is True and typed["took_text"] is True

    done = await learn.rehearse(config, "paged", capabilities, page, targets.files)
    assert done.passed, done.said()
    assert done.reply and done.shots == ["turn-1.png"] and done.at is not None
    shot = config.data_dir / "files" / done.at["run"] / f"rehearsal.{done.at['n']}" / "turn-1.png"
    assert shot.read_bytes()[:4] == b"\x89PNG"
    # The copy went with each: the plan is as it was, proven.
    assert PlanStore(config.data_dir).load("paged").status == "proven"


def test_a_stale_page_map_is_checked_on_the_copy_before_the_run_plays(world) -> None:
    kept = PageMapStore(world.root / "data")
    stale = KeptMap("paged", PAGE, status="stale", stale_files=["web/chat.html"])
    kept.save(stale)
    # The page as it is: the check finds everything it names, and the run plays.
    doc = world.play("paged")
    assert doc["status"] == "done", doc.get("error")
    assert kept.load("paged").status == "approved"
    assert _try(doc)["n"] == 1

    # A commit renamed Send: the check before playing catches it, and no try plays.
    kept.save(stale)
    world.switches("rename_send")
    doc = world.play("paged")
    assert doc["status"] == "failed" and doc["failure"] == PAGE_MAP_BROKEN
    assert "checked on evalution's copy before playing" in doc["error"]
    assert "button 'Send'" in doc["error"] and "Re-learn the page" in doc["error"]
    assert doc["cases"]["answer-shown"]["tries"] == []
    broken = kept.load("paged")
    assert broken.status == "broken" and broken.missing == ["button 'Send'"]
    # Home says so, as it says an isolation plan is out of date, with the page as it was.
    health = world.client.get("/v1/targets/paged/health").json()
    shot = f"/v1/targets/paged/page/shots/{broken.missing_shot}"
    assert health["page"] == {
        "status": "broken",
        "stale_files": ["web/chat.html"],
        "missing": ["button 'Send'"],
        "shot": shot,
    }
    served = world.client.get(shot)
    assert served.status_code == 200 and served.content[:4] == b"\x89PNG"
    reach = world.client.get("/v1/targets/paged/page").json()
    assert reach["current"]["missing_shot"] == shot


def test_a_message_the_app_refuses_is_played_again_and_one_it_never_takes_ends_soon(
    world,
) -> None:
    """The app's side of the page's Send. A 503 on the message is the app busy, and nothing
    reached it: the try is played again, as through the API, and passes. A message the app holds
    with no answer ends at the map's ``send_seconds``, not the try's limit; it may have reached
    the app, so it isn't played again; and either way it is the app's, under answered, never
    "failed on the page"."""
    world.switches("refuse_message_once")
    doc = world.play("paged")
    assert doc["status"] == "done", doc.get("error")
    attempt = _try(doc)
    assert current(attempt).outcome is Outcome.PASSED, _checks(attempt)
    (again,) = attempt["retried"]
    assert again["what"] == "app" and "503" in again["why"]

    world.page_map(send_seconds=4)
    world.switches("hang_message")
    doc = world.play("paged")
    assert doc["status"] == "done", doc.get("error")
    attempt = _try(doc)
    found = current(attempt)
    assert found.outcome is Outcome.UNJUDGED, _checks(attempt)
    assert not attempt.get("retried"), "it may have reached the app: never sent twice"
    assert layers(found) == {"stored": "absent", "answered": "unjudged", "shown": "unjudged"}
    (evidence,) = world.evidence(doc["run"])
    assert evidence.failure is not None and evidence.failure.value == "transient"
    assert "send_seconds" in (evidence.error or "") and evidence.seconds < 40
    view = _view(world, doc["run"])
    assert view["page"]["not_taken"] == {"status": None}
    assert view["page"]["health"]["outcome"] == "passed"
