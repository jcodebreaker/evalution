"""UI testing a developer can't miss (docs/plans/ui-testing-ease.md, sections 1 and 3): the offer
where the code has a chat page, turned down once and remembered; how each test plays; what a try
keeps of the page (its proof), per target and per test, and the files that setting deletes; and
the Run dialog's words for how a run will play."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import ConfigError, load, page_targets, save_page_target
from evalution.core.case import Case
from evalution.core.evidence import VIDEO, PageRecord, PageTurn, TryEvidence
from evalution.core.judgement import Outcome
from evalution.project.pagemaps import KeptMap, PageMapStore
from evalution.runs.estimate import about, plays
from evalution.runs.page.map import PageMap, described, proof_words, read_map
from evalution.runs.runner import Runs
from evalution.storage import Database
from evalution.storage.files import LocalFiles
from evalution.suite.cases import CaseError, parse_case
from evalution.suite.store import save_case, save_set
from evalution.targets import Targets

#: A frontend's chat page: a message box and Send.
CHAT = """export function Chat() {
  return (
    <main>
      <div role="log" aria-label="Messages"></div>
      <form>
        <textarea aria-label="Message" />
        <button type="submit">Send</button>
      </form>
    </main>
  );
}
"""
#: A frontend with a form, but no chat in it.
SETTINGS = """export function Settings() {
  return <form><input aria-label="Name" /></form>;
}
"""
MAP = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {"in": {"role": "log", "name": "Messages"}},
}
HTTP = {"adapter": "http", "http": {"send": {"url": "x"}}}


def _git(root, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _home(tmp_path, page_code: str = CHAT, *, page: dict | None = None) -> None:
    """A target connected from the page with its code in git, and a set of two tests."""
    repo = tmp_path / "app"
    (repo / "web").mkdir(parents=True)
    (repo / "web" / "Chat.tsx").write_text(page_code)
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "add", ".")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "page")
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"web_dir": "none"}))
    docs = Database.open(tmp_path / "data").docs()
    block = {**HTTP, "code": {"path": str(repo)}}
    if page is not None:
        block["page"] = page
    save_page_target(docs, "bot", block)
    save_set(docs, "bot", "main", {"name": "Main"})
    save_case(docs, "bot", "main", {"name": "Dog", "turns": [{"message": "Hi"}]})
    own = {"name": "Cat", "turns": [{"message": "Hi"}], "page": {"proof": {"video": "always"}}}
    save_case(docs, "bot", "main", own)


@pytest.fixture
def client(tmp_path):
    def made(page_code: str = CHAT, *, page: dict | None = None) -> TestClient:
        _home(tmp_path, page_code, page=page)
        return TestClient(create_app(load(tmp_path / "evalution.yaml")))

    return made


# The offer


def test_a_target_whose_code_has_a_chat_page_is_offered_ui_testing_until_turned_down(
    client,
) -> None:
    with client() as app:
        offer = app.get("/v1/targets/bot/page/offer").json()
        assert offer["offer"] is True and offer["found_in"] == "web/Chat.tsx"
        # No copy of the app yet: the agent can't learn the page, and says what comes first.
        assert offer["can_learn"] is False and "own copy" in offer["why_not"]

        turned_down = app.post("/v1/targets/bot/page/offer/dismiss").json()
        assert turned_down["offer"] is False and turned_down["dismissed"] is True
        assert app.get("/v1/targets/bot/page/offer").json()["offer"] is False, "remembered"


def test_the_made_up_apps_code_reads_as_a_chat_page(tmp_path) -> None:
    """Connecting the made-up app, whose code is its chat page, offers UI testing."""
    made_up = Path(__file__).parent / "acceptance" / "made_up_app.py"
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "made_up_app.py").write_text(made_up.read_text())
    _git(repo, "init", "-q")
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"web_dir": "none"}))
    docs = Database.open(tmp_path / "data").docs()
    save_page_target(docs, "made-up", {**HTTP, "code": {"path": str(repo)}})
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as app:
        offer = app.get("/v1/targets/made-up/page/offer").json()
        assert offer["offer"] is True and offer["found_in"] == "made_up_app.py"


def test_the_offer_stays_away_once_turned_down_across_a_restart(client, tmp_path) -> None:
    with client() as app:
        app.post("/v1/targets/bot/page/offer/dismiss")
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as again:
        assert again.get("/v1/targets/bot/page/offer").json()["offer"] is False


def test_no_offer_without_a_chat_page(client) -> None:
    with client(SETTINGS) as app:
        assert app.get("/v1/targets/bot/page/offer").json()["offer"] is False


def test_no_offer_for_a_target_that_plays_through_its_page(client) -> None:
    with client(page=MAP) as app:
        assert app.get("/v1/targets/bot/page/offer").json()["offer"] is False


def test_no_offer_while_a_map_waits_for_approval(client, tmp_path) -> None:
    PageMapStore(tmp_path / "data").propose(KeptMap("bot", {**MAP, "status": "tried"}))
    with client() as app:
        assert app.get("/v1/targets/bot/page/offer").json()["offer"] is False


def test_no_offer_where_nothing_could_be_kept(client) -> None:
    app_ = client()
    with app_ as app:
        # Hosted, a workspace keeps no files: a page is only played locally or in CI.
        app.app.state.workspaces.get("local").targets.files = None
        assert app.get("/v1/targets/bot/page/offer").json()["offer"] is False


# How each test plays


def test_each_test_of_an_app_without_a_page_map_plays_through_its_api(client) -> None:
    with client() as app:
        rows = app.get("/v1/evals/sets/main/cases", params={"target": "bot"}).json()["data"]
        assert {row["plays"] for row in rows} == {"api"}
        dog = app.get("/v1/evals/sets/main/cases/dog", params={"target": "bot"}).json()
        assert dog["plays"] == "api" and dog["proof"] is None, "nothing to capture"


def test_each_test_of_an_app_with_a_page_map_plays_through_its_page(client) -> None:
    with client(page=MAP) as app:
        rows = app.get("/v1/evals/sets/main/cases", params={"target": "bot"}).json()["data"]
        assert {row["plays"] for row in rows} == {"page"}


# Proof


def test_proof_defaults_and_the_older_video_setting_still_reads() -> None:
    plain = read_map(MAP)
    assert plain.proof.screenshots == "always" and plain.proof.video == "failed"
    assert read_map({**MAP, "video": "never"}).proof.video == "off"
    assert read_map({**MAP, "video": "always"}).proof.video == "always"
    # Both given: proof wins.
    both = read_map({**MAP, "video": "never", "proof": {"video": "always"}})
    assert both.proof.video == "always"
    kept = read_map({**MAP, "video": "never", "proof": {"screenshots": "failed"}})
    assert kept.proof.screenshots == "failed" and kept.proof.video == "off"
    with pytest.raises(ConfigError, match=r"proof\.video"):
        read_map({**MAP, "proof": {"video": "sometimes"}})
    with pytest.raises(ConfigError, match="video"):
        read_map({**MAP, "video": "sometimes"})
    words = {row["what"]: row["where"] for row in described(plain)}
    assert words["Screenshots"] == "one after each message, kept for every try"
    assert words["Video"] == "kept when a try doesn't pass"


def test_a_test_says_its_own_proof_and_it_wins_part_by_part() -> None:
    case = parse_case(
        {"name": "Cat", "turns": [{"message": "Hi"}], "page": {"proof": {"screenshots": "off"}}},
        "cat",
    )
    assert case.page_proof == {"screenshots": "off"}
    proof = read_map({**MAP, "proof": {"screenshots": "always", "video": "always"}}).proof
    assert proof.over(case.page_proof).model_dump() == {"screenshots": "off", "video": "always"}
    for wrong, said in (
        ({"screenshots": "sometimes"}, "screenshots: must be one of always, failed, off"),
        ({"audio": "off"}, "proof.audio: unknown field"),
    ):
        with pytest.raises(CaseError, match=said):
            parse_case({"name": "C", "turns": [{"message": "Hi"}], "page": {"proof": wrong}}, "c")


def test_a_targets_proof_is_set_from_the_page_and_keeps_its_map_approved(client, tmp_path) -> None:
    data = tmp_path / "data"
    with client(page={**MAP, "status": "approved"}) as app:
        PageMapStore(data).save(KeptMap("bot", {**MAP, "status": "approved"}, status="approved"))
        view = app.get("/v1/targets/bot/page").json()
        assert view["proof"] == {"screenshots": "always", "video": "failed"}
        assert view["current"]["status"] == "approved"

        changed = app.put("/v1/targets/bot/page/proof", json={"screenshots": "off"})
        assert changed.status_code == 200, changed.text
        view = changed.json()
        assert view["proof"] == {"screenshots": "off", "video": "failed"}
        assert view["current"]["status"] == "approved", "a setting beside the map"
        saved = page_targets(Database.open(data).docs())["bot"]["page"]
        assert saved["proof"] == {"screenshots": "off", "video": "failed"}

        assert app.put("/v1/targets/bot/page/proof", json={"video": "x"}).status_code == 400
        assert app.put("/v1/targets/bot/page/proof", json={"audio": "off"}).status_code == 400

        # A test's own proof wins over the target's, part by part.
        cat = app.get("/v1/evals/sets/main/cases/cat", params={"target": "bot"}).json()
        assert cat["plays"] == "page"
        assert cat["proof"] == {
            "screenshots": "off",
            "video": "always",
            "own": {"video": "always"},
            "target": {"screenshots": "off", "video": "failed"},
        }


def test_a_targets_proof_is_never_written_into_evalution_yaml(tmp_path) -> None:
    targets = {"bot": {**HTTP, "page": MAP}}
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": targets}))
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as app:
        said = app.put("/v1/targets/bot/page/proof", json={"video": "off"})
        assert said.status_code == 400 and "evalution.yaml" in said.json()["detail"]


def test_a_tests_proof_is_set_and_cleared_without_changing_what_it_checks(client, tmp_path) -> None:
    with client(page=MAP) as app:
        url = "/v1/evals/sets/main/cases/dog/proof"
        said = app.put(url, params={"target": "bot"}, json={"screenshots": "off"})
        assert said.status_code == 200, said.text
        assert said.json()["proof"]["screenshots"] == "off"
        assert said.json()["proof"]["own"] == {"screenshots": "off"}
        file = app.get("/v1/evals/sets/main/cases/dog/file", params={"target": "bot"}).text
        assert json.loads(file)["page"] == {"proof": {"screenshots": "off"}}

        cleared = app.put(url, params={"target": "bot"}, json={"screenshots": None}).json()
        assert cleared["proof"]["own"] == {} and cleared["proof"]["screenshots"] == "always"
        file = app.get("/v1/evals/sets/main/cases/dog/file", params={"target": "bot"}).text
        assert "page" not in json.loads(file), "nothing of its own left"

        assert app.put(url, params={"target": "bot"}, json={"video": "x"}).status_code == 400
        assert app.put(url, params={"target": "bot"}, json={}).status_code == 400


def _record(screenshots: str | None, video: str | None) -> PageRecord:
    files = ("turn-1.png", "turn-2.png", *((VIDEO,) if video else ()))
    return PageRecord(
        turns=(PageTurn(shot="turn-1.png"), PageTurn(shot="turn-2.png")),
        files=files,
        video_policy=video,
        screenshot_policy=screenshots,
    )


@pytest.mark.parametrize(
    ("screenshots", "video", "outcome", "left"),
    [
        # On failure: a passed try's go once judged; a failed try's stay.
        ("failed", "failed", Outcome.PASSED, []),
        ("failed", "failed", Outcome.FAILED, ["try.webm", "turn-1.png", "turn-2.png"]),
        # Always: kept, passed or not.
        ("always", "always", Outcome.PASSED, ["try.webm", "turn-1.png", "turn-2.png"]),
        # Each part by its own setting.
        ("always", "failed", Outcome.PASSED, ["turn-1.png", "turn-2.png"]),
        ("failed", "always", Outcome.PASSED, ["try.webm"]),
        # A try kept before proof could be set kept every screenshot.
        (None, "failed", Outcome.PASSED, ["turn-1.png", "turn-2.png"]),
    ],
)
async def test_a_passed_trys_proof_is_deleted_as_its_setting_says(
    tmp_path, screenshots, video, outcome, left
) -> None:
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"web_dir": "none"}))
    runs = Runs(Targets(load(tmp_path / "evalution.yaml")), None)
    files = runs.store.files
    assert isinstance(files, LocalFiles)
    for name in ("turn-1.png", "turn-2.png", VIDEO):
        files.put("r1", "dog", 1, name, b"kept")
    evidence = TryEvidence(case="dog", n=1, t0=0.0, seconds=1.0, page=_record(screenshots, video))
    await runs._drop_passed_proof("r1", evidence, outcome)
    assert files.list("r1", "dog", 1) == left


# The Run dialog's words


def _case(stem: str, proof: dict | None = None) -> Case:
    data: dict = {"name": stem, "turns": [{"message": "Hi"}, {"message": "And?"}]}
    if proof is not None:
        data["page"] = {"proof": proof}
    return parse_case(data, stem, stem)


def test_the_run_dialog_says_how_each_test_plays_what_it_keeps_and_how_long() -> None:
    page = PageMap.model_validate(MAP)
    runs = [{"cases": {"a": {"tries": [{"seconds": 38.0}, {"seconds": 42.0}]}}}]
    found = plays("relay-lite", [_case("a"), _case("b"), _case("c")], runs, page)
    assert [line["text"] for line in found] == [
        "3 tests through relay-lite's page, one at a time, about 55 s a try; "
        "screenshots of every message, video when a try fails."
    ]
    assert found[0]["how"] == "page" and found[0]["cases"] == ["a", "b", "c"]

    # A test's own proof changes what the dialog says of it.
    found = plays("relay-lite", [_case("a"), _case("b", {"screenshots": "off"})], [], page)
    assert [line["text"] for line in found] == [
        "1 test through relay-lite's page, one at a time, about 60 s a try; "
        "screenshots of every message, video when a try fails.",
        "1 test through relay-lite's page, one at a time, about 60 s a try; "
        "no screenshots, video when a try fails.",
    ]

    found = plays("bot", [_case("a"), _case("b")], [], None)
    assert [line["text"] for line in found] == ["2 tests through bot's API, about 60 s a try."]
    assert found[0]["how"] == "api" and found[0]["kept"] is None

    only = plays("bot", [_case("a")], [], page, only=True)
    assert only[0]["how"] == "page_only"
    assert only[0]["text"].startswith("1 test through bot's page only, one at a time")


def test_rough_times_and_proof_in_words() -> None:
    assert about(3) == "about 5 s" and about(41) == "about 40 s" and about(200) == "about 3 min"
    off = read_map({**MAP, "proof": {"screenshots": "off", "video": "off"}}).proof
    assert proof_words(off) == "no screenshots or video"


def test_the_estimate_says_how_its_tests_play(client) -> None:
    with client() as app:
        asked = {"target": "bot", "set": "main"}
        found = app.post("/v1/evals/runs/estimate", json=asked).json()
        assert [line["text"] for line in found["plays"]] == [
            "2 tests through bot's API, about 30 s a try."
        ]
