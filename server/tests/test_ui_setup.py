"""Setting UI testing up from the page (docs/plans/ui-testing-ease.md, section 2): installing what
this computer lacks with fixed commands only, one job at a time and never during a run; the one
flow's refusals; the rehearsal's video beside its screenshots; and the offer for an app whose
code settings leave its frontend out. Nothing is ever installed here: every command is mocked."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load, save_page_target
from evalution.connect import page as page_connect
from evalution.core.evidence import VIDEO
from evalution.project.pagemaps import KeptMap, PageMapStore
from evalution.runs.page import install
from evalution.runs.page import target as page_target
from evalution.storage import Database
from evalution.suite.store import save_set
from evalution.targets import Targets
from evalution.writer import paging

ALL_MISSING = {"playwright": False, "browser": False, "ffmpeg": False}
ALL_THERE = {"playwright": True, "browser": True, "ffmpeg": True}
CHAT = """export function Chat() {
  return (
    <main>
      <div role="log" aria-label="Messages"></div>
      <textarea aria-label="Message" />
      <button type="submit">Send</button>
    </main>
  );
}
"""


class FakeProcess:
    """A command that prints *out* and ends with *code*, without running anything."""

    def __init__(self, out: bytes, code: int) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(out)
        self.stdout.feed_eof()
        self.code = code

    async def wait(self) -> int:
        return self.code

    def kill(self) -> None:
        pass


@pytest.fixture
def commands(monkeypatch):
    """Every command a job runs, as its argv; each prints a progress line and succeeds, unless
    ``fail`` names the piece whose command fails."""
    ran: list[tuple[str, ...]] = []
    state: dict = {"fail": None, "installs": None}

    async def fake_exec(*argv, **kwargs):
        assert "shell" not in kwargs
        ran.append(tuple(argv))
        failing = state["fail"] is not None and state["fail"] in argv
        if state["installs"] is not None and not failing:
            state["installs"].update({argv[-1]: True})
        return FakeProcess(b"Downloading 130 MiB\r 50%\r100%\n", 1 if failing else 0)

    monkeypatch.setattr(install.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(install, "server_folder", lambda: install.Path("/evalution/server"))
    monkeypatch.setattr(install, "uv_path", lambda: "/bin/uv")
    return ran, state


def _parts(monkeypatch, *readings: dict[str, bool]) -> None:
    """What :func:`page_parts` reads, one reading after the other, the last one kept."""
    left = list(readings)

    def read() -> dict[str, bool]:
        return dict(left.pop(0) if len(left) > 1 else left[0])

    monkeypatch.setattr(page_target, "page_parts", read)


# What runs


def test_only_the_fixed_commands_for_whats_missing_run(monkeypatch, commands) -> None:
    steps, why_not = install.plan(ALL_MISSING)
    assert why_not is None
    python = sys.executable
    assert [s.command for s in steps] == [
        ("/bin/uv", "sync", "--project", "/evalution/server", "--extra", "page", "--inexact"),
        (python, "-m", "playwright", "install", "chromium"),
        (python, "-m", "playwright", "install", "ffmpeg"),
    ]
    only_ffmpeg, _ = install.plan({**ALL_THERE, "ffmpeg": False})
    assert [s.command for s in only_ffmpeg] == [(python, "-m", "playwright", "install", "ffmpeg")]
    assert install.plan(ALL_THERE) == ([], None)


def test_without_its_uv_checkout_the_page_extra_isnt_added_from_here(monkeypatch) -> None:
    monkeypatch.setattr(install, "server_folder", lambda: None)
    assert install.plan(ALL_MISSING) == ([], install.NOT_A_CHECKOUT)
    # A browser alone needs no checkout: the server's own Python installs it.
    steps, why_not = install.plan({**ALL_THERE, "browser": False})
    assert why_not is None and steps[0].command[:3] == (sys.executable, "-m", "playwright")
    monkeypatch.setattr(install, "server_folder", lambda: install.Path("/evalution/server"))
    monkeypatch.setattr(install, "uv_path", lambda: None)
    assert install.plan(ALL_MISSING) == ([], install.NO_UV)


async def test_a_job_installs_each_piece_in_turn_and_says_how_it_went(
    monkeypatch, commands
) -> None:
    ran, _ = commands
    _parts(monkeypatch, ALL_MISSING, ALL_THERE)
    installer = install.Installer()
    job = installer.start()
    assert job is not None and installer.running()
    with pytest.raises(RuntimeError, match="already"):
        installer.start()
    await installer._task
    assert [argv[-1] for argv in ran] == ["--inexact", "chromium", "ffmpeg"]
    view = job.view()
    assert view["state"] == "done" and view["error"] is None and view["ended"] is not None
    assert [(s["key"], s["label"], s["state"]) for s in view["steps"]] == [
        ("playwright", "Add Playwright to evalution", "done"),
        ("browser", "Download Chromium", "done"),
        ("ffmpeg", "Download ffmpeg for video", "done"),
    ]
    assert all(isinstance(s["seconds"], float) for s in view["steps"])
    # What the commands printed, line by line (a progress bar's \r too), each after its command.
    assert view["lines"][:4] == [
        "$ /bin/uv sync --project /evalution/server --extra page --inexact",
        "Downloading 130 MiB",
        "50%",
        "100%",
    ]


async def test_a_command_that_fails_ends_the_job_with_its_step_failed(
    monkeypatch, commands
) -> None:
    ran, state = commands
    state["fail"] = "chromium"
    _parts(monkeypatch, {**ALL_THERE, "browser": False, "ffmpeg": False})
    installer = install.Installer()
    job = installer.start()
    await installer._task
    assert [argv[-1] for argv in ran] == ["chromium"], "nothing after the failed step"
    view = job.view()
    assert view["state"] == "failed" and view["error"] == "Download Chromium didn't finish."
    assert [s["state"] for s in view["steps"]] == ["failed", "waiting"]
    assert "exit code 1" in view["steps"][0]["detail"]


async def test_with_everything_there_nothing_runs(monkeypatch, commands) -> None:
    ran, _ = commands
    _parts(monkeypatch, ALL_THERE)
    assert install.Installer().start() is None
    assert ran == []


# The routes


def _home(tmp_path, *, include: list[str] | None = None) -> None:
    repo = tmp_path / "app"
    (repo / "web" / "src").mkdir(parents=True)
    (repo / "web" / "src" / "Chat.tsx").write_text(CHAT)
    (repo / "prompts").mkdir()
    (repo / "prompts" / "system.md").write_text("You are a helpful assistant.\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"web_dir": "none"}))
    docs = Database.open(tmp_path / "data").docs()
    code = {"path": str(repo), **({"include": include} if include else {})}
    save_page_target(docs, "bot", {"adapter": "http", "http": {"send": {"url": "x"}}, "code": code})
    save_set(docs, "bot", "main", {"name": "Main"})


@pytest.fixture
def app(tmp_path):
    _home(tmp_path)
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        yield client


def test_settings_say_whether_the_page_can_install_and_follow_the_job(
    app, monkeypatch, commands
) -> None:
    ran, state = commands
    there = {**ALL_THERE, "ffmpeg": False}
    state["installs"] = there  # the fake command installs what it names
    monkeypatch.setattr(page_target, "page_parts", lambda: dict(there))
    found = app.get("/v1/settings/ui-testing").json()
    assert found["ready"] is False and found["can_install"] is True
    assert found["why_not_install"] is None and found["job"] is None
    assert [m["part"] for m in found["missing"]] == ["ffmpeg"]

    started = app.post("/v1/settings/ui-testing/install")
    assert started.status_code == 202
    job = started.json()["job"]
    assert job["state"] in ("running", "done") and job["steps"][0]["key"] == "ffmpeg"
    # The request's body is never a command: only the fixed ones ran.
    assert ran == [(sys.executable, "-m", "playwright", "install", "ffmpeg")]
    for _ in range(50):
        found = app.get("/v1/settings/ui-testing").json()
        if found["job"]["state"] != "running":
            break
    assert found["ready"] is True and found["can_install"] is False
    assert found["job"]["state"] == "done" and found["job"]["steps"][0]["state"] == "done"


def test_the_install_is_refused_hosted_and_while_a_run_goes(app, monkeypatch, commands) -> None:
    ran, _ = commands
    _parts(monkeypatch, ALL_MISSING)
    runs = app.app.state.runs
    monkeypatch.setattr(runs, "busy", lambda: True)
    refused = app.post("/v1/settings/ui-testing/install")
    assert refused.status_code == 409 and "A run is going" in refused.json()["detail"]
    monkeypatch.setattr(runs, "busy", lambda: False)
    app.app.state.hosted = True
    assert app.post("/v1/settings/ui-testing/install").status_code == 400
    assert app.get("/v1/settings/ui-testing").status_code == 400
    assert ran == []


def test_without_a_checkout_the_page_says_how_instead(app, monkeypatch, commands) -> None:
    ran, _ = commands
    monkeypatch.setattr(install, "server_folder", lambda: None)
    _parts(monkeypatch, ALL_MISSING)
    found = app.get("/v1/settings/ui-testing").json()
    assert found["can_install"] is False and found["why_not_install"] == install.NOT_A_CHECKOUT
    refused = app.post("/v1/settings/ui-testing/install")
    assert refused.status_code == 409 and "pip install" in refused.json()["detail"]
    assert ran == []


def test_learning_the_page_from_the_card_needs_a_yes_and_a_copy(app) -> None:
    unsaid = app.post("/v1/targets/bot/page/learn", json={})
    assert unsaid.status_code == 400 and "one message" in unsaid.json()["detail"]
    no_set = app.post("/v1/targets/bot/page/learn", json={"confirm": True, "set": "nope"})
    assert no_set.status_code == 404
    no_copy = app.post("/v1/targets/bot/page/learn", json={"confirm": True})
    assert no_copy.status_code == 409 and "own copy" in no_copy.json()["detail"]
    view = app.get("/v1/targets/bot/page").json()
    assert view["learn"] is None and view["writable"] is True


def test_a_map_waiting_for_approval_keeps_the_card_on_it(tmp_path) -> None:
    """The offer's card follows the setup to its Approve: with a tried map waiting, it isn't
    offered again, it's underway."""
    _home(tmp_path)
    config = load(tmp_path / "evalution.yaml")
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/targets/bot/page/offer").json()["underway"] is False
        PageMapStore(config.data_dir).propose(KeptMap("bot", {"url": "{copy_page}/"}))
        offer = client.get("/v1/targets/bot/page/offer").json()
    assert offer["offer"] is False and offer["underway"] is True


