"""``evalution demo ui``'s pieces, fast and with no browser: what it sets up in its scratch folder
(a target played through the made-up app's page on a proven copy, two tests that read), what it
keeps from the person's environment, and its refusals. The whole demo, played, is
``tests/acceptance/test_demo_ui.py``.
"""

import json

import pytest

from evalution import cli
from evalution.config import load
from evalution.demo import app as made_up
from evalution.demo import ui
from evalution.isolation.isolate import off_copy
from evalution.isolation.plan import PlanStore
from evalution.runs.page.map import page_map_of
from evalution.runs.page.target import PAGE_SETUP
from evalution.storage import Database
from evalution.suite.cases import parse_case
from evalution.suite.store import import_sets


@pytest.fixture
def web(tmp_path):
    built = tmp_path / "web"
    built.mkdir()
    (built / "index.html").write_text("<!doctype html><title>evalution</title>")
    return built


def test_the_demo_sets_up_a_page_target_on_a_proven_copy_in_its_own_folder(tmp_path, web) -> None:
    folder = tmp_path / "demo"
    folder.mkdir()
    path = ui.prepare(folder, web, 8123)
    assert path == folder / "evalution.yaml"
    assert {p.name for p in folder.iterdir()} == {"evalution.yaml", "sets", "copy-data", "data"}

    config = load(path)
    assert config.data_dir == folder / "data" and config.web_dir == web
    assert config.model is None, "no model: the demo spends nothing"
    target = config.targets[ui.TARGET]
    page = page_map_of(target)
    assert page is not None and page.url == "{copy_page}/chat"
    # Every address it calls is the copy's.
    assert off_copy(config, ui.TARGET) is None

    plan = PlanStore(config.data_dir).load(ui.TARGET)
    assert plan is not None and plan.status == "proven"
    assert plan.launch["command"][1:3] == ["-m", "evalution.demo.app"]
    env = plan.launch["env"]
    assert env["TRACES"] == "http://127.0.0.1:8123/v1/traces"
    assert env["DROP_REPLY_TO"] == ui.DROPPED

    # The two tests read, and import as a set; only the failing one asks what the page drops.
    docs = Database.open(folder / "data").docs()
    assert import_sets(docs, folder / "sets") == [f"{ui.TARGET}/{ui.SET}"]
    asked = {}
    for stem in ui.CASES:
        data = json.loads((folder / "sets" / ui.TARGET / ui.SET / f"{stem}.case.json").read_text())
        case = parse_case(data, stem, stem)
        asked[stem] = [t.message for t in case.turns if getattr(t, "message", None)]
        assert data["page"] == {"decides": True} and "rubric" not in data["reply"]
    assert not any(ui.DROPPED in m for m in asked[ui.PASSES])
    assert asked[ui.FAILS][-1] == ui.DROPPED


def test_the_demo_keeps_the_persons_own_data_out(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone/their-own")
    monkeypatch.setenv("EVALUTION_CONFIG", "/their/evalution.yaml")
    monkeypatch.setenv("EVALUTION_CHROME", "/their/chrome")
    env = ui.environment()
    assert "DATABASE_URL" not in env and "EVALUTION_CONFIG" not in env
    assert env["EVALUTION_CHROME"] == "/their/chrome", "the browser it found is still used"
    assert env[ui.PASSWORD_ENV] == made_up.ACCOUNTS[ui.EMAIL]


def test_without_the_page_extra_or_a_browser_it_says_the_one_command(
    monkeypatch, tmp_path, web, capsys
) -> None:
    config = tmp_path / "evalution.yaml"
    config.write_text(f"web_dir: {web}\n")
    for lacking in ("playwright", "browser"):
        parts = {"playwright": True, "browser": True, "ffmpeg": True, lacking: False}
        monkeypatch.setattr(ui, "page_parts", lambda parts=parts: parts)
        assert cli.main(["--config", str(config), "demo", "ui", "--no-open"]) == 1
        said = capsys.readouterr()
        assert PAGE_SETUP in said.err and said.err.count("\n") == 1
        assert "demo data" not in said.out, "nothing was set up"


def test_without_the_page_built_it_says_how_to_build_it(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(ui, "page_parts", lambda: dict.fromkeys(("playwright", "browser"), True))
    assert ui.run(tmp_path / "no-page", open_browser=False) == 1
    assert ui.WEB_BUILD in capsys.readouterr().err


def test_the_made_up_page_drops_only_the_reply_to_its_phrase(monkeypatch) -> None:
    monkeypatch.setattr(made_up, "drop_reply_to", ui.DROPPED)
    sid = made_up._new_session(owner=ui.EMAIL)
    page = made_up._chat_page(ui.EMAIL, sid)
    data = json.loads(page.split('id="page-data">')[1].split("</script>")[0])
    assert data["drop_reply_to"] == ui.DROPPED and data["switches"]["drop_reply"] is False
    assert "function dropped(asked)" in page
