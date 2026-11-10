"""What a try played through the app's page keeps and how it is checked (ADR 0010): its page
record, the run's files, the page's checks, and the layers a verdict reads from them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalution.core.evidence import (
    ITEM_BYTES,
    PAGE_LINES,
    Clipped,
    ConsoleLine,
    FailedRequest,
    Failure,
    PageRecord,
    PageTurn,
    StateRecord,
    TraceStatus,
    TryEvidence,
    TurnRecord,
)
from evalution.core.judgement import CheckKind, Outcome, Role, Route, WhyUnjudged
from evalution.core.rendered import NOTHING_SHOWN
from evalution.core.rules import (
    RULES_VERSION,
    current,
    first_reason,
    judge_try,
    judgement,
    layers,
    page_health_words,
)
from evalution.judging.checks import code_checks
from evalution.judging.claims import index_of
from evalution.results.migrate import upcast
from evalution.results.record import checks_made_under, regrade_rules
from evalution.results.store import RUN, RunStore
from evalution.results.upload import read_upload
from evalution.storage import Database
from evalution.storage.files import FileNameError, LocalFiles
from evalution.suite.cases import CaseError, parse_case

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_KINDS = (CheckKind.SHOWN, CheckKind.PAGE_HEALTH)


def _turn(reply: str, shown: str | None = None, error: str | None = None) -> TurnRecord:
    return TurnRecord(
        kind="message",
        message=Clipped(text="Our dog is called Biscuit"),
        reply=Clipped(text=reply),
        started=0.0,
        ended=1.0,
        error=error,
        shown=Clipped(text=shown) if shown is not None else None,
    )


def _evidence(*turns: TurnRecord, page: PageRecord | None = None, **fields) -> TryEvidence:
    return TryEvidence(case="c", n=1, t0=0.0, seconds=2.0, turns=turns, page=page, **fields)


def _page(*turns: PageTurn, **fields) -> PageRecord:
    return PageRecord(turns=turns, **fields)


def _case(**fields) -> object:
    return parse_case({"name": "n", "turns": [{"message": "hi"}], **fields}, "c")


def _checks(case, evidence: TryEvidence) -> dict:
    return {c.id: c for c in code_checks(case, evidence, [], 1.0, {"tools": 0.8}).checks}


# The page record


def test_a_page_record_round_trips_capped() -> None:
    long = "x" * (ITEM_BYTES + 10)
    page = _page(
        PageTurn(shown_at=1.4, shot="turn-1.png"),
        PageTurn(instead=Clipped(text=long)),
        console=tuple(
            ConsoleLine(level="error", text=Clipped(text=long), at=0.5)
            for _ in range(PAGE_LINES + 3)
        ),
        failed_requests=(FailedRequest(method="GET", path="/v1/me/assistant", status=500, at=0.2),),
        files=("turn-1.png", "video.webm"),
        video_missing=None,
        interrupted=True,
    )
    kept = _evidence(_turn("hi", "hi"), _turn("ok", None), page=page).capped()
    assert kept.page is not None
    assert kept.page.turns[1].instead.elided > 0 and kept.page.turns[1].instead.sha256
    assert len(kept.page.console) == PAGE_LINES and kept.page.left_out == 3
    assert all(line.text.elided for line in kept.page.console)
    again = TryEvidence.model_validate(json.loads(kept.model_dump_json()))
    assert again == kept and again.page.failed_requests[0].status == 500
    assert FailedRequest(method="POST", path="/send", at=1.0).status is None, "no answer"


def test_old_evidence_without_a_page_still_reads() -> None:
    old = {
        "case": "c",
        "n": 1,
        "t0": 0.0,
        "seconds": 1.0,
        "turns": [
            {
                "kind": "message",
                "message": {"text": "hi"},
                "reply": {"text": "hello"},
                "started": 0.0,
                "ended": 1.0,
            }
        ],
    }
    found = TryEvidence.model_validate(old)
    assert found.page is None and found.turns[0].shown is None
    assert found.capped().page is None


# The run's files


def test_the_file_store_keeps_lists_and_deletes_a_run(tmp_path) -> None:
    files = LocalFiles(tmp_path)
    files.put("run-1", "greets", 1, "turn-1.png", b"png")
    files.put("run-1", "greets", 1, "turn-1.png", b"png2")
    files.put("run-1", "greets", 2, "video.webm", b"webm")
    files.put("run-2", "greets", 1, "turn-1.png", b"other")
    assert files.list("run-1", "greets", 1) == ["turn-1.png"]
    assert files.list("run-1", "nope", 1) == []
    with files.open("run-1", "greets", 1, "turn-1.png") as f:
        assert f.read() == b"png2"
    where = files.path("run-1", "greets", 3, "video.webm")
    assert where == tmp_path / "files" / "run-1" / "greets.3" / "video.webm"
    assert where.parent.is_dir() and not where.exists()
    files.put("run-1", "greets", 2, "try.webm", b"webm")
    assert files.delete("run-1", "greets", 2, "try.webm") is True
    assert files.delete("run-1", "greets", 2, "try.webm") is False
    assert files.list("run-1", "greets", 2) == ["video.webm"]
    with pytest.raises(FileNameError):
        files.delete("run-1", "greets", 2, "../video.webm")
    assert files.delete_run("run-1") == 2
    assert not (tmp_path / "files" / "run-1").exists()
    assert files.list("run-2", "greets", 1) == ["turn-1.png"]
    assert files.delete_run("run-1") == 0


@pytest.mark.parametrize(
    ("run", "case", "n", "name"),
    [
        ("run-1", "greets", 1, "../x.png"),
        ("run-1", "greets", 1, "a/b.png"),
        ("run-1", "greets", 1, "a\\b.png"),
        ("run-1", "greets", 1, ".hidden"),
        ("run-1", "greets", 1, "a..png"),
        ("run-1", "greets", 1, ""),
        ("../run", "greets", 1, "x.png"),
        ("run-1", "../greets", 1, "x.png"),
        ("run-1", "greets", 0, "x.png"),
    ],
)
def test_the_file_store_refuses_a_name_that_could_leave_its_folder(
    tmp_path, run, case, n, name
) -> None:
    files = LocalFiles(tmp_path)
    with pytest.raises(FileNameError):
        files.put(run, case, n, name, b"x")
    with pytest.raises(FileNameError):
        files.delete_run("../x")
    assert not (tmp_path / "files").exists() or not any((tmp_path / "files").rglob("*.png"))


def test_deleting_a_run_deletes_its_evidence_and_files(tmp_path) -> None:
    files = LocalFiles(tmp_path)
    store = RunStore(Database.open(tmp_path).docs(), files=files)
    store.write({"run": "run-1", "cases": {}})
    store.write({"run": "run-2", "cases": {}})
    store.docs.append("evidence", "run-1", _evidence(_turn("hi")).model_dump(mode="json"))
    store.ask_stop("run-1")
    files.put("run-1", "c", 1, "turn-1.png", b"png")
    assert store.delete_run("run-1") is True
    assert store.docs.get(RUN, "run-1") is None and store.evidence("run-1") == []
    assert not store.stop_asked("run-1") and files.list("run-1", "c", 1) == []
    assert store.docs.get(RUN, "run-2") is not None
    assert store.delete_run("run-1") is False
    with pytest.raises(ValueError, match="run id"):
        store.delete_run("../x")
    # Without a data folder of its own (hosted) there are no files to delete.
    assert RunStore(store.docs).delete_run("run-2") is True


def test_a_runs_files_go_once_they_are_older_than_keep_days(tmp_path) -> None:
    import os
    import time

    from evalution.config import load
    from evalution.storage.files import DAY

    files = LocalFiles(tmp_path)
    assert files.prune(30) == [], "nothing kept yet"
    files.put("old-run", "c", 1, "turn-1.png", b"png")
    files.put("old-run", "c", 2, "video.webm", b"webm")
    files.put("new-run", "c", 1, "turn-1.png", b"png")
    now = time.time()
    for path in (tmp_path / "files" / "old-run").rglob("*"):
        if path.is_file():
            os.utime(path, (now - 31 * DAY, now - 31 * DAY))
    assert files.prune(30, now) == ["old-run"]
    assert files.list("old-run", "c", 1) == [] and files.list("old-run", "c", 2) == []
    assert files.list("new-run", "c", 1) == ["turn-1.png"]
    # Kept 30 days unless the config says otherwise.
    (tmp_path / "evalution.yaml").write_text("files:\n  keep_days: 7\n")
    assert load(tmp_path / "evalution.yaml").files.keep_days == 7
    (tmp_path / "none.yaml").write_text("{}\n")
    assert load(tmp_path / "none.yaml").files.keep_days == 30
    (tmp_path / "bad.yaml").write_text("files:\n  keep_days: 0\n")
    with pytest.raises(ValueError, match=r"files\.keep_days"):
        load(tmp_path / "bad.yaml")


def test_a_run_is_deleted_through_the_api_with_its_files(tmp_path) -> None:
    import yaml
    from fastapi.testclient import TestClient

    from evalution.api.app import create_app
    from evalution.config import load

    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        runs = client.app.state.workspaces.get("local").runs
        runs._orphans_marked = True  # run-2 is playing here, not left by an earlier server
        done = {"run": "run-1", "target": "bot", "set": "main", "status": "done", "cases": {}}
        runs.store.write(done)
        runs.store.write({**done, "run": "run-2", "status": "running"})
        runs.store.files.put("run-1", "c", 1, "turn-1.png", b"png")
        assert client.delete("/v1/evals/runs/run-2").status_code == 409
        gone = client.delete("/v1/evals/runs/run-1")
        assert gone.status_code == 200 and gone.json() == {"run": "run-1", "deleted": True}
        assert runs.store.load("run-1") is None
        assert runs.store.files.list("run-1", "c", 1) == []
        assert client.delete("/v1/evals/runs/run-1").status_code == 404


def test_an_uploaded_run_keeps_its_files_names_only() -> None:
    raw = json.loads((FIXTURES / "runs" / "v0-first.json").read_text(encoding="utf-8"))
    run = upcast(raw)
    page = _page(PageTurn(shot="turn-1.png"), files=("turn-1.png", "video.webm"))
    played = _evidence(_turn("hi", "hi"), page=page).model_copy(update={"case": "test-a"})
    _, kept = read_upload({"run": run, "evidence": [played.model_dump(mode="json")]})
    assert kept[0].page is not None and kept[0].page.files == ("turn-1.png", "video.webm")


# The page's checks


def test_the_reply_showing_is_checked_on_each_page_turn() -> None:
    page = _page(
        PageTurn(shown_at=1.0, shot="turn-1.png"),
        PageTurn(instead=Clipped(text="Couldn't load this reply")),
        PageTurn(),
        PageTurn(),
    )
    evidence = _evidence(
        _turn("Your dog is **Biscuit**.\n\n- a walk\n- [the vet](https://x.test)", None),
        _turn("Biscuit loves walks", None),
        _turn("Biscuit", "Something else entirely"),
        _turn("", None, error="the app answered 500"),
        page=page,
    )
    evidence = evidence.model_copy(
        update={
            "turns": (
                evidence.turns[0].model_copy(
                    update={"shown": Clipped(text="Your dog is Biscuit.\na walk\nthe vet")}
                ),
                *evidence.turns[1:],
            )
        }
    )
    found = _checks(_case(), evidence)
    assert found["shown:1"].outcome is Outcome.PASSED
    assert [r.ref for r in found["shown:1"].evidence] == ["page:1", "shot:turn-1.png"]
    assert found["shown:2"].outcome is Outcome.FAILED
    assert "never showed" in found["shown:2"].why and "Couldn't load" in found["shown:2"].why
    assert found["shown:3"].outcome is Outcome.FAILED
    assert found["shown:4"].outcome is Outcome.UNJUDGED
    assert found["shown:4"].unjudged is WhyUnjudged.NO_REPLY
    assert all(c.role is Role.GUIDE for c in found.values() if c.kind in PAGE_KINDS)


def test_the_page_s_health_fails_on_an_error_or_a_failed_request() -> None:
    def health(**fields) -> object:
        return _checks(_case(), _evidence(_turn("hi", "hi"), page=_page(**fields)))["page_health"]

    assert health().outcome is Outcome.PASSED
    warned = (ConsoleLine(level="warning", text=Clipped(text="slow"), at=0.1),)
    assert health(console=warned).outcome is Outcome.PASSED, "a warning decides nothing"
    errored = health(
        console=(*warned, ConsoleLine(level="error", text=Clipped(text="TypeError: x"), at=0.2))
    )
    assert errored.outcome is Outcome.FAILED and "TypeError" in errored.why
    assert [r.ref for r in errored.evidence] == ["console:2"]
    lost = health(failed_requests=(FailedRequest(method="GET", path="/v1/me", at=0.3),))
    assert lost.outcome is Outcome.FAILED and "no answer" in lost.why
    assert [r.ref for r in lost.evidence] == ["request:1"]


def test_a_test_s_own_page_words_are_read_from_what_showed() -> None:
    case = _case(page={"contains": ["Biscuit"], "lacks": ["error"]})
    assert case.shown_contains == ("Biscuit",) and case.shown_lacks == ("error",)
    shown = _evidence(_turn("It's Biscuit", "It's **Biscuit**"), page=_page(PageTurn()))
    found = _checks(case, shown)
    assert found["shown:contains:0"].outcome is Outcome.PASSED
    assert found["shown:lacks:0"].outcome is Outcome.PASSED
    errored = _evidence(_turn("It's Biscuit", "Biscuit. An error occurred"), page=_page(PageTurn()))
    assert _checks(case, errored)["shown:lacks:0"].outcome is Outcome.FAILED
    unseen = _evidence(_turn("It's Biscuit"), page=_page(PageTurn()))
    never = _checks(case, unseen)
    assert never["shown:contains:0"].outcome is never["shown:lacks:0"].outcome is Outcome.FAILED
    # The reply's own words still read the reply, never what the page showed.
    replied = _case(reply={"contains": ["Biscuit"]})
    assert _checks(replied, unseen)["reply:contains:0"].outcome is Outcome.PASSED
    for bad in ({"contains": "x"}, {"decides": "yes"}, {"shows": []}, []):
        with pytest.raises(CaseError, match="page"):
            _case(page=bad)


def test_page_checks_guide_unless_the_test_says_they_decide() -> None:
    state = StateRecord(index=0, key="memory_contains", value="Biscuit", at=0.5, ok=True)
    evidence = _evidence(
        _turn("Biscuit", None), page=_page(PageTurn()), states=(state,), trace=TraceStatus.COMPLETE
    )
    guided = code_checks(_case(), evidence, [], 1.0, {"tools": 0.8}).checks
    assert judge_try(guided) is Outcome.PASSED, "the page's fail is shown, not counted"
    decided = code_checks(_case(page={"decides": True}), evidence, [], 1.0, {"tools": 0.8}).checks
    assert judge_try(decided) is Outcome.FAILED
    assert all(c.role is Role.MUST for c in decided if c.kind in PAGE_KINDS)
    # With nothing else to check, a guide doesn't stand in for the try having been seen.
    alone = _evidence(_turn("hi", "hi"), page=_page(PageTurn()), trace=TraceStatus.COMPLETE)
    found = _checks(_case(), alone)
    assert found["turn:seen"].outcome is Outcome.PASSED and found["shown:1"].role is Role.GUIDE
    assert "turn:seen" not in _checks(_case(page={"decides": True}), alone)


def test_an_api_try_gets_no_page_checks() -> None:
    """The control: a try played through the API alone is judged as before."""
    case = _case(reply={"contains": ["Biscuit"]}, page={"decides": True, "contains": ["Biscuit"]})
    api = _evidence(_turn("Biscuit", None))
    found = code_checks(case, api, [], 1.0, {"tools": 0.8}).checks
    assert not [c for c in found if c.kind in PAGE_KINDS]
    assert [c.id for c in found] == ["reply:contains:0"] and judge_try(found) is Outcome.PASSED
    assert layers(judgement(found, None, Route.UNKNOWN))["shown"] == "absent"


def test_a_try_cut_short_reads_no_page_words() -> None:
    case = _case(page={"contains": ["Biscuit"]})
    cut = _evidence(
        _turn("", None, error="lost"),
        page=_page(PageTurn(), interrupted=True),
        failure=Failure.ADAPTER,
        error="lost",
    )
    found = _checks(case, cut)
    assert "shown:contains:0" not in found and found["shown:1"].outcome is Outcome.UNJUDGED


# Layers


def test_the_layers_say_which_held_stored_answered_not_shown() -> None:
    """The mockup's try 3: it remembered and answered, and the person never saw it."""
    state = StateRecord(index=0, key="memory_contains", value="Biscuit", at=0.5, ok=True)
    evidence = _evidence(
        _turn("Your dog is Biscuit", None),
        page=_page(PageTurn(instead=Clipped(text="Couldn't load this reply"))),
        states=(state,),
        trace=TraceStatus.COMPLETE,
    )
    case = _case(reply={"contains": ["Biscuit"]})
    found = judgement(
        code_checks(case, evidence, [], 1.0, {"tools": 0.8}).checks, None, Route.UNKNOWN
    )
    assert layers(found) == {"stored": "pass", "answered": "pass", "shown": "fail"}
    assert found.outcome is Outcome.PASSED, "through the API alone it passed"
    assert layers(judgement([], None, Route.UNKNOWN)) == {
        "stored": "absent",
        "answered": "absent",
        "shown": "absent",
    }