# What a rehearsal keeps


def test_the_rehearsals_video_shows_beside_its_screenshots(tmp_path) -> None:
    _home(tmp_path)
    targets = Targets(load(tmp_path / "evalution.yaml"))
    at = {"run": "page-map-bot", "case": "rehearsal", "n": 1}
    kept = KeptMap("bot", {"url": "{copy_page}/"}, shots=["turn-1.png", VIDEO], shots_at=at)
    PageMapStore(targets.config.data_dir).propose(kept)
    proposed = page_connect.view(targets, "bot")["proposed"]
    assert proposed["shots"] == ["/v1/targets/bot/page/shots/turn-1.png"]
    assert proposed["video"] == f"/v1/targets/bot/page/shots/{VIDEO}"
    files = targets.files
    assert files is not None
    files.put("page-map-bot", "rehearsal", 1, VIDEO, b"webm")
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        served = client.get(f"/v1/targets/bot/page/shots/{VIDEO}")
    assert served.status_code == 200 and served.headers["content-type"] == "video/webm"


# The offer, for code settings that leave the frontend out


def test_a_frontend_the_code_settings_leave_out_is_still_offered(tmp_path) -> None:
    """relay-lite's code settings name only its prompts and tools: its web/ page is found
    all the same, and what the agent reads as evidence stays what the settings include."""
    _home(tmp_path, include=["prompts/**"])
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        offer = client.get("/v1/targets/bot/page/offer").json()
    assert offer["offer"] is True and offer["found_in"] == "web/src/Chat.tsx"
    targets = Targets(load(tmp_path / "evalution.yaml"))
    assert paging._frontend(targets, "bot") == {}, "the code read keeps to the settings"
    assert paging.outside_code(targets, "bot") == ["web"]


