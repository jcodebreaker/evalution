"""What the page reads of a try played through the app's page (ADR 0010): its kept files over the
API, the try view's page record, and how a target plays, in plain words."""

from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load
from evalution.core.evidence import (
    Clipped,
    ConsoleLine,
    FailedRequest,
    PageRecord,
    PageTurn,
    TryEvidence,
    TurnRecord,
)
from evalution.core.judgement import CheckKind, CheckResult, Decider, Outcome, Role, Route
from evalution.core.rules import judgement
from evalution.results.migrate import upcast
from evalution.results.store import RunStore
from evalution.storage import Database
from evalution.storage.files import LocalFiles
from evalution.suite.store import import_sets

PNG = b"\x89PNG\r\n\x1a\nnot really"
PAGE = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {"in": {"role": "main", "name": "Chat"}},
    "busy": [{"role": "button", "name": "Stop"}],
}


@pytest.fixture
def home(tmp_path):
    targets = {
        "bot": {"adapter": "http", "http": {"send": {"url": "x"}}},
        "paged": {"adapter": "http", "http": {"send": {"url": "x"}}, "page": PAGE},
        "broken": {"adapter": "http", "http": {"send": {"url": "x"}}, "page": {"url": "/chat"}},
    }
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": targets}))
    main = tmp_path / "sets" / "bot" / "main"
    main.mkdir(parents=True)
    (main / "set.json").write_text(json.dumps({"name": "Main"}))
    (main / "dog.case.json").write_text(
        json.dumps(
            {"name": "Dog", "turns": [{"message": "Her name is Biscuit"}, {"message": "Name?"}]}
        )
    )
    docs = Database.open(load(tmp_path / "evalution.yaml").data_dir).docs()
    import_sets(docs, tmp_path / "sets")
    return tmp_path


@pytest.fixture
def client(home):
    with TestClient(create_app(load(home / "evalution.yaml"))) as made:
        yield made


def _check(id_: str, kind: CheckKind, outcome: Outcome, why: str) -> CheckResult:
    """A code check; the page's guide, as they do unless a test says the page decides."""
    role = Role.GUIDE if kind in (CheckKind.SHOWN, CheckKind.PAGE_HEALTH) else Role.MUST
    return CheckResult(
        id=id_, kind=kind, decided_by=Decider.CODE, role=role, outcome=outcome, why=why
    )


def _turn(message: str, reply: str, shown: str | None) -> TurnRecord:
    return TurnRecord(
        kind="message",
        message=Clipped(text=message),
        reply=Clipped(text=reply),
        started=0.0,
        ended=1.0,
        shown=Clipped(text=shown) if shown is not None else None,
    )


def _store(home, page: PageRecord | None) -> RunStore:
    """Run r1: one try of Dog, its second reply never shown when played through *page*."""
    checks = [_check("state:0", CheckKind.STATE, Outcome.PASSED, "holds Biscuit")]
    if page is not None:
        checks += [
            _check("shown:1", CheckKind.SHOWN, Outcome.PASSED, "turn 1's reply showed on the page"),
            _check("shown:2", CheckKind.SHOWN, Outcome.FAILED, "turn 2's reply never showed"),
            _check("page_health", CheckKind.PAGE_HEALTH, Outcome.FAILED, "1 console error(s)"),
        ]
    found = judgement(checks, None, Route.UNKNOWN)
    doc = upcast(
        {
            "run": "r1",
            "target": "bot",
            "set": "main",
            "started_at": 1.0,
            "status": "done",
            "cases": {
                "dog": {
                    "name": "Dog",
                    "turns": [{"message": "Her name is Biscuit"}, {"message": "Name?"}],
                    "expected": {},
                    "reply_rubric": [],
                    "tries": [
                        {
                            "n": 1,
                            "tools": 0.0,
                            "reply": 1.0,
                            "seconds": 3.0,
                            "tokens": 10,
                            "error": None,
                            "messages": 2,
                        }
                    ],
                }
            },
        }
    )
    doc["cases"]["dog"]["tries"][0]["judgements"] = [found.model_dump(mode="json")]
    store = RunStore(Database.open(load(home / "evalution.yaml").data_dir).docs())
    turns = (
        _turn("Her name is Biscuit", "Lovely!", "Lovely!" if page else None),
        _turn("Name?", "Biscuit", None),
    )
    kept = TryEvidence(case="dog", n=1, t0=0.0, seconds=3.0, turns=turns, page=page)
    store.replace(doc, [kept])
    return store


SHOWN = PageRecord(
    turns=(
        PageTurn(shown_at=1.4, shot="turn-1.png"),
        PageTurn(instead=Clipped(text="Couldn't load this reply"), shot="turn-2.png"),
    ),
    console=(
        ConsoleLine(level="warning", text=Clipped(text="slow"), at=0.2),
        ConsoleLine(level="error", text=Clipped(text="TypeError: parts"), at=2.5),
    ),
    failed_requests=(FailedRequest(method="GET", path="/v1/me", status=500, at=0.1),),
    files=("turn-1.png", "turn-2.png", "try.webm"),
    video_missing=None,
)