def _layered(case, evidence: TryEvidence) -> dict[str, str]:
    return layers(
        judgement(code_checks(case, evidence, [], 1.0, {"tools": 0.8}).checks, None, Route.UNKNOWN)
    )


@pytest.mark.parametrize(
    ("failure", "error", "refused", "unanswered", "answered", "outcome"),
    [
        (Failure.TRANSIENT, "busy or down: answered 503", 503, False, "unjudged", "unjudged"),
        (Failure.TRANSIENT, "no answer in 30 s", None, True, "unjudged", "unjudged"),
        (Failure.APP, "the app answered the page's message with 500", 500, False, "fail", "failed"),
    ],
)
def test_a_message_the_app_refused_or_never_took_is_answered_not_shown(
    failure, error, refused, unanswered, answered, outcome
) -> None:
    """The app's, under answered: the page did its part, and its request carrying the message
    failing is no fault of the page's health."""
    message = FailedRequest(method="POST", path="/send", status=refused, at=0.1, message=True)
    evidence = _evidence(
        _turn("", None, error=error),
        page=_page(
            PageTurn(refused=refused, unanswered=unanswered),
            failed_requests=(message,) if refused else (),
        ),
        failure=failure,
        error=error,
    )
    case = _case(reply={"contains": ["Biscuit"]}, page={"decides": True})
    found = _checks(case, evidence)
    assert found["page_health"].outcome is Outcome.PASSED
    assert found["shown:1"].outcome is Outcome.UNJUDGED
    assert _layered(case, evidence) == {
        "stored": "absent",
        "answered": answered,
        "shown": "unjudged",
    }
    assert judge_try(found.values()).value == outcome