async def test_the_code_read_says_the_page_is_left_out_and_how_to_learn_it(tmp_path) -> None:
    _home(tmp_path, include=["prompts/**"])
    targets = Targets(load(tmp_path / "evalution.yaml"))
    found = await paging.read_code(targets, {"target": "bot"})
    assert found["files_read"] == 0 and found["left_out"] == ["web"]
    assert "try_page_entry" in found["note"]


def test_the_frontend_look_is_bounded_and_read_only(tmp_path, monkeypatch) -> None:
    root = tmp_path / "app"
    for n in range(10):
        (root / "src" / f"part{n}").mkdir(parents=True)
        (root / "src" / f"part{n}" / "Box.tsx").write_text("<textarea />")
    (root / "web" / "node_modules" / "x").mkdir(parents=True)
    (root / "web" / "node_modules" / "x" / "Chat.tsx").write_text(CHAT)
    (root / "web" / "Chat.test.tsx").write_text(CHAT)
    (root / "web" / ".hidden").mkdir()
    (root / "web" / ".hidden" / "Chat.tsx").write_text(CHAT)
    (root / "server.py").write_text(CHAT)
    monkeypatch.setattr(paging, "BEYOND_FILES", 4)
    found = paging.frontend_beyond(root)
    assert len(found) == 4 and all(rel.startswith("src/") for rel in found)
    monkeypatch.setattr(paging, "BEYOND_FILES", 400)
    assert set(paging.frontend_beyond(root)) == {f"src/part{n}/Box.tsx" for n in range(10)}