def test_a_kept_file_is_served_with_its_type_and_nothing_else_is(client, home) -> None:
    _store(home, SHOWN)
    files = LocalFiles(load(home / "evalution.yaml").data_dir)
    files.put("r1", "dog", 1, "turn-1.png", PNG)
    files.put("r1", "dog", 1, "try.webm", b"webm")
    files.put("r1", "dog", 1, "notes.txt", b"text")
    url = "/v1/evals/runs/r1/cases/dog/tries/1/files/"

    shot = client.get(url + "turn-1.png")
    assert shot.status_code == 200 and shot.content == PNG
    assert shot.headers["content-type"] == "image/png"
    assert shot.headers["x-content-type-options"] == "nosniff"
    assert client.get(url + "try.webm").headers["content-type"] == "video/webm"
    other = client.get(url + "notes.txt")
    assert other.headers["content-type"] == "application/octet-stream"
    assert other.headers["content-disposition"] == "attachment"

    # Missing, out of its folder, or of a try, test or run that isn't there: not found.
    assert client.get(url + "turn-2.png").status_code == 404, "named, but not kept here"
    assert client.get(url + ".hidden").status_code == 404
    assert client.get(url + "..%2Fx.png").status_code == 404
    assert client.get("/v1/evals/runs/r1/cases/dog/tries/2/files/turn-1.png").status_code == 404
    assert client.get("/v1/evals/runs/r1/cases/cat/tries/1/files/turn-1.png").status_code == 404
    assert client.get("/v1/evals/runs/r9/cases/dog/tries/1/files/turn-1.png").status_code == 404


def test_a_workspace_without_files_serves_none(home) -> None:
    _store(home, SHOWN)
    LocalFiles(load(home / "evalution.yaml").data_dir).put("r1", "dog", 1, "turn-1.png", PNG)
    app = create_app(load(home / "evalution.yaml"))
    with TestClient(app) as client:
        url = "/v1/evals/runs/r1/cases/dog/tries/1/files/turn-1.png"
        assert client.get(url).status_code == 200
        # Hosted, a workspace keeps no files: a page is only played locally or in CI.
        ws = app.state.workspaces.get("local")
        ws.targets.files = None
        assert client.get(url).status_code == 404


def test_the_try_view_lays_the_page_over_its_turns(client, home) -> None:
    _store(home, SHOWN)
    LocalFiles(load(home / "evalution.yaml").data_dir).put("r1", "dog", 1, "turn-1.png", PNG)
    shown = client.get("/v1/evals/runs/r1/cases/dog/compare").json()

    assert shown["layers"] == {"stored": "pass", "answered": "absent", "shown": "fail"}
    said = [s for s in shown["steps"] if s["kind"] == "say"]
    first, second = (s["page"] for s in said)
    assert first == {
        "shown": "Lovely!",
        "shown_at": 1.4,
        "instead": "",
        "shot": "turn-1.png",
        "shot_url": "/v1/evals/runs/r1/cases/dog/tries/1/files/turn-1.png",
        "check": {"outcome": "passed", "why": "turn 1's reply showed on the page"},
    }
    assert second["shown"] is None and second["instead"] == "Couldn't load this reply"
    assert second["shot_url"] is None, "not kept on this server"
    assert second["check"]["outcome"] == "failed"

    page = shown["page"]
    assert [c["level"] for c in page["console"]] == ["warning", "error"]
    assert page["failed_requests"] == [
        {"method": "GET", "path": "/v1/me", "status": 500, "at": 0.1}
    ]
    assert page["health"] == {"outcome": "failed", "why": "1 console error(s)"}
    assert page["without_page"] == "passed", "its state held: through the API alone, it passed"
    assert page["video"] == {"name": "try.webm", "url": None}
    assert page["video_missing"] is None and page["interrupted"] is False
    kinds = {c["id"]: c["kind"] for c in shown["explain"]["code"]["checks"]}
    assert kinds["shown:2"] == "shown" and kinds["page_health"] == "page_health"


def test_a_try_through_the_api_has_no_page(client, home) -> None:
    _store(home, None)
    shown = client.get("/v1/evals/runs/r1/cases/dog/compare").json()
    assert shown["page"] is None
    assert shown["layers"]["shown"] == "absent"
    assert all("page" not in s for s in shown["steps"])


def test_a_target_says_whether_it_plays_through_its_page(client) -> None:
    assert client.get("/v1/targets/bot").json()["page"] is None
    paged = client.get("/v1/targets/paged").json()["page"]
    assert paged["error"] is None
    words = {row["what"]: row["where"] for row in paged["map"]}
    assert words["Message box"] == "textbox named “Message”"
    assert words["Send"] == "button named “Send”"
    assert words["Busy while"] == "button named “Stop”"
    assert words["Replies"] == "main named “Chat”"
    assert words["Opens"] == "/chat"
    assert "New chat" not in words
    broken = client.get("/v1/targets/broken").json()["page"]
    assert broken["map"] == [] and "{copy_page}" in broken["error"]