def test_the_shown_layer_can_leave_the_pages_health_apart() -> None:
    """Every reply showed, and a request failed: shown fails, but not what showed of the replies."""
    evidence = _evidence(
        _turn("Your dog is Biscuit", "Your dog is Biscuit"),
        page=_page(
            PageTurn(shown_at=1.0),
            failed_requests=(FailedRequest(method="GET", path="/v1/me", status=503, at=0.2),),
        ),
    )
    found = judgement(
        code_checks(_case(), evidence, [], 1.0, {"tools": 0.8}).checks, None, Route.UNKNOWN
    )
    assert layers(found)["shown"] == "fail"
    assert layers(found, health=False)["shown"] == "pass"


@pytest.mark.parametrize(
    ("why", "said"),
    [
        (
            "1 console error(s), first 'Failed to load resource: the server responded with a "
            "status of 503 (Service Unavailable)'; 1 failed request(s), first POST "
            "/v1/sessions/95834ea5/events: 503",
            "the app answered a request from the page with an error, 503",
        ),
        (
            "2 failed request(s), first GET /v1/me: 404",
            "the app turned down a request from the page, 404 (and 1 more)",
        ),
        (
            "1 failed request(s), first GET /v1/me: no answer",
            "a request from the page to the app got no answer",
        ),
        ("3 console error(s), first 'TypeError: parts'", "the page logged 3 errors"),
        ("something else", "something else"),
    ],
)
def test_a_failed_page_health_reads_whose_it_is_not_a_log_line(why: str, said: str) -> None:
    assert page_health_words(why) == said


