"""``evalution demo versions``'s pieces, fast and with no server: the small repository it makes
(``main`` and ``feature-x``, one line apart, checked out on ``main``), the target, plan and recipe
it sets up in its scratch folder, the version a run of ``feature-x`` would play, what it prints of
a comparison, and its refusals. The whole demo, played, is
``tests/acceptance/test_demo_versions.py``.
"""

import json
import runpy
import sys

import pytest

from evalution import cli
from evalution.config import load
from evalution.demo import app as made_up
from evalution.demo import versions as demo
from evalution.isolation.isolate import off_copy
from evalution.isolation.plan import PlanStore
from evalution.isolation.recipe import RecipeStore
from evalution.runs import versioned
from evalution.storage import Database
from evalution.suite.cases import parse_case
from evalution.suite.store import import_sets


@pytest.fixture
def web(tmp_path):
    built = tmp_path / "web"
    built.mkdir()
    (built / "index.html").write_text("<!doctype html><title>evalution</title>")
    return built


@pytest.fixture
def folder(tmp_path):
    made = tmp_path / "demo"
    made.mkdir()
    return made.resolve()


def test_the_demo_makes_a_repository_whose_branches_differ_in_one_line(folder) -> None:
    repo = folder / "app"
    commits = demo.make_repo(repo)
    assert set(commits) == {"main", "feature-x"} and commits["main"] != commits["feature-x"]
    assert demo.git(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert demo.git(repo, "status", "--porcelain").strip() == "", "a clean checkout"
    diff = demo.git(repo, "diff", "main", "feature-x", "--unified=0").splitlines()
    changed = [line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert changed == [
        "-#: Whether it keeps what it's asked to remember.",
        "-made_up.remembers = True",
        "+#: Whether it keeps what it's asked to remember. Memory moves to a new store, not "
        "wired up yet.",
        "+made_up.remembers = False",
    ]
    assert not (repo / ".git" / "hooks" / "post-commit").exists()


def test_each_branchs_assistant_sets_whether_the_made_up_app_remembers(folder, monkeypatch) -> None:
    repo = folder / "app"
    demo.make_repo(repo)
    monkeypatch.setattr(made_up, "remembers", None)
    runpy.run_path(str(repo / demo.ASSISTANT), run_name="read")
    assert made_up.remembers is True
    demo.git(repo, "checkout", "-q", "feature-x")
    runpy.run_path(str(repo / demo.ASSISTANT), run_name="read")
    assert made_up.remembers is False


def test_the_demo_sets_up_a_target_whose_versions_start_in_the_repository(folder, web) -> None:
    path = demo.prepare(folder, web, 8123)
    assert path == folder / "evalution.yaml"
    assert {p.name for p in folder.iterdir()} == {
        "evalution.yaml", "sets", "copy-data", "data", "app"
    }  # fmt: skip
    config = load(path)
    assert config.data_dir == folder / "data" and config.web_dir == web
    assert config.model is None, "no model: the demo spends nothing"
    target = config.targets[demo.TARGET]
    assert target.code is not None and target.code.path == folder / "app"
    assert "page" not in target.options, "played through the API"
    assert off_copy(config, demo.TARGET) is None

    plan = PlanStore(config.data_dir).load(demo.TARGET)
    assert plan is not None and plan.status == "proven"
    assert plan.launch["kind"] == "process" and plan.launch["cwd"] == str(folder / "app")
    assert plan.launch["command"] == [
        sys.executable,
        str(folder / "app" / "assistant.py"),
        "{port}",
    ]
    assert plan.launch["env"] == {"TRACES": "http://127.0.0.1:8123/v1/traces"}
    recipe = RecipeStore(config.data_dir).load(demo.TARGET)
    assert recipe is not None and recipe.status == "proven"
    assert recipe.kind == "process" and recipe.setup == [] and recipe.cwd == "."

    # A run of feature-x would check it out and start it there, and nothing needs building.
    feature = demo.git(folder / "app", "rev-parse", "feature-x").strip()
    said = versioned.preview(config, demo.TARGET, "feature-x")
    short = feature[:7]
    assert said["commit"] == feature and said["said"] == f"feature-x @ {short}"
    assert said["cost"] == (
        f"Checks out feature-x @ {short} and starts it from there. Your checkout isn't touched."
    )

    # Three tests, each decided by code; only one asks what it was told.
    docs = Database.open(folder / "data").docs()
    assert import_sets(docs, folder / "sets") == [f"{demo.TARGET}/{demo.SET}"]
    for stem in demo.CASES:
        data = json.loads(
            (folder / "sets" / demo.TARGET / demo.SET / f"{stem}.case.json").read_text()
        )
        case = parse_case(data, stem, stem)
        assert "rubric" not in data["reply"] and data["limits"]["tries"] == demo.TRIES
        asks = [t.message for t in case.turns if getattr(t, "message", None)]
        assert ("What did I tell you?" in asks) == (stem == demo.FORGETS)


def _compared(base: str, worse: list[str]) -> dict:
    tests = [
        {
            "case": stem,
            "name": demo.CASES[stem]["name"],
            "change": "worse" if stem in worse else "same",
        }
        for stem in demo.CASES
    ]
    return {
        "base": base,
        "headline": f"{len(worse)} test got worse" if worse else "No change",
        "versions": {"heading": "feature-x vs main"},
        "tests": tests,
    }


def test_it_says_the_comparison_in_a_line_and_whether_it_came_out_right() -> None:
    compared = _compared("run-1", [demo.FORGETS])
    assert demo.summary(compared) == (
        "feature-x vs main: 1 test got worse: Remembers the dog's name"
    )
    done = {"status": "done", "totals": {"passed": 3, "cases": 3}}
    runs = {"main": {**done, "run": "run-1"}, "feature-x": {**done, "run": "run-2"}}
    assert demo.came_out(runs, compared) is None
    assert demo.came_out(runs, _compared("run-0", [demo.FORGETS])) == (
        "feature-x's run wasn't compared with main's"
    )
    assert "none" in str(demo.came_out(runs, _compared("run-1", [])))
    assert demo.summary(_compared("run-1", [])) == "feature-x vs main: No change"


def test_without_the_page_built_it_says_how_to_build_it(tmp_path, capsys) -> None:
    config = tmp_path / "evalution.yaml"
    config.write_text(f"web_dir: {tmp_path / 'no-page'}\n")
    assert cli.main(["--config", str(config), "demo", "versions", "--no-open"]) == 1
    said = capsys.readouterr()
    assert demo.WEB_BUILD in said.err and said.err.count("\n") == 1
    assert "demo data" not in said.out, "nothing was set up"


def test_without_git_it_says_so(monkeypatch, web, capsys) -> None:
    monkeypatch.setattr(demo.shutil, "which", lambda _name: None)
    assert demo.run(web, open_browser=False) == 1
    assert "needs git" in capsys.readouterr().err