def test_a_trys_reason_says_the_pages_health_in_plain_words() -> None:
    """Run 20261108-162620-839b: the reason a row read was the log line itself."""
    evidence = _evidence(
        _turn("Your dog is Biscuit", "Your dog is Biscuit"),
        page=_page(
            PageTurn(shown_at=1.0),
            failed_requests=(FailedRequest(method="POST", path="/events", status=503, at=0.2),),
        ),
    )
    found = judgement(
        code_checks(_case(page={"decides": True}), evidence, [], 1.0, {"tools": 0.8}).checks,
        None,
        Route.UNKNOWN,
    )
    assert first_reason(found) == "the app answered a request from the page with an error, 503"
    page_health = next(c for c in found.checks if c.kind is CheckKind.PAGE_HEALTH)
    assert "POST /events: 503" in page_health.why, "the check itself keeps the detail"


def test_a_page_that_showed_nothing_is_still_the_shown_layers() -> None:
    evidence = _evidence(
        _turn("", None, error=NOTHING_SHOWN),
        page=_page(PageTurn(instead=Clipped(text="…")), page_only=True),
        failure=Failure.APP,
        error=NOTHING_SHOWN,
    )
    assert _layered(_case(), evidence) == {
        "stored": "absent",
        "answered": "absent",
        "shown": "fail",
    }


def test_a_broken_page_map_is_no_layer_of_the_apps() -> None:
    evidence = _evidence(
        _turn("", None, error="the page has no button 'Send'"),
        page=_page(PageTurn()),
        failure=Failure.PAGE_MAP,
        error="the page has no button 'Send'",
    )
    assert _layered(_case(), evidence)["answered"] == "absent"


def test_a_claim_may_cite_the_page() -> None:
    page = _page(
        PageTurn(shot="turn-1.png"),
        console=(ConsoleLine(level="error", text=Clipped(text="TypeError: boom"), at=0.1),),
        failed_requests=(FailedRequest(method="GET", path="/x", status=500, at=0.1),),
        files=("turn-1.png",),
    )
    index = index_of([_evidence(_turn("hi", "hi there"), page=page)])
    assert {"page:1", "shot:turn-1.png", "console:1", "request:1"} <= index.refs
    assert index.has_quote("TypeError: boom") and index.has_quote("hi there")
    assert "page:1" not in index_of([_evidence(_turn("hi"))]).refs


def test_a_try_judged_under_the_rules_before_page_checks_says_so() -> None:
    old = judgement([], None, Route.UNKNOWN).model_copy(update={"rules": RULES_VERSION - 1})
    attempt = {"judgements": [old.model_dump(mode="json")]}
    run = {"run": "r", "rules": RULES_VERSION - 1, "cases": {"c": {"tries": [attempt]}}}
    assert regrade_rules(run) == 1
    found = current(attempt)
    assert (found.rules, found.rules_from) == (RULES_VERSION, RULES_VERSION - 1)
    assert checks_made_under(run) == RULES_VERSION - 1, "not compared with today's as like"


def test_a_rendered_table_counts_as_shown() -> None:
    """relay-lite numbers a table's rows and drops its bars: the reply still showed."""
    reply = "| Fruit | Colour |\n|---|---|\n| Apple | Red |\n| Banana | Yellow |"
    shown = "Fruit\tColour\n1\tApple\tRed\n2\tBanana\tYellow"
    checks = _checks(_case(), _evidence(_turn(reply, shown), page=_page(PageTurn())))
    assert checks["shown:1"].outcome is Outcome.PASSED, checks["shown:1"].why


def test_the_failure_analysis_is_told_which_checks_failed() -> None:
    """A try that failed only on the page must say so to the model reading it."""
    from evalution.review.findings import try_input

    page = _page(PageTurn(instead=Clipped(text="Something went wrong")))
    case = _case(page={"decides": True})
    checks = code_checks(case, _evidence(_turn("Ok."), page=page), [], 1.0, {"tools": 0.8})
    attempt = {
        "n": 1,
        "tools": 1.0,
        "reply": 1.0,
        "seconds": 2,
        "judgements": [judgement(checks.checks, None, Route.EXPECTED).model_dump(mode="json")],
    }
    material = try_input({"turns": [{"message": "hi"}], "name": "n"}, attempt, [])
    assert "never showed on the page" in material
    assert "Something went wrong" in material
