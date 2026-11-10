"""False passes and false fails found in review. Each test asserts the correct behaviour; where
today's code is wrong it is a strict xfail, so it flips loudly when the fix lands.

``raises=AssertionError`` keeps the xfail honest: a broken rig raises something else
(:func:`given`) and shows as an error. The ``control`` tests are what keeps each one from being
vacuous: the same setup without the fault gets the right answer today.
"""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripted_agent import FACT_WORD, SET, TARGET, VALUES, StubJudge, StubLLM, what_it_did

from evalution import cli
from evalution.adapters.base import (
    AppError,
    Capabilities,
    Isolation,
    Session,
    Target,
    TargetGone,
    Turn,
    failure_of,
    may_have_heard,
)
from evalution.config import IsolationConfig
from evalution.core.evidence import (
    ITEM_BYTES,
    Clipped,
    Failure,
    ModelCallRecord,
    StateRecord,
    TraceStatus,
    TryEvidence,
    TurnEnd,
    TurnRecord,
)
from evalution.core.judgement import (
    Basis,
    CheckKind,
    Outcome,
    Role,
    Route,
    VerdictKind,
    WhyUnjudged,
)
from evalution.core.rules import (
    ADAPTER_BROKEN,
    APP_NOT_READY,
    PAGE_MAP_BROKEN,
    SERVER_RESTARTED,
    current,
    first_reason,
    judgement,
    label_of,
)
from evalution.core.trace import Call
from evalution.judging.checks import _answering, code_checks
from evalution.judging.claims import index_of, read_claim
from evalution.judging.steps import chosen_calls, grade_tools
from evalution.results.evidence import EVIDENCE
from evalution.results.record import verdict_kind, verdict_of
from evalution.results.store import RUN, RunLock, RunStore
from evalution.results.views import (
    _run_test,
    case_detail,
    case_history,
    case_row,
    report_facts,
    summary,
)
from evalution.review.findings import report_input
from evalution.review.health import _played, _verdicts, regressions
from evalution.runs import play, runner, trust
from evalution.runs.collect import TRACE_WAIT, Wanted, own_spans
from evalution.runs.isolated import NotIsolated
from evalution.runs.play import play_try, play_with_retry
from evalution.runs.preflight import forget_setup, passed_setups, remember_setup, setup_key
from evalution.runs.runner import STILL_WORKING, RunBusyError, Runs
from evalution.storage import Database
from evalution.suite.cases import parse_case
from evalution.suite.store import load_case
from evalution.traces.base import Span, SpanRead, nest
from evalution.traces.view import answered_at
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import AgentTurn


def known(reason: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


def given(condition: Any, what: str) -> None:
    """A precondition of the rig; failing it is an error, not the expected failure."""
    if not condition:
        raise RuntimeError(f"the rig is broken: {what}")


ASK = VALUES["ask"]["value"]
RUBRIC = {"reference": "Handles the request.", "rubric": ["Handles the request"]}
FORBIDDEN = "wipe_memory"


def _case(*turns: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {
        "name": "Proof case",
        "turns": list(turns),
        "reply": RUBRIC,
        "limits": {"tries": 1},
        **fields,
    }


def _verdict(doc: dict) -> str | None:
    ((stem, entry),) = doc["cases"].items()
    return _run_test(doc, stem, entry)["verdict"]


def _tries(doc: dict) -> list[dict]:
    return [t for c in doc["cases"].values() for t in c["tries"]]


def _status(doc: dict, name: str) -> list[str]:
    return [s["status"] for t in _tries(doc) for s in t["steps"] if s["expected"] == name]


FORBIDDEN_CALL = {"message": f"Please use {FORBIDDEN}."}
ASKS = {"message": ASK}


# 1. No traces sent


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param({"expected": [{"name": "memory_search"}], "never": [FORBIDDEN]}, id="route"),
        pytest.param({"never": [FORBIDDEN]}, id="never"),
    ],
)
async def test_1_no_traces_sent_is_unjudged_for_trace_checks(make_rig, tools) -> None:
    rig = make_rig(["sends_no_traces"])
    rig.add(_case(FORBIDDEN_CALL, tools=tools))
    doc = await rig.run()
    given(rig.agent.store is None, "its spans reach nothing")
    assert _verdict(doc) == "unjudged"


async def test_1_control_with_traces_the_same_try_fails_on_its_never_call(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(FORBIDDEN_CALL, tools={"never": [FORBIDDEN]}))
    doc = await rig.run()
    assert _verdict(doc) == "fail"
    assert [s["status"] for s in _tries(doc)[0]["steps"]] == ["never"]


async def test_1_a_reply_only_test_with_no_traces_sent_rests_on_the_model(make_rig) -> None:
    rig = make_rig(["sends_no_traces"])
    rig.add(_case(ASKS, tools={"expected": [{"name": "memory_search"}]}))
    doc = await rig.run()
    ((_, entry),) = doc["cases"].items()
    found = verdict_of(entry)
    assert (found.kind, found.basis) == (VerdictKind.PASS, Basis.MODEL_ONLY)
    judged = current(entry["tries"][0])
    assert judged.route is Route.UNKNOWN
    steps = next(c for c in judged.checks if c.id == "steps")
    assert (steps.role, steps.unjudged) == (Role.GUIDE, WhyUnjudged.NO_TRACES)
    assert doc["totals"]["passed_model_only"] == 1 and doc["totals"]["passed_checked"] == 0


# 2. Empty captured content


async def test_2_context_lacks_on_empty_content_is_unjudged(make_rig) -> None:
    rig = make_rig(["content_capture_off", "withdrawn_fact_still_sent"])
    rig.fill("forget-on-request")
    doc = await rig.run()
    # The context check can't say, so it doesn't pass. The test still fails, on what it can see:
    # the withdrawn fact is in the reply.
    assert set(_status(doc, "state:context_lacks")) == {"unjudged"}
    assert _verdict(doc) == "fail"


async def test_2_context_contains_on_empty_content_is_unjudged(make_rig) -> None:
    rig = make_rig(["content_capture_off"])
    rig.fill("compaction-keeps-early-decision")
    doc = await rig.run()
    assert _verdict(doc) == "unjudged"


async def test_2_control_captured_content_catches_the_same_bug(make_rig) -> None:
    rig = make_rig(["withdrawn_fact_still_sent"])
    rig.fill("forget-on-request")
    doc = await rig.run()
    assert _verdict(doc) == "fail"
    assert set(_status(doc, "state:context_lacks")) == {"missing"}


# 3. A state read that looked in the wrong place


LACKS_ONLY = (
    {"message": f"Something to remember: {VALUES['fact']}"},
    {"expect_state": {"memory_lacks": FACT_WORD}},
)


async def test_3_memory_lacks_on_a_wrong_namespace_is_unjudged(make_rig) -> None:
    rig = make_rig(["wrong_namespace"])
    rig.add(_case(*LACKS_ONLY))
    doc = await rig.run()
    given(any(FACT_WORD in m for p in rig.agent.people.values() for m in p.memory), "no fact held")
    assert _verdict(doc) == "unjudged"


async def test_3_control_a_memory_contains_before_it_protects_the_library_test(make_rig) -> None:
    """Why the library's forget test isn't open to this: it first checks the fact is there."""
    rig = make_rig(["wrong_namespace"])
    rig.fill("forget-on-request")
    doc = await rig.run()
    assert _verdict(doc) == "fail"
    assert set(_status(doc, "state:memory_contains")) == {"missing"}


# 4. Partial and late spans


async def test_4_a_late_never_call_is_not_a_pass(make_rig) -> None:
    rig = make_rig(["late_span_batch"])
    rig.add(_case(ASKS, FORBIDDEN_CALL, tools={"never": [FORBIDDEN]}))
    doc = await rig.run()
    assert _verdict(doc) in ("fail", "unjudged")


async def test_4_a_never_call_past_the_span_cap_is_not_a_pass(make_rig) -> None:
    rig = make_rig(["partial_trace"])
    rig.add(_case(FORBIDDEN_CALL, ASKS, tools={"never": [FORBIDDEN]}))
    doc = await rig.run()
    assert _verdict(doc) in ("fail", "unjudged")


async def test_4_a_late_model_call_is_unjudged_not_failed(make_rig) -> None:
    rig = make_rig(["late_span_batch"])
    rig.fill("compaction-keeps-early-decision")
    doc = await rig.run()
    assert _verdict(doc) == "unjudged"


# 5. The app or the judge not there


@pytest.mark.parametrize(
    ("faults", "how", "default"),
    [
        pytest.param(["unreachable_mid_try"], "connect", "pass", id="app-refuses-connections"),
        pytest.param(["unreachable_mid_try"], "gone", "pass", id="app-session-gone"),
        pytest.param(["judge_outage"], "connect", "pass", id="judge-outage"),
        pytest.param([], "connect", "garbage", id="judge-garbage"),
    ],
)
async def test_5_infrastructure_failures_are_unjudged_not_regressions(
    make_rig, faults, how, default
) -> None:
    turns = (ASKS, {"message": "Write a two-line intro of me for our team page."})
    good = make_rig(judge=StubJudge())
    good.add(_case(*turns))
    before = await good.run()
    given(_verdict(before) == "pass", "the healthy run didn't pass")

    rig = make_rig(faults, judge=StubJudge(faults, default=default))
    rig.agent.unreachable_how = how
    rig.add(_case(*turns))
    doc = await rig.run()
    before["commit"], doc["commit"] = "a" * 40, "b" * 40
    assert (_verdict(doc), regressions(_played([doc, before]))) == ("unjudged", [])


async def test_5_an_unreachable_app_ends_the_run_early(make_rig) -> None:
    rig = make_rig(["unreachable_mid_try"])
    rig.add(_case(ASKS, {"message": "And another."}, limits={"tries": 3}))
    doc = await rig.run()
    assert doc["status"] == "failed"
    assert [current(t).outcome for t in _tries(doc)] == [Outcome.UNJUDGED]
    assert current(_tries(doc)[0]).checks[0].unjudged is WhyUnjudged.APP_UNREACHABLE


async def test_5_control_an_app_error_fails_the_try(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))

    async def broken(*_args: Any, **_kwargs: Any) -> Turn:
        return Turn("", 0.0, 0.0, error="the app answered 500")

    rig.agent.send = broken
    doc = await rig.run()
    assert _verdict(doc) == "fail" and doc["status"] == "done"
    assert current(_tries(doc)[0]).checks[0].kind is CheckKind.TURN


# 6. Unsure and agreeing


async def test_6_both_gradings_unsure_is_unjudged(make_rig) -> None:
    rig = make_rig(["judge_unsure_both"])
    rig.add(_case(ASKS))
    doc = await rig.run()
    given(not _tries(doc)[0]["rubric"][0]["sure"], "the judge wasn't unsure")
    given(not _tries(doc)[0]["rubric"][0]["second"]["sure"], "no second grading")
    assert _verdict(doc) == "unjudged"


async def test_6_a_sure_grading_runs_once(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    await rig.run()
    assert len(rig.judge.inputs) == 1


# 7. Evidence clipped


def _long_try() -> dict[str, Any]:
    names = ", ".join(f"step_{i:03}" for i in range(200))
    return _case(
        {"message": f"Please use {names}."},
        {"expect_state": {"memory_lacks": FACT_WORD}},
    )


async def test_7_state_lines_reach_the_judge(make_rig) -> None:
    rig = make_rig()
    rig.add(_long_try())
    await rig.run()
    assert "check state:memory_lacks" in what_it_did(rig.judge.inputs[0])


async def test_7_the_last_tool_call_reaches_the_judge(make_rig) -> None:
    rig = make_rig()
    rig.add(_long_try())
    await rig.run()
    assert "step_199" in what_it_did(rig.judge.inputs[0])


async def test_7_control_a_short_try_shows_both(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(FORBIDDEN_CALL, {"expect_state": {"memory_lacks": FACT_WORD}}))
    await rig.run()
    seen = what_it_did(rig.judge.inputs[0])
    assert FORBIDDEN in seen and "check state:memory_lacks" in seen


async def test_7_a_try_leaves_its_whole_evidence_with_the_run(make_rig) -> None:
    rig = make_rig()
    big = "x" * (ITEM_BYTES + 10_000)
    rig.add(_case({"message": f"Some background on the team: {big}"}, ASKS))
    doc = await rig.run()
    (kept,) = rig.runs.store.evidence(doc["run"])
    assert kept.case == next(iter(doc["cases"])) and len(kept.turns) == 2
    assert kept.turns[0].message.elided == 10_000 + len("Some background on the team: ")
    assert kept.turns[0].message.sha256
    assert kept.model_calls and kept.model_calls[0].sent.text
    assert len(rig.runs.store.all()) == 1, "the evidence is no run"


async def test_7_what_the_page_showed_is_kept_with_its_turn(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    real = rig.agent.send
    big = "y" * (ITEM_BYTES + 10_000)

    async def shows(*args: Any, **kwargs: Any) -> Turn:
        return replace(await real(*args, **kwargs), shown=big)

    rig.agent.send = shows
    doc = await rig.run(tries=1)
    (kept,) = rig.runs.store.evidence(doc["run"])
    shown = kept.turns[-1].shown
    assert shown is not None and shown.elided == 10_000 and shown.sha256, "capped like the reply"
    assert kept.turns[-1].reply.text != shown.text, "the reply stays what the adapter read"


async def test_7_control_an_adapter_shows_nothing_and_old_evidence_still_reads(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    doc = await rig.run(tries=1)
    (kept,) = rig.runs.store.evidence(doc["run"])
    assert all(turn.shown is None for turn in kept.turns)
    # Evidence kept before ``shown`` existed has no such field at all.
    old = kept.model_dump(mode="json")
    for turn in old["turns"]:
        del turn["shown"]
    rig.runs.store.docs.clear_entries(EVIDENCE, doc["run"])
    rig.runs.store.docs.append(EVIDENCE, doc["run"], old)
    (read,) = rig.runs.store.evidence(doc["run"])
    assert read == kept


async def test_7_grading_again_needs_evidence_and_only_adds(make_rig, monkeypatch) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    doc = await rig.run(tries=3)
    run, stem = doc["run"], next(iter(doc["cases"]))
    # A person says the grader's pass is wrong on every try: its passes stop counting.
    for n in (1, 2, 3):
        await trust.label(rig.runs.store, rig.docs, run, stem, n, 0, agree=False)
    assert _verdict(rig.runs.load(run)) == "unjudged"
    rig.judge.default = "fail"
    estimate = rig.runs.estimate_regrade(run)
    assert estimate["tries"] == 3
    assert await rig.runs.regrade(estimate["token"]) == {"graded": 3, "failed": 0}
    with pytest.raises(KeyError):
        await rig.runs.regrade(estimate["token"])
    after = rig.runs.load(run)
    judged = after["cases"][stem]["tries"][0]["judgements"]
    assert [j["outcome"] for j in judged] == ["passed", "unjudged", "failed"]
    # The labelled examples were graded again with it: it now says what the person said.
    line = RUBRIC["rubric"]
    assert trust.lines_view(rig.docs, after, stem, line)[0]["agreed"] == 3

    # Another model grades the next run: the examples are graded again from their evidence, it
    # passes them against the person, and its own passes on the line don't count.
    rig.judge.default = "pass"
    monkeypatch.setattr(rig.runs.llm, "model_id", lambda _key: "another-model", raising=False)
    later = await rig.run(tries=1)
    assert trust.lines_view(rig.docs, later, stem, line)[0] == {
        "agreed": 0,
        "labelled": 3,
        "unreliable": True,
    }
    assert _verdict(later) == "unjudged" and _verdict(rig.runs.load(run)) == "fail"
    rig.runs.store.docs.clear_entries(EVIDENCE, run)
    with pytest.raises(ValueError, match="no evidence"):
        rig.runs.estimate_regrade(run)


async def test_7_one_run_at_a_time_across_processes(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    other = RunLock(rig.config.data_dir)
    assert other.acquire()
    try:
        with pytest.raises(RunBusyError):
            await rig.runs.start(rig.runs.estimate(TARGET, SET, None, None)["token"])
    finally:
        other.release()
    await rig.run()
    assert not rig.runs.lock.held


async def test_7_a_stopped_save_cant_land_on_top_of_the_one_after_it(tmp_path) -> None:
    store = RunStore(Database.open(tmp_path).docs())
    written = store.write

    def slow_while_running(run: Any) -> None:
        if run["status"] == "running":
            time.sleep(0.2)
        written(run)

    store.write = slow_while_running
    doc = {"run": "r1", "status": "running"}
    stopping = asyncio.ensure_future(store.save(doc))
    await asyncio.sleep(0.05)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    doc["status"] = "stopped"
    await store.save(doc)
    # Control: had the write been lost, or last, this says so.
    await asyncio.sleep(0.3)
    assert store.docs.get(RUN, "r1")["status"] == "stopped"


def test_7_writers_of_one_run_from_many_threads_leave_one_run(tmp_path) -> None:
    store = RunStore(Database.open(tmp_path).docs())
    with ThreadPoolExecutor(8) as pool:
        for found in [pool.submit(store.write, {"run": "r1", "n": n}) for n in range(200)]:
            found.result()
    assert store.docs.get(RUN, "r1")["run"] == "r1" and store.ids() == ["r1"]


def test_7_one_holder_ending_doesnt_take_the_lock_from_another(tmp_path) -> None:
    mine = RunLock(tmp_path)
    assert mine.acquire()  # the pass over a last server's runs
    assert mine.acquire()  # a run starting meanwhile
    mine.release()
    assert mine.held
    assert not RunLock(tmp_path).acquire(), "another process is still kept out"
    mine.release()
    assert not mine.held
    # Control: it's free again, and letting go twice takes nothing that isn't there.
    other = RunLock(tmp_path)
    assert other.acquire()
    mine.release()
    assert other.held
    other.release()


async def test_7_the_orphan_pass_leaves_a_starting_runs_lock_alone(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    rig.runs._orphans_marked = False
    marking = rig.runs._mark_stored_orphans

    def while_a_run_starts() -> None:
        assert rig.runs.lock.acquire()  # what start does, in the loop's thread
        marking()

    rig.runs._mark_stored_orphans = while_a_run_starts
    await rig.runs.recover()
    assert rig.runs.lock.held
    rig.runs.lock.release()
    assert not rig.runs.lock.held


@pytest.mark.parametrize("what", ["open", "reset"])
async def test_7_an_app_that_never_opens_or_resets_ends_the_run(
    make_rig, monkeypatch, what
) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))

    async def never(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.Event().wait()

    await rig.run()  # the control: both answer, and the first check passes for what follows
    monkeypatch.setattr(runner, "SETUP_SECONDS", 0.02)
    setattr(rig.agent, what, never)
    doc = await asyncio.wait_for(rig.run(), 30)
    assert doc["status"] == "failed" and doc["failure"] == APP_NOT_READY
    assert "didn't" in doc["error"] and "in 0 s" in doc["error"]
    assert not rig.runs.lock.held


async def test_7_a_runs_own_exception_keeps_no_login_or_query(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    await rig.run()

    async def refused(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("open: http://me:fake-pw@app.example.test/s?key=fake-key said no")

    rig.agent.open = refused
    doc = await rig.run()
    assert doc["status"] == "failed"
    assert "app.example.test/s" in doc["error"]
    assert "fake-pw" not in json.dumps(doc) and "fake-key" not in json.dumps(doc)

    # A try's own, from an adapter that quotes the URL, in the run file and beside it.
    async def quotes(*_args: Any, **_kwargs: Any) -> Turn:
        error = "HTTPStatusError: 500 for http://me:fake-pw@app.example.test/c?key=fake-key"
        return Turn("", 0.0, 0.0, error=error)

    del rig.agent.open
    rig.agent.send = quotes
    # One try: unasked, a test that got worse than the run before plays on.
    doc = await rig.run(tries=1)
    attempt = _tries(doc)[0]
    assert "app.example.test/c" in attempt["error"]
    assert "fake-pw" not in json.dumps(doc)
    (kept,) = rig.runs.store.evidence(doc["run"])
    assert "fake-pw" not in kept.model_dump_json() and "fake-key" not in kept.model_dump_json()


# 8. One document, one answer


def _readers(rig, capsys, monkeypatch, tries: set[int]) -> tuple[dict[str, str | None], int]:
    """Play through the real CLI, then what each reader says of the one stored document."""
    monkeypatch.setattr(cli, "Targets", lambda _config: rig.targets)
    stem = rig.add({"name": "Proof case", "turns": [ASKS], "limits": {"tries": 2}})
    rig.agent.no_spans_on_tries = tries
    capsys.readouterr()
    code = cli.main(
        ["--config", str(rig.config_file), "run", "--target", TARGET, "--set", SET, "--yes"]
    )
    table = capsys.readouterr().out.splitlines()
    row = next(line for line in table if line.startswith(stem))
    (doc,) = rig.runs.store.all(TARGET, SET)
    entry = doc["cases"][stem]
    given(any(t["error"] for t in entry["tries"]), "no try lost its traces")
    return {
        "case_row": case_row(stem, entry["name"], entry)["verdict"],
        "_run_test": _run_test(doc, stem, entry)["verdict"],
        "health": _verdicts(doc).get(stem, "unjudged"),
        "case_detail": case_detail(doc, stem)["verdict"],
        "case_history": case_history(stem, [doc])[0]["verdict"],
        "cli table": row.split()[1],
    }, code


def test_8_no_traces_at_all_reads_the_same_everywhere(make_rig, capsys, monkeypatch) -> None:
    rig = make_rig()
    answers, code = _readers(rig, capsys, monkeypatch, {1, 2})
    assert set(answers.values()) == {"unjudged"} and code != 0, (answers, code)


def test_8_one_try_without_traces_reads_the_same_everywhere(make_rig, capsys, monkeypatch) -> None:
    rig = make_rig()
    answers, code = _readers(rig, capsys, monkeypatch, {1})
    assert set(answers.values()) == {"pass"} and code == 0, (answers, code)


# 9. What the report is given


async def _flaky(make_rig) -> dict:
    """Try 1 holds the rubric line and try 2 misses it."""
    rig = make_rig(judge=StubJudge(gradings=["pass", "fail"]))
    rig.add(_case(ASKS))
    doc = await rig.run(tries=2)
    given(_verdict(doc) == "flaky", "the test wasn't flaky")
    return doc


async def test_9_a_rubric_line_missed_in_try_2_reaches_the_report(make_rig) -> None:
    doc = await _flaky(make_rig)
    text = report_input(doc, None)
    assert "missed: Handles the request" in text


async def test_9_control_that_try_2_failed_does_reach_the_report(make_rig) -> None:
    """The review overstated this one: the failure itself gets there, only not what failed."""
    doc = await _flaky(make_rig)
    assert "failed try 2" in report_input(doc, None)


# 10. Before the check, counted as after it


async def _after_a_break(make_rig, turn_seconds: float) -> dict:
    """The word was sent before the check, in the old conversation; the next call lacks it."""
    rig = make_rig()
    rig.agent.turn_seconds = turn_seconds
    rig.add(
        _case(
            {"message": f"Some background on the team: {VALUES['fact']}"},
            {"break": "conversation"},
            {"expect_state": {"context_contains": FACT_WORD}},
            ASKS,
        )
    )
    return await rig.run()


async def test_10_a_call_before_the_check_does_not_count_as_after_it(make_rig) -> None:
    doc = await _after_a_break(make_rig, turn_seconds=0.9)
    assert _status(doc, "state:context_contains") == ["missing"]


async def test_10_control_a_slower_agent_gets_the_right_answer(make_rig) -> None:
    doc = await _after_a_break(make_rig, turn_seconds=5.0)
    assert _status(doc, "state:context_contains") == ["missing"]


# 11. Leaks across tries


async def _store_breaks_in_try_2(make_rig, faults: list[str], **options: Any) -> dict:
    rig = make_rig(faults, options={"store_fails_from_try": 2, **options})
    rig.fill("memory-recalls-fact")
    # Two tries exactly: unasked, a pass and a fail would play on.
    return await rig.run(tries=2)


@known(
    "With no proven isolation plan, an adapter's own reset is trusted and fresh is only handed to "
    "it: try 2 stores nothing and passes on what try 1 left behind. A managed target is put back "
    "and checked before every try (test_11_on_a_managed_copy_...)"
)
async def test_11_a_pass_resting_on_a_leak_is_not_a_pass(make_rig) -> None:
    doc = await _store_breaks_in_try_2(make_rig, ["leak_across_tries", "forgets_to_store"])
    assert _verdict(doc) != "pass"


async def test_11_control_without_the_leak_try_2_fails(make_rig) -> None:
    doc = await _store_breaks_in_try_2(make_rig, ["forgets_to_store"])
    assert _verdict(doc) == "flaky"
    assert [current(t).outcome.value for t in _tries(doc)] == ["passed", "failed"]
    assert {t["isolation"]["how"] for t in _tries(doc)} == {"identity"}
    assert doc["isolation"] == {"how": "identity", "plan_version": None, "person": None}


#: Where the file-backed agent keeps its memory, in its rig's folder.
MEMORY_FILE = "app-data/memory.json"


async def _on_a_managed_copy(make_rig, faults: list[str], *, drifts: bool = False) -> dict:
    """The same agent keeping its memory in a file, as a managed target: a proven plan names the
    folder, and the runner plays on evalution's own copy of the app."""
    rig = make_rig(faults, options={"store_fails_from_try": 2, "memory_file": MEMORY_FILE})
    rig.manage(drifts=drifts)
    rig.fill("memory-recalls-fact")
    return await rig.run(tries=2)


async def test_11_on_a_managed_copy_a_pass_resting_on_a_leak_is_not_a_pass(make_rig) -> None:
    doc = await _on_a_managed_copy(make_rig, ["leak_across_tries", "forgets_to_store"])
    # Putting the copy back took try 1's memory away: try 2 stores nothing and fails, as it should.
    assert _verdict(doc) != "pass"
    assert [current(t).outcome.value for t in _tries(doc)] == ["passed", "failed"]
    labels = [t["isolation"] for t in _tries(doc)]
    assert all(lab["how"] == "proven" and lab["plan_version"] == 1 for lab in labels)
    # The plan's id for each store checked, which the page names in plain words.
    assert all(lab["checked"] == ["memory"] and lab["problem"] is None for lab in labels)
    # Try 1 plays on the copy as it was kept (and checked) a moment before: no put back.
    assert [lab["check"] for lab in labels] == ["kept", "full"]
    assert labels[0]["restore_seconds"] is None and isinstance(labels[1]["restore_seconds"], float)
    assert doc["isolation"] == {"how": "proven", "plan_version": 1, "person": None}
    assert summary(doc)["isolation"] == doc["isolation"]


async def test_11_control_unmanaged_the_same_file_backed_agent_passes_on_the_leak(make_rig) -> None:
    rig = make_rig(
        ["leak_across_tries", "forgets_to_store"],
        options={"store_fails_from_try": 2, "memory_file": MEMORY_FILE},
    )
    rig.fill("memory-recalls-fact")
    doc = await rig.run(tries=2)
    # The false pass the managed copy prevents: try 2 recalls what try 1 stored.
    assert _verdict(doc) == "pass"
    assert {t["isolation"]["how"] for t in _tries(doc)} == {"identity"}


async def test_11_on_a_managed_copy_a_reset_that_doesnt_hold_leaves_the_try_unjudged(
    make_rig,
) -> None:
    doc = await _on_a_managed_copy(make_rig, ["forgets_to_store"], drifts=True)
    tries = _tries(doc)
    assert [current(t).outcome.value for t in tries] == ["unjudged", "unjudged"]
    assert all(t["isolation"]["problem"].startswith("reset didn't hold: ") for t in tries)
    assert all(t["isolation"]["checked"] == [] for t in tries)
    assert all(t["error"].startswith("reset didn't hold") for t in tries)
    assert _verdict(doc) != "pass"


async def test_11_a_managed_target_with_require_proven_and_no_plan_is_refused(make_rig) -> None:
    rig = make_rig()
    rig.fill("memory-recalls-fact")
    rig.targets.config = replace(rig.config, isolation=IsolationConfig(require_proven=True))
    with pytest.raises(NotIsolated, match="evalution isolate --target agent"):
        rig.runs.estimate(TARGET, SET, None, 1)
    rig.targets.config = rig.config
    assert rig.runs.estimate(TARGET, SET, None, 1)["isolation"]["how"] == "identity"


# 12. What the approval covers


async def test_12_a_case_other_than_the_approved_one_is_refused(make_rig) -> None:
    rig = make_rig()
    agent = Agent(rig.runs, SessionStore(rig.targets.docs), None)
    opened = await agent.create(TARGET, SET)
    session = await agent._session(opened["id"])
    plan = {
        "steps": [{"kind": "message", "title": "Say hi"}],
        "tries": 3,
        "case": "greets",
    }
    await agent.tools.reply(AgentTurn(session), {"text": "Here is the plan", "plan": plan})
    plan_id = session["messages"][-1]["plan"]["id"]
    await agent.approve(session["id"], plan_id)
    for task in list(agent._tasks.values()):
        await task
    other = {"name": "Something else entirely", "turns": [{"message": "Delete everything"}]}
    saved = await agent.tools.save_case(
        AgentTurn(await agent._session(session["id"])), {"plan_id": plan_id, "case": other}
    )
    assert isinstance(saved, str) and saved.startswith("Refused")


async def test_12_control_the_approved_case_saves(make_rig) -> None:
    rig = make_rig()
    agent = Agent(rig.runs, SessionStore(rig.targets.docs), None)
    opened = await agent.create(TARGET, SET)
    session = await agent._session(opened["id"])
    case = {"name": "Greets", "turns": [{"message": "hi"}]}
    plan = {
        "steps": [{"kind": "message", "title": "Say hi"}],
        "tries": 3,
        "case": "greets",
        "file": case,
    }
    await agent.tools.reply(AgentTurn(session), {"text": "Here is the plan", "plan": plan})
    plan_id = session["messages"][-1]["plan"]["id"]
    await agent.approve(session["id"], plan_id)
    for task in list(agent._tasks.values()):
        await task
    turn = AgentTurn(await agent._session(session["id"]))
    other = {"name": "Something else entirely", "turns": [{"message": "Delete everything"}]}
    refused = await agent.tools.save_case(turn, {"plan_id": plan_id, "case": other})
    assert refused.startswith("Refused")
    # "Add to tests" saved the approved file itself.
    assert turn.session["plans"][plan_id]["saved"] == "greets"
    assert load_case(rig.docs, TARGET, SET, "greets").raw["turns"] == case["turns"]


# Claims


def _evidence_for_claims() -> Any:
    said = TurnRecord(
        kind="message",
        message=Clipped(text="Where is the office?"),
        reply=Clipped(text="The  office moved\nto Lisbon."),
        started=0.0,
        ended=1.0,
    )
    return TryEvidence(case="c", n=1, t0=0.0, seconds=1.0, turns=(said,), spans=("ab12",))


@pytest.mark.parametrize(
    ("refs", "quotes", "verified"),
    [
        (["span:ab12"], ["the office moved to lisbon"], True),  # whitespace and case aside
        (["reply", "turn:1"], [], True),
        (["span:nope"], ["moved to Lisbon"], False),  # a ref that doesn't exist
        (["span:ab12"], ["moved to Porto"], False),  # a quote that doesn't occur
        ([], [], False),  # cites nothing
    ],
)
def test_a_claim_is_checked_and_kept_either_way(refs, quotes, verified) -> None:
    index = index_of([_evidence_for_claims()])
    claim = read_claim({"text": "It moved.", "refs": refs, "quotes": quotes}, index)
    assert claim.text == "It moved." and claim.verified is verified


async def test_a_grader_quote_is_checked_too(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    real = rig.judge._grade

    def quoting(input_text: str) -> str:
        said = json.loads(real(input_text))
        said["lines"][0]["quotes"] = ["Here you go", "Not in the reply"]
        return json.dumps(said)

    rig.judge._grade = quoting
    doc = await rig.run()
    refs = current(_tries(doc)[0]).checks[0].evidence
    assert [(r.quote, r.verified) for r in refs] == [
        ("Here you go", True),
        ("Not in the reply", False),
    ]


async def test_an_analysis_is_a_claim_a_report_is_facts_and_a_reading(make_rig) -> None:
    rig = make_rig(["forget_says_done_only"], judge=StubJudge())
    rig.fill("forget-on-request")
    doc = await rig.run(tries=1)
    (attempt,) = _tries(doc)
    assert attempt["analysis"]["summary"] == {
        "text": "scripted review",
        "refs": [],
        "quotes": [],
        "verified": False,
    }
    assert attempt["analysis"]["suspected"] == "app"
    assert doc["report"] == {"reading": [], "well": [], "next": []}


# Found while building this


async def test_extra_a_try_does_not_read_the_spans_of_the_try_before(make_rig) -> None:
    rig = make_rig()
    rig.agent.stable_trace_value = True
    rig.agent.turn_seconds = 1.0
    rig.add(_case(FORBIDDEN_CALL, limits={"tries": 2}))
    doc = await rig.run()
    assert [len(t["calls"]) for t in _tries(doc)] == [1, 1]


# 12. An adapter that doesn't read the app: its faults are not the app's


ONE_TURN = {
    "name": "Answers",
    "turns": [{"message": "Hello there."}],
    "reply": {"rubric": ["Replies"]},
    "limits": {"tries": 2, "seconds": 900},
}


def _why_unjudged(doc: dict) -> str:
    ((_, entry),) = doc["cases"].items()
    return current(entry["tries"][0]).checks[0].why


async def test_12_an_empty_reply_to_an_answered_turn_is_the_adapters(make_rig) -> None:
    rig = make_rig(["nested_reply_read_flat"])
    rig.add(ONE_TURN)
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and doc["failure"] == ADAPTER_BROKEN
    assert "adapter read an empty reply" in doc["error"]
    # Two in a row were enough: the rest of the run would only have read nothing again.
    assert len(_tries(doc)) == 2


async def test_12_one_adapter_failure_ends_the_try_and_not_the_run(make_rig) -> None:
    rig = make_rig()
    stem = rig.add(ONE_TURN)
    real, sent = rig.agent.send, []

    async def breaks_once(*args: Any, **kwargs: Any) -> Turn:
        sent.append(1)
        if len(sent) == 1:
            raise ValueError("no reply here")
        return await real(*args, **kwargs)

    rig.agent.send = breaks_once
    doc = await rig.run()
    assert doc["status"] == "done" and doc.get("failure") is None and len(sent) == 2
    first, second = (current(t) for t in _tries(doc))
    assert first.outcome is Outcome.UNJUDGED
    assert first.checks[0].unjudged is WhyUnjudged.ADAPTER_MISREAD
    assert second.outcome is Outcome.PASSED, "control: the next try is judged as usual"
    assert _verdict(doc) == "pass"
    assert (
        case_row(stem, "Answers", doc["cases"][stem])["note"] == "1 of 2 tries couldn't be judged"
    )


@pytest.mark.parametrize("fault", ["polls_for_unread_reply", "ignores_deadline"])
async def test_12_it_stops_soon_after_the_agent_finished(make_rig, clock, fault) -> None:
    rig = make_rig([fault])
    rig.add(ONE_TURN)
    began = clock.now
    doc = await rig.run()
    assert doc["failure"] == ADAPTER_BROKEN and "still waiting" in doc["error"]
    assert clock.now - began < 2 * 200, "two tries, and not nine minutes"
    assert current(_tries(doc)[0]).checks[0].unjudged is WhyUnjudged.ADAPTER_MISREAD


async def test_12_without_traces_the_deadline_still_holds(make_rig, clock) -> None:
    rig = make_rig(["ignores_deadline", "sends_no_traces"])
    rig.add({**ONE_TURN, "limits": {"tries": 2, "seconds": 60}})
    began = clock.now
    doc = await rig.run()
    assert doc["failure"] == ADAPTER_BROKEN and "past the 60 s limit" in doc["error"]
    assert clock.now - began < 2 * 120


async def test_12_a_broken_page_map_ends_the_run_after_one_try_and_fails_nothing(make_rig) -> None:
    rig = make_rig()
    rig.add(ONE_TURN)
    rig.add({**ONE_TURN, "name": "Answers again"})
    sent = []

    async def misses_send(*_args: Any, **_kwargs: Any) -> Turn:
        sent.append(1)
        error = 'the page has no button "Send"'
        return Turn("", time.time(), time.time(), error=error, failure=Failure.PAGE_MAP)

    rig.agent.send = misses_send
    doc = await rig.run()
    assert doc["status"] == "failed" and doc["failure"] == PAGE_MAP_BROKEN
    assert 'no button "Send"' in doc["error"]
    # Every try would meet the same page: one was enough, of two asked for each of two tests.
    assert len(sent) == 1 and len(_tries(doc)) == 1
    found = current(_tries(doc)[0])
    assert found.outcome is Outcome.UNJUDGED and label_of([found]) == "Setup blocked"
    assert found.checks[0].unjudged is WhyUnjudged.PAGE_MAP
    tests = {t["name"]: t for t in summary(doc)["tests"]}
    played, left = sorted(tests.values(), key=lambda t: t["verdict"] is None)
    assert played["verdict"] == "unjudged" and played["unjudged"] == ["page_map"]
    assert left["verdict"] is None
    assert left["reason"] == "the page lacks what its page map names"


async def test_12_a_restart_late_in_the_wait_is_not_called_the_apps_fault(make_rig) -> None:
    # Told to wait longer than the restart: only the read after the turn can tell.
    rig = make_rig(["restart_while_polling"], traces_options={"stuck_seconds": 600})
    rig.add(ONE_TURN)
    doc = await rig.run()
    assert doc["failure"] == ADAPTER_BROKEN
    assert "still waiting" in doc["error"] and "ConnectError" in doc["error"]


async def test_12_control_an_app_down_while_it_works_stays_unreachable(make_rig) -> None:
    rig = make_rig(["unreachable_mid_try"])
    rig.add({**ONE_TURN, "turns": [{"message": "One."}, {"message": "Two."}]})
    doc = await rig.run()
    assert doc["failure"] == APP_NOT_READY


async def test_12_a_slow_tool_is_not_a_finished_agent(make_rig, clock) -> None:
    """A model call that asked for a tool, long ago, while the tool runs: nothing was missed."""
    said = {"role": "assistant", "content": "Working on it."}

    root = Span("root", "t", None, "turn", "AGENT", 0.0, 1.5, "OK", {})

    def span(message: dict) -> Span:
        attrs = {"gen_ai": {"output": {"messages": json.dumps([message])}}}
        return Span("a", "t", "root", "llm", "LLM", 0.0, 1.0, "OK", attrs)

    done = [span(said), root]
    assert answered_at(done, 0.0) == 1.0, "control: the same call, asking for nothing"
    assert answered_at([span({**said, "tool_calls": [{"id": "1"}]}), root], 0.0) is None
    tool = Span("b", "t", None, "search", "TOOL", 2.0, 3.0, "OK", {})
    assert answered_at([*done, tool], 0.0) is None, "it went on working after"
    assert answered_at(done, 5.0) is None, "that was an earlier turn"
    # As a provider's body in output.value, the shape real traces came in.
    body = {"content": [{"type": "text", "text": "On it."}, {"type": "tool_use", "name": "x"}]}
    asking = Span(
        "a", "t", "root", "llm", "LLM", 0.0, 1.0, "OK", {"output": {"value": json.dumps(body)}}
    )
    assert answered_at([asking, root], 0.0) is None
    # Inside a turn whose own span hasn't come: it's still open.
    assert answered_at([span(said)], 0.0) is None


def _asks_for_a_tool(
    id_: str, at: float, parent: str | None = None, trace: str = "A", **attrs: Any
) -> Span:
    out = [{"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]}}]
    return _span(id_, "LLM", at, trace, parent, **{"llm.output_messages": out, **attrs})


def test_12_a_side_call_or_a_call_during_a_tool_is_not_the_answer() -> None:
    root = _span("root", "AGENT", 0.0)
    root.end = 9.0
    asks, title_root = _asks_for_a_tool("asks", 1.0, "root"), _span("sr", "CHAIN", 2.0, "B")
    title = _llm("title", 2.1, "B", "sr", said="Order lookup")
    # A title made on the side, with a root of its own, while the main call's tool runs.
    assert answered_at([asks, title_root, title], 0.0) is None
    assert answered_at([asks, title_root, title], 0.0, enclosed=False) is None
    # The same, with nothing yet from the main tree but its first span's parent to go on.
    first = _span("step", "CHAIN", 0.5, parent="root")
    assert answered_at([first, title_root, title], 0.0) is None
    # In the main tree, a guard's words while the tool runs aren't the answer either.
    guard = _llm("guard", 2.0, parent="root", said="Looks fine")
    assert answered_at([asks, guard, root], 0.0) is None
    # Control: the tool closed, the same words from the main tree are.
    tool = _tool("tool", "search", 1.6, parent="root")
    assert answered_at([asks, tool, guard, root], 0.0) == guard.end
    assert answered_at([asks, tool, guard], 0.0, enclosed=False) == guard.end
    # Two tools asked for, one closed.
    both = [{"message": {"role": "assistant", "tool_calls": [{"id": "1"}, {"id": "2"}]}}]
    asks_two = _span("asks", "LLM", 1.0, "A", "root", **{"llm.output_messages": both})
    assert answered_at([asks_two, tool, guard, root], 0.0) is None
    other = _tool("other", "search", 1.7, parent="root")
    assert answered_at([asks_two, tool, other, guard, root], 0.0) == guard.end


async def test_12_the_first_run_checks_the_adapter_and_spends_no_more(make_rig) -> None:
    rig = make_rig(["nested_reply_read_flat"], check_adapters=True)
    rig.add(ONE_TURN)
    doc = await rig.run()
    assert doc["failure"] == ADAPTER_BROKEN and not _tries(doc), "no test was played"
    assert rig.judge.inputs == []

    well = make_rig(check_adapters=True)
    well.add(ONE_TURN)
    assert (await well.run())["status"] == "done"
    assert well.agent.every_send == 2 + 1
    # It passed: the next run doesn't check again.
    await well.run()
    assert well.agent.every_send == 3 + 2


def test_12_an_app_with_no_traces_is_warned_of_and_still_played(
    make_rig, monkeypatch, capsys
) -> None:
    rig = make_rig(check_adapters=True)
    says = rig.add({**ONE_TURN, "limits": {"tries": 1, "seconds": 60}})
    never = rig.add(
        {
            "name": "Keeps from a tool",
            "turns": [{"message": "Hello there."}],
            "tools": {"never": ["charge_card"]},
            "limits": {"tries": 1, "seconds": 60},
        }
    )
    monkeypatch.setattr(rig.targets, "source", lambda _name: _Source())
    monkeypatch.setattr(cli, "Targets", lambda _config: rig.targets)
    command = ["--config", str(rig.config_file), "check", "--target", TARGET]
    capsys.readouterr()
    assert cli.main(command) == 0
    said = capsys.readouterr()
    assert "Warning: No traces arrived" in said.err and "The adapter works." in said.out
    assert "Problem" not in said.err

    rig.agent.every_send = 0
    forget_setup(rig.config.data_dir, TARGET)
    doc = asyncio.run(rig.run())
    assert doc["status"] == "done" and doc.get("failure") is None
    assert rig.agent.every_send > 2, "the check, and then the tests"
    assert verdict_kind(doc["cases"][says]) == "pass", "the reply alone decides it"
    held = doc["cases"][never]
    assert verdict_kind(held) == "unjudged" and label_of([current(held["tries"][0])]) == (
        "Missing evidence"
    )

    # Control: the reply misread stays a problem whatever the traces do.
    broken = make_rig(["nested_reply_read_flat"], check_adapters=True)
    broken.add(ONE_TURN)
    assert asyncio.run(broken.run())["failure"] == ADAPTER_BROKEN


# 13. An app that doesn't work the way the first ones did


async def test_13_an_app_that_works_on_after_answering_isnt_a_broken_adapter(make_rig) -> None:
    """It answers "Queued!" and the adapter waits for the work: a wait, not a miss."""
    rig = make_rig(["polls_for_unread_reply"], options={"background": True})
    rig.add({**ONE_TURN, "limits": {"tries": 1, "seconds": 600}})
    doc = await rig.run()
    assert doc["failure"] != ADAPTER_BROKEN
    assert "still waiting" not in str(_tries(doc)[0]["error"])
    # The same app on an adapter that doesn't say so, told how to fix it.
    plain = make_rig(["polls_for_unread_reply"])
    plain.add({**ONE_TURN, "limits": {"tries": 2, "seconds": 600}})
    assert "set background on the target" in (await plain.run())["error"]


async def test_13_a_test_the_app_cant_serve_is_refused_before_anything_is_spent(make_rig) -> None:
    rig = make_rig()
    rig.add(ONE_TURN)
    rig.add(
        {
            "name": "Needs a check it lacks",
            "turns": [{"message": "Hi."}, {"expect_state": {"no_such_check": "x"}}],
        }
    )
    found = rig.runs.estimate(TARGET, SET, None, None)
    assert found["cases"] == 1
    ((refused),) = found["refused"]
    assert refused["case"] == "needs-a-check-it-lacks" and "no_such_check" in refused["why"]
    doc = await rig.run()
    assert list(doc["cases"]) == ["answers"], "and it isn't played"


async def test_13_automatic_tools_stay_in_the_evidence_and_out_of_the_score(make_rig) -> None:
    calls = [Call("1", "recall", {}, 0.0, 1.0), Call("2", "save", {}, 1.0, 2.0)]
    tools = {"expected": [{"name": "save"}], "extras": False, "never": []}
    steps, score = grade_tools(tools, chosen_calls(calls, ["recall"], tools))
    assert score == 1.0 and [s.status for s in steps] == ["matched"]
    assert grade_tools(tools, calls)[1] < 1.0, "control: without saying so it costs"
    # A test that names it means it.
    named = {"expected": [{"name": "recall"}, {"name": "save"}]}
    assert len(chosen_calls(calls, ["recall"], named)) == 2


async def test_13_a_try_shows_what_ran_by_itself_and_what_it_counted(make_rig) -> None:
    rig = make_rig(options={"automatic_tools": ["recall"]})
    rig.add(
        _case(
            {"message": "Please use recall, save."},
            tools={"expected": [{"name": "save"}], "extras": False},
        )
    )
    doc = await rig.run(tries=1)
    ((attempt),) = _tries(doc)
    recall = next(c["id"] for c in attempt["calls"] if c["name"] == "recall")
    assert attempt["automatic"] == [recall] and attempt["tools"] == 1.0
    ((stem, _),) = doc["cases"].items()
    shown = case_detail(doc, stem)["tries"][0]
    flagged = {r["name"] for r in shown["timeline"] if r.get("automatic")}
    assert flagged == {"recall"}, "the call that counted isn't marked"
    assert {s["span"] for s in shown["trajectory"] if s.get("automatic")} == {recall}


async def test_13_a_long_test_says_which_message_it_is_on(make_rig) -> None:
    rig = make_rig()
    rig.add({"name": "Long", "turns": [{"message": "One."}, {"message": "Two."}], "reply": RUBRIC})
    seen = []
    save = rig.runs.store.save

    async def noting(doc):
        seen.append(dict(doc.get("current") or {}))
        await save(doc)

    rig.runs.store.save = noting
    await rig.run(tries=1)
    assert {(c.get("turn"), c.get("turns")) for c in seen} >= {(1, 2), (2, 2)}
    stages = list(dict.fromkeys(c["stage"] for c in seen if c.get("stage")))
    assert stages == ["playing", "reading traces", "grading"]


# 14. Work a try leaves going


def _label(doc: dict) -> str | None:
    ((stem, entry),) = doc["cases"].items()
    return _run_test(doc, stem, entry)["label"]


async def _mid_try(rig: Any, from_message: int = 1) -> dict:
    """Start a run and stop it where the app has the given message; its summary."""
    rig.agent.hold, rig.agent.hold_from = asyncio.Event(), from_message
    estimate = rig.runs.estimate(TARGET, SET, None, None)
    started = await rig.runs.start(estimate["token"])
    while True:
        await asyncio.sleep(0)
        if rig.agent.every_send >= from_message:
            return started


LONG = {**ONE_TURN, "limits": {"tries": 2, "seconds": 3_600}}


@pytest.mark.parametrize("can_cancel", [True, False])
async def test_14_a_stopped_run_stops_the_work(make_rig, can_cancel) -> None:
    rig = make_rig(options={"can_cancel": can_cancel})
    rig.add(LONG)
    started = await _mid_try(rig)
    await rig.runs.stop(started["run"])
    doc = rig.runs.load(started["run"])
    assert doc["status"] == "stopped" and rig.agent.closed
    assert bool(rig.agent.cancelled) == can_cancel


async def test_14_a_cancel_that_breaks_changes_nothing(make_rig) -> None:
    rig = make_rig(options={"can_cancel": True})
    rig.add(LONG)

    async def broken(_session: Any) -> None:
        raise RuntimeError("no such route")

    rig.agent.cancel = broken
    started = await _mid_try(rig)
    await rig.runs.stop(started["run"])
    assert rig.runs.load(started["run"])["status"] == "stopped" and rig.agent.closed


async def test_14_a_restart_reads_as_interrupted_and_a_new_run_can_start(make_rig) -> None:
    rig = make_rig(options={"can_cancel": True})
    rig.add(LONG)
    started = await _mid_try(rig, from_message=2)
    run = started["run"]
    # The process dies here: the run stays as it was stored, going and with its session open.
    left = rig.runs.store.docs.get(RUN, run)
    assert left["status"] == "running", "the rig is broken"
    await rig.runs.stop(run)
    rig.runs.store.docs.put(RUN, run, left)
    rig.agent.cancelled.clear()
    rig.agent.closed.clear()
    rig.agent.hold = None

    rig.runs = Runs(rig.targets, StubLLM(rig.judge))  # type: ignore[arg-type]
    doc = rig.runs.load(run)
    said = summary(doc)
    assert (doc["status"], doc["failure"]) == ("stopped", None)
    assert said["interrupted"] and said["stop_reason"] == SERVER_RESTARTED
    # What it played stands. Nothing is said against the app, and nothing is left to look at twice.
    assert [current(t).outcome for t in _tries(doc)] == [Outcome.PASSED]
    assert report_facts(doc)["failed"] == [] and report_facts(doc)["unjudged"] == []
    assert doc["sessions"] == [] and doc["current"] is None

    await rig.runs.recover()
    assert rig.agent.cancelled and rig.agent.cancelled == rig.agent.closed
    again = await rig.run()
    assert again["run"] != run and again["status"] == "done" and _verdict(again) == "pass"


async def test_14_a_try_never_starts_on_the_last_ones_work(make_rig) -> None:
    rig = make_rig(["delegates"], options={"background": True})
    rig.add({**ONE_TURN, "limits": {"tries": 3, "seconds": 600}})
    doc = await rig.run()
    assert rig.agent.every_send == 3 and rig.agent.on_top == 0
    assert _verdict(doc) == "pass"
    # Control: the same agent told a second message too soon shows it.
    assert doc["status"] == "done"
    session = await rig.agent.open("soon")
    rig.agent.faults = frozenset({"answers_before_the_work_ends"})
    await rig.agent.send(session, "One.", deadline=rig.agent.clock.now + 60)
    await rig.agent.send(session, "Two.", deadline=rig.agent.clock.now + 60)
    assert rig.agent.on_top == 1


async def test_14_out_of_time_with_work_going_it_is_stopped_or_the_run_ends(make_rig) -> None:
    slow = {**ONE_TURN, "limits": {"tries": 2, "seconds": 60}}
    can = make_rig(["work_outlasts_the_turn"], options={"background": True, "can_cancel": True})
    can.agent.work_seconds = 600.0
    can.add(slow)
    doc = await can.run()
    assert doc["status"] == "done" and len(_tries(doc)) == 2
    assert len(can.agent.cancelled) == 2 and can.agent.on_top == 0

    cant = make_rig(["work_outlasts_the_turn"], options={"background": True})
    cant.agent.work_seconds = 600.0
    cant.add(slow)
    doc = await cant.run()
    assert doc["status"] == "failed" and doc["error"] == STILL_WORKING
    assert len(_tries(doc)) == 1 and cant.agent.on_top == 0
    assert _label(doc) == "Timed out"


async def test_14_failed_work_is_the_apps_with_the_apps_reason(make_rig) -> None:
    rig = make_rig(["work_fails"], options={"background": True})
    rig.add(ONE_TURN)
    doc = await rig.run()
    assert doc["status"] == "done" and _verdict(doc) == "fail" and _label(doc) == "Failed check"
    assert first_reason(current(_tries(doc)[0])) == "the child's build step failed"


async def test_14_blocked_work_is_unjudged_unless_the_test_expects_it(make_rig) -> None:
    rig = make_rig(["work_waits"], options={"can_cancel": True})
    rig.add(ONE_TURN)
    doc = await rig.run()
    found = [c for c in current(_tries(doc)[0]).checks if c.kind is CheckKind.TURN]
    assert _verdict(doc) == "unjudged" and _label(doc) == "Setup blocked"
    assert found[0].unjudged is WhyUnjudged.BLOCKED and "waiting for an approval" in found[0].why
    assert doc["status"] == "done", "it says nothing of the next test"
    assert rig.agent.cancelled, "what waits doesn't stay in the way of the next try"
    assert "blocked" in case_row("answers", "Answers", doc["cases"]["answers"])["unjudged"]

    expecting = {**ONE_TURN, "turns": [{"message": "Hello there.", "blocked": True}]}
    rig = make_rig(["work_waits"], options={"can_block": True})
    rig.add(expecting)
    doc = await rig.run()
    assert _verdict(doc) == "pass" and _label(doc) == "Passed"
    assert current(_tries(doc)[0]).basis is Basis.CHECKED

    # Control: the test expects it and the app just answers.
    rig = make_rig(options={"can_block": True})
    rig.add(expecting)
    doc = await rig.run()
    assert _verdict(doc) == "fail" and _label(doc) == "Failed check"
    # And an adapter that can't tell doesn't get to play it.
    rig = make_rig()
    rig.add(expecting)
    ((refused),) = rig.runs.estimate(TARGET, SET, None, None)["refused"]
    assert "stop and wait" in refused["why"]


# 15. A timeout is its own outcome


async def test_15_an_agent_that_never_started_is_not_a_failure(make_rig) -> None:
    slow = {**ONE_TURN, "limits": {"tries": 1, "seconds": 60}}
    rig = make_rig(["never_starts"])
    rig.add(slow)
    doc = await rig.run()
    ((check),) = current(_tries(doc)[0]).checks
    assert check.unjudged is WhyUnjudged.NEVER_STARTED and _label(doc) == "Setup blocked"
    assert _tries(doc)[0]["called"] is False
    # It stays out of what's counted for or against the app.
    assert doc["totals"]["passed"] == 0 and doc["totals"]["unjudged"] == 1
    assert report_facts(doc)["failed"] == [] and report_facts(doc)["passed"] == 0

    # Control: the traces show it working. Out of time is the app's.
    rig = make_rig(["work_outlasts_the_turn"], options={"background": True})
    rig.agent.work_seconds = 600.0
    rig.add(slow)
    doc = await rig.run()
    found = current(_tries(doc)[0])
    assert found.outcome is Outcome.FAILED and found.checks[-1].why.startswith("timed out")
    assert found.checks[-1].kind is CheckKind.TIMEOUT
    assert _label(doc) == "Timed out" and _tries(doc)[0]["called"] is True
    assert report_facts(doc)["failed"][0]["label"] == "Timed out"
    assert not _tries(doc)[0]["limits_broken"], "the limit it broke is the same fact"

    # An app that sends no traces looks the same: its traces are read, and show nothing started.
    rig = make_rig(["never_starts", "sends_no_traces"])
    rig.add(slow)
    doc = await rig.run()
    assert _verdict(doc) == "unjudged"
    assert _tries(doc)[0]["called"] is False


async def test_15_a_view_of_a_try_says_all_of_it(make_rig) -> None:
    rig = make_rig()
    rig.add(ONE_TURN)
    doc = await rig.run(tries=1)
    shown = case_detail(doc, "answers")
    assert shown["label"] == "Passed"
    assert shown["tries"][0]["label"] == "Passed" and shown["tries"][0]["called"] is True
    assert shown["tries"][0]["retried"] == []
    row = case_row("answers", "Answers", doc["cases"]["answers"])
    assert (row["label"], row["labels"], row["called"]) == ("Passed", ["Passed"], [True])
    # A try stored before these says the same, from what it holds.
    entry = doc["cases"]["answers"]
    for attempt in entry["tries"]:
        del attempt["called"], attempt["retried"]
    again = case_detail(doc, "answers")["tries"][0]
    assert (again["label"], again["called"], again["retried"]) == ("Passed", True, [])


# 16. Trying again, and saying so


async def test_16_an_app_not_there_at_the_first_message_gets_one_more_go(make_rig) -> None:
    rig = make_rig(["app_blips"])
    rig.add({**ONE_TURN, "limits": {"tries": 1}})
    doc = await rig.run()
    assert doc["status"] == "done" and _verdict(doc) == "pass"
    ((retried),) = _tries(doc)[0]["retried"]
    assert retried["what"] == "app" and "ConnectError" in retried["why"]
    assert rig.agent.every_send == 2

    # Still not there: as it always was, and it says it tried.
    rig = make_rig(["app_blips"])
    rig.agent.blips = 99
    rig.add({**ONE_TURN, "limits": {"tries": 1}})
    doc = await rig.run()
    assert doc["failure"] == APP_NOT_READY and _verdict(doc) == "unjudged"
    assert _label(doc) == "Setup blocked" and _tries(doc)[0]["retried"]
    assert rig.agent.every_send == 2


async def test_16_control_past_the_first_message_or_the_apps_doing_no_retry(make_rig) -> None:
    rig = make_rig(["unreachable_mid_try"])
    rig.add({**ONE_TURN, "turns": [{"message": "One."}, {"message": "Two."}]})
    doc = await rig.run()
    assert rig.agent.every_send == 2 and _tries(doc)[0]["retried"] == []

    rig = make_rig()
    rig.add({**ONE_TURN, "limits": {"tries": 1}})
    sent = []

    async def broken(*_args: Any, **_kwargs: Any) -> Turn:
        sent.append(1)
        return Turn("", 0.0, 0.0, error="the app answered 500")

    rig.agent.send = broken
    doc = await rig.run()
    assert len(sent) == 1 and _verdict(doc) == "fail" and _tries(doc)[0]["retried"] == []


async def test_16_a_grader_that_blips_gets_one_more_go(make_rig) -> None:
    rig = make_rig(["judge_blips"])
    rig.add({**ONE_TURN, "limits": {"tries": 1}})
    doc = await rig.run()
    assert _verdict(doc) == "pass" and rig.judge.calls_failed == 1
    ((retried),) = _tries(doc)[0]["retried"]
    assert retried["what"] == "grader" and "503" in retried["why"]
    assert len(rig.judge.inputs) == 1, "the one that failed never got to grade"

    rig = make_rig(["judge_blips"])
    rig.judge.blips = 99
    rig.add({**ONE_TURN, "limits": {"tries": 1}})
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and _label(doc) == "Grader unavailable"
    assert rig.judge.calls_failed == 2 and _tries(doc)[0]["retried"]

    # Control: not something passing, so no second go.
    rig = make_rig(["judge_outage"])
    rig.add({**ONE_TURN, "limits": {"tries": 1}})
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and _tries(doc)[0]["retried"] == []


# 17. A verdict never lies: whose problem it is, and what was really seen


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://made-up-app/chat")
    return httpx.HTTPStatusError(
        f"answered {status}", request=request, response=httpx.Response(status, request=request)
    )


def _answered(status: int, *, heard: bool = False) -> Turn:
    error = _http_error(status)
    heard = heard or may_have_heard(error)
    return Turn("", 0.0, 0.0, error=str(error), failure=failure_of(error), heard=heard)


def _rig_answering(make_rig, status: int, tries: int = 1, **options: Any) -> tuple[Any, list[int]]:
    rig = make_rig(**options)
    rig.add({**ONE_TURN, "limits": {"tries": tries, "seconds": 900}})
    sent: list[int] = []

    async def answers(*_args: Any, **_kwargs: Any) -> Turn:
        sent.append(1)
        return _answered(status)

    rig.agent.send = answers
    return rig, sent


@pytest.mark.parametrize("status", [401, 403])
async def test_17_credentials_the_app_refused_end_the_run_unjudged(make_rig, status) -> None:
    rig, sent = _rig_answering(make_rig, status, tries=3)
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and _label(doc) == "Setup blocked"
    assert current(_tries(doc)[0]).checks[0].unjudged is WhyUnjudged.CREDENTIALS_REFUSED
    assert "refused the credentials" in current(_tries(doc)[0]).checks[0].why
    assert doc["failure"] == APP_NOT_READY and "refused the credentials" in doc["stop_reason"]
    assert len(sent) == 1 and doc["status"] == "failed", "no second go, and no more tries"
    assert report_facts(doc)["failed"] == []


async def test_17_a_stored_pass_of_the_setup_doesnt_hide_a_refusal(make_rig) -> None:
    rig, _ = _rig_answering(make_rig, 401, check_adapters=True)
    data = rig.config.data_dir
    remember_setup(data, TARGET, setup_key(rig.agent, rig.config.targets[TARGET]))
    doc = await rig.run()
    assert len(_tries(doc)) == 1 and "refused the credentials" in doc["error"]
    assert TARGET not in passed_setups(data), "the next run checks again"

    # Nothing stored: the check before the run says it the same way, and nothing is played.
    fresh, _ = _rig_answering(make_rig, 401, check_adapters=True)
    doc = await fresh.run()
    assert not _tries(doc) and doc["failure"] == APP_NOT_READY
    assert "refused the credentials" in doc["stop_reason"]
    assert TARGET not in passed_setups(fresh.config.data_dir)


@pytest.mark.parametrize("status", [502, 504])
async def test_17_a_gateways_error_isnt_sent_again(make_rig, status) -> None:
    """It may have passed the message on: a second go could ask the same thing twice."""
    rig, sent = _rig_answering(make_rig, status, tries=2)
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and _label(doc) == "Setup blocked"
    assert current(_tries(doc)[0]).checks[0].unjudged is WhyUnjudged.APP_BUSY
    assert doc["status"] == "done" and len(_tries(doc)) == 2 and len(sent) == 2
    assert _tries(doc)[0]["retried"] == []


@pytest.mark.parametrize("status", [429, 503])
async def test_17_a_busy_app_or_gateway_isnt_the_apps_failed_check(make_rig, status) -> None:
    # It blips: one more go, and the second is the try.
    rig = make_rig()
    rig.add({**ONE_TURN, "limits": {"tries": 1, "seconds": 900}})
    real, sent = rig.agent.send, []

    async def blips(*args: Any, **kwargs: Any) -> Turn:
        sent.append(1)
        return _answered(status) if len(sent) == 1 else await real(*args, **kwargs)

    rig.agent.send = blips
    doc = await rig.run()
    assert _verdict(doc) == "pass" and len(sent) == 2
    ((retried),) = _tries(doc)[0]["retried"]
    assert retried["what"] == "app" and str(status) in retried["why"]

    # It stays that way: unjudged, each try on its own, and the run goes on.
    rig, sent = _rig_answering(make_rig, status, tries=2)
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and _label(doc) == "Setup blocked"
    assert current(_tries(doc)[0]).checks[0].unjudged is WhyUnjudged.APP_BUSY
    assert doc["status"] == "done" and len(_tries(doc)) == 2 and len(sent) == 4
    assert report_facts(doc)["failed"] == []


@pytest.mark.parametrize("marked", [True, False], ids=["marked", "by session"])
async def test_17_a_second_go_doesnt_read_the_first_ones_spans(clock, marked) -> None:
    source = _Source()
    marks = {"evalution.run": "r", "evalution.try": "1"} if marked else {}
    t0 = clock.now
    sends: list[int] = []

    async def blips(_message: str, _deadline: float) -> Turn:
        sends.append(1)
        if len(sends) == 1:
            return Turn("", clock.now, clock.now, error="busy", failure=Failure.TRANSIENT)
        now = clock.now
        source.spans += [
            # The first go's, come only now.
            _tool("late", "charge_card", t0 + 0.1, session="s", **marks),
            # This one's: the control.
            _span("turn", "AGENT", now + 0.1, session="s", **marks),
            _tool("own", "look_up", now + 0.2, parent="turn", session="s", **marks),
        ]
        return Turn("done", now, now)

    case = parse_case({"name": "t", **JUST_SAY}, "x", "t")
    session = _session()
    if marked:
        session.extra.update(run="r", **{"try": "1"})
    played = await play_with_retry(
        _Fake(blips), session, case, Path(), filler=None, source=source, settle=1.0
    )
    assert played.retried and len(sends) == 2
    assert [t.name for t in played.evidence.tools] == ["look_up"]


async def test_17_control_a_500_is_still_the_apps_failed_check(make_rig) -> None:
    rig, sent = _rig_answering(make_rig, 500, tries=2)
    doc = await rig.run()
    assert _verdict(doc) == "fail" and _label(doc) == "Failed check"
    assert doc["status"] == "done" and len(sent) == 2, "nothing about it is tried again"


async def test_17_no_second_go_once_something_shows_the_app_heard(make_rig) -> None:
    # The adapter knows: its message was taken, and what failed came after.
    rig = make_rig()
    rig.add({**ONE_TURN, "limits": {"tries": 1, "seconds": 900}})
    sent: list[int] = []

    async def taken(*_args: Any, **_kwargs: Any) -> Turn:
        sent.append(1)
        return _answered(503, heard=True)

    rig.agent.send = taken
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and len(sent) == 1 and _tries(doc)[0]["retried"] == []

    # The traces know: the agent worked on it, and a gateway lost the answer.
    rig = make_rig()
    rig.add({**ONE_TURN, "limits": {"tries": 1, "seconds": 900}})
    real, sent = rig.agent.send, []

    async def lost(*args: Any, **kwargs: Any) -> Turn:
        sent.append(1)
        await real(*args, **kwargs)
        return _answered(503)

    rig.agent.send = lost
    doc = await rig.run()
    assert _verdict(doc) == "unjudged" and len(sent) == 1 and _tries(doc)[0]["retried"] == []


async def test_17_work_a_gateway_lost_sight_of_ends_a_run_that_cant_stop_it(make_rig) -> None:
    rig = make_rig(options={"background": True})
    rig.add({**ONE_TURN, "limits": {"tries": 2, "seconds": 900}})

    async def taken(*_args: Any, **_kwargs: Any) -> Turn:
        return _answered(503, heard=True)

    rig.agent.send = taken
    doc = await rig.run()
    assert doc["error"] == STILL_WORKING and len(_tries(doc)) == 1


class _Fake(Target):
    """An app that does what *act* says with each message."""

    name = "fake"
    capabilities = Capabilities(Isolation.SESSION, "", blocks=True)

    def __init__(self, act: Any) -> None:
        self.act = act
        self.sends = 0

    async def open(self, run: str) -> Session:
        return _session()

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
        self.sends += 1
        return await self.act(message, deadline)

    async def close(self, session: Session) -> None:
        return None


def _session() -> Session:
    return Session("s", "session.id", "s", {})


class _Source:
    """Spans a source gives, as one with a strict window does: none begun after its end."""

    trace_url = staticmethod(lambda _trace: None)

    def __init__(self, *spans: Span) -> None:
        self.spans = list(spans)
        self.broken = False

    async def window(self, start: float, end: float) -> SpanRead:
        if self.broken:
            raise httpx.ReadError("the source is down")
        return SpanRead([s for s in self.spans if start - 5 <= s.start <= end])


def _span(id_: str, kind: str, at: float, trace: str = "A", parent=None, **attrs: Any) -> Span:
    if "session" in attrs:
        attrs["session.id"] = attrs.pop("session")
    return Span(id_, trace, parent, id_, kind, at, at + 0.5, "OK", nest(attrs))


def _llm(id_: str, at: float, trace: str = "A", parent=None, said="done", **attrs: Any) -> Span:
    out = [{"message": {"role": "assistant", "content": said}}]
    return _span(id_, "LLM", at, trace, parent, **{"llm.output_messages": out, **attrs})


def _tool(id_: str, name: str, at: float, trace: str = "A", parent=None, **attrs: Any) -> Span:
    return _span(id_, "TOOL", at, trace, parent, **{"tool.name": name, **attrs})


MARKS = {"tools": 0.7, "reply": 0.7}


def _judged(raw: dict[str, Any], evidence: TryEvidence, calls: Any = ()) -> Any:
    case = parse_case({"name": "t", **raw}, "x", "t")
    steps, score = grade_tools(case.tools, list(calls))
    code = code_checks(case, evidence, steps, score, MARKS)
    return judgement(code.checks, None, code.route)


async def _play(raw: dict[str, Any], target: Target, source: Any, **options: Any) -> Any:
    case = parse_case({"name": "t", **raw}, "x", "t")
    options.setdefault("settle", 1.0)
    return await play_try(target, _session(), case, Path(), filler=None, source=source, **options)


def _of(raw: dict[str, Any], played: Any) -> Any:
    calls = [Call(t.id, t.name, {}, t.start, t.end, t.helper) for t in played.evidence.tools]
    return _judged(raw, played.evidence, calls)


JUST_SAY = {"turns": [{"message": "Hi."}], "reply": {"contains": ["done"]}}


class _ResetHangs(_Fake):
    capabilities = Capabilities(Isolation.SESSION, "", reset=True)

    def __init__(self, act: Any, hangs: bool) -> None:
        super().__init__(act)
        self.hangs = hangs

    async def reset(self, session: Session) -> None:
        if self.hangs:
            await asyncio.sleep(60)


@pytest.mark.parametrize("hangs", [True, False], ids=["hangs", "control"])
async def test_17_a_reset_out_of_time_on_a_retry_keeps_the_first_go(monkeypatch, hangs) -> None:
    async def busy_once(_message: str, _deadline: float) -> Turn:
        now = time.time()
        if target.sends == 1:
            return Turn("", now, now, error="busy", failure=Failure.TRANSIENT)
        return Turn("done", now, now)

    target = _ResetHangs(busy_once, hangs)
    monkeypatch.setattr(play, "ADAPTER_SECONDS", 0.01)
    monkeypatch.setattr(play, "RETRY_SECONDS", 0)
    case = parse_case({"name": "t", **JUST_SAY}, "x", "t")
    played = await play_with_retry(target, _session(), case, Path(), filler=None, source=None)
    assert played.retried
    assert played.failure is (Failure.TRANSIENT if hangs else None)
    assert target.sends == (1 if hangs else 2)


# H3. A shared trace id


def _wanted(trace_ids: Any = ()) -> Wanted:
    return Wanted("session.id", ["try-1"], frozenset(), trace_ids, 100.0, 110.0, [])


def test_17_another_sessions_spans_in_a_shared_trace_are_not_this_trys() -> None:
    window = [
        _llm("mine", 100.0, trace="T", session="try-1"),
        _tool("other", "delete_all", 102.0, trace="T", session="someone-else"),
        # A span saying nothing of its own, under theirs, is theirs too.
        _tool("under", "delete_more", 103.0, trace="T", parent="other"),
        # Their session's other trace isn't reached through the one they share.
        _tool("further", "delete_too", 104.0, trace="T2", session="someone-else"),
    ]
    assert {s.id for s in own_spans(window, _wanted(), follow=True)} == {"mine"}
    # The trace named by the adapter is held to the same.
    assert {s.id for s in own_spans(window, _wanted(["T"]), follow=True)} == {"mine"}


def test_17_a_root_that_says_no_session_links_no_one() -> None:
    window = [
        _span("gateway", "CHAIN", 99.5, trace="T"),
        _llm("mine", 100.0, trace="T", parent="gateway", session="try-1"),
        _tool("other", "delete_all", 102.0, trace="T", parent="gateway", session="someone-else"),
    ]
    assert {s.id for s in own_spans(window, _wanted(), follow=True)} == {"gateway", "mine"}


def test_17_control_linked_child_work_in_a_shared_trace_still_counts() -> None:
    by_parent_span = [
        _llm("mine", 100.0, trace="T", session="try-1"),
        _tool("child", "write_file", 101.0, trace="T", parent="mine", session="child-1"),
        _tool("under", "read_file", 101.2, trace="T", parent="child"),
        # And the rest of that child session, in a trace of its own.
        _tool("later", "send_file", 103.0, trace="T3", session="child-1"),
    ]
    assert {s.id for s in own_spans(by_parent_span, _wanted(), follow=True)} == {
        "mine",
        "child",
        "under",
        "later",
    }
    by_parent_session = [
        _llm("mine", 100.0, trace="T", session="try-1"),
        _tool(
            "child",
            "write_file",
            101.0,
            trace="T",
            session="child-1",
            **{"session.parent_id": "try-1"},
        ),
        _tool(
            "away",
            "plan",
            102.0,
            trace="T2",
            session="child-2",
            **{"gen_ai.conversation.parent_id": "try-1"},
        ),
    ]
    assert {s.id for s in own_spans(by_parent_session, _wanted(), follow=True)} == {
        "mine",
        "child",
        "away",
    }


def test_17_an_adapter_named_trace_of_an_app_with_its_own_session_ids_is_kept() -> None:
    """The app calls its session something else: the adapter's naming is the only tie there is."""
    window = [
        _llm("root", 100.0, trace="T", session="the-apps-own-id"),
        _tool("tool", "search", 101.0, trace="T", parent="root", session="the-apps-own-id"),
        _tool("other", "delete_all", 102.0, trace="T", session="someone-else"),
    ]
    assert {s.id for s in own_spans(window, _wanted(["T"]), follow=True)} == {"root", "tool"}


async def test_17_a_shared_trace_id_doesnt_fail_a_never_check(clock) -> None:
    raw = {"turns": [{"message": "Hi."}], "tools": {"never": ["delete_all"]}}

    async def answers(_message: str, _deadline: float) -> Turn:
        return Turn("done", clock.now, clock.now)

    t0 = clock.now
    theirs = _tool("other", "delete_all", t0 + 0.2, trace="T", session="someone-else")
    mine = _llm("mine", t0 + 0.1, trace="T", session="s")
    played = await _play(raw, _Fake(answers), _Source(mine, theirs))
    assert _of(raw, played).outcome is Outcome.PASSED and not played.evidence.tools
    # Control: the same tool in this session.
    ours = _tool("ours", "delete_all", t0 + 0.2, trace="T", session="s")
    played = await _play(raw, _Fake(answers), _Source(mine, ours))
    assert _of(raw, played).outcome is Outcome.FAILED


# H4. A partial read


def _steps_evidence(trace: TraceStatus) -> TryEvidence:
    turn = TurnRecord(
        kind="message", message=Clipped(text="hi"), reply=Clipped(text="ok"), started=0, ended=1
    )
    return TryEvidence(case="t", n=1, t0=0, seconds=1, turns=(turn,), trace=trace)


@pytest.mark.parametrize("binding", [{"extras": False}, {"order": "exact"}])
def test_17_binding_steps_cant_pass_on_a_partial_read(binding) -> None:
    raw = {"turns": [{"message": "hi"}], "tools": {"expected": [{"name": "a"}], **binding}}
    calls = [Call("1", "a", {}, 0, 1, None)]
    found = _judged(raw, _steps_evidence(TraceStatus.PARTIAL), calls)
    assert found.outcome is Outcome.UNJUDGED
    assert found.checks[0].unjudged is WhyUnjudged.PARTIAL_TRACE
    # Control: on a complete read the same calls hold.
    assert _judged(raw, _steps_evidence(TraceStatus.COMPLETE), calls).outcome is Outcome.PASSED


@pytest.mark.parametrize("order", ["in_order", "any_order"])
def test_17_control_plain_expected_calls_can_pass_on_a_partial_read(order) -> None:
    raw = {
        "turns": [{"message": "hi"}],
        "tools": {"expected": [{"name": "a"}], "order": order},
    }
    calls = [Call("1", "a", {}, 0, 1, None)]
    assert _judged(raw, _steps_evidence(TraceStatus.PARTIAL), calls).outcome is Outcome.PASSED


# H5. After the reply


async def test_17_a_never_tool_after_the_reply_is_seen(clock) -> None:
    raw = {"turns": [{"message": "Hi."}], "tools": {"never": ["charge_card"]}}

    async def answers(_message: str, _deadline: float) -> Turn:
        return Turn("done", clock.now, clock.now)

    t0 = clock.now
    late = _tool("bad", "charge_card", t0 + 4, session="s")
    played = await _play(raw, _Fake(answers), _Source(_llm("a", t0, session="s"), late), settle=6)
    assert [t.name for t in played.evidence.tools] == ["charge_card"]
    assert _of(raw, played).outcome is Outcome.FAILED

    # Control: nothing came late.
    played = await _play(raw, _Fake(answers), _Source(_llm("b", clock.now, session="s")))
    assert played.evidence.trace is TraceStatus.COMPLETE
    assert _of(raw, played).outcome is Outcome.PASSED


async def test_17_a_read_that_keeps_finding_more_still_ends(clock) -> None:
    async def answers(_message: str, _deadline: float) -> Turn:
        return Turn("done", clock.now, clock.now)

    class Endless(_Source):
        async def window(self, start: float, end: float) -> SpanRead:
            self.spans.append(_llm(f"n{len(self.spans)}", clock.now, session="s"))
            return await super().window(start, end)

    began = clock.now
    played = await _play(JUST_SAY, _Fake(answers), Endless(), settle=6)
    assert played.evidence.trace is TraceStatus.PARTIAL and clock.now - began < 30


# H7. What the model was sent


def _context_evidence(*calls: ModelCallRecord, trace=TraceStatus.COMPLETE) -> TryEvidence:
    def turn(message: str, started: float, ended: float) -> TurnRecord:
        return TurnRecord(
            kind="message",
            message=Clipped(text=message),
            reply=Clipped(text="ok"),
            started=started,
            ended=ended,
        )

    return TryEvidence(
        case="t",
        n=1,
        t0=0,
        seconds=3,
        turns=(turn("a", 0, 0.4), turn("b", 0.5, 2)),
        model_calls=calls,
        trace=trace,
    )


def _sent(n: int, text: str, at: float = 0.6, helper: str | None = None) -> ModelCallRecord:
    return ModelCallRecord(
        n=n,
        span=f"c{n}",
        t=at,
        model="m",
        sent=Clipped(text=text),
        said=Clipped(text="ok"),
        helper=helper,
    )


def _context_check(key: str, evidence: TryEvidence) -> Any:
    with_state = evidence.model_copy(
        update={"states": (StateRecord(index=0, key=key, value="WORD", at=0.45),)}
    )
    raw = {"turns": [{"message": "a"}, {"expect_state": {key: "WORD"}}, {"message": "b"}]}
    return _judged(raw, with_state).checks[0]


def test_17_context_is_read_on_the_answering_call() -> None:
    guard, main = _sent(1, "classify this message"), _sent(2, "you remember WORD", 0.7)
    lacks = _context_check("context_lacks", _context_evidence(guard, main))
    assert lacks.outcome is Outcome.FAILED and "model call 2" in lacks.why
    assert lacks.evidence[0].ref == "span:c2"
    contains = _context_check("context_contains", _context_evidence(guard, main))
    assert contains.outcome is Outcome.PASSED and "model call 2" in contains.why

    # Control: no call has it.
    clean = _context_evidence(guard, _sent(2, "you remember nothing", 0.7))
    assert _context_check("context_lacks", clean).outcome is Outcome.PASSED
    assert _context_check("context_contains", clean).outcome is Outcome.FAILED
    # A guard or a compaction first, with the word: it doesn't decide. A fact withdrawn before the
    # answering call is gone, and one the compaction dropped is lost, whatever it was sent.
    summarised = _sent(1, "summarise: WORD; scratch that")
    answers = _sent(2, "summary: nothing relevant", 0.7)
    both = _context_evidence(summarised, answers)
    assert _context_check("context_lacks", both).outcome is Outcome.PASSED
    assert _context_check("context_contains", both).outcome is Outcome.FAILED
    # Only the last that said something answers: a call after it with no output doesn't.
    silent = _sent(3, "still WORD", 0.8).model_copy(update={"said": Clipped()})
    assert _context_check("context_lacks", _context_evidence(answers, silent)).outcome is (
        Outcome.PASSED
    )
    # Nor does one still going when the reply came.
    late = _sent(3, "still WORD", 0.8).model_copy(update={"ended": 9.0})
    assert _context_check("context_lacks", _context_evidence(answers, late)).outcome is (
        Outcome.PASSED
    )
    # Calls, and none that answered.
    quiet = _context_evidence(silent)
    for key in ("context_lacks", "context_contains"):
        found = _context_check(key, quiet)
        assert found.outcome is Outcome.UNJUDGED and "none that answered" in found.why


def test_17_context_leaves_out_helpers_and_what_came_after_the_reply() -> None:
    main = _sent(1, "plain request")
    helper = _sent(2, "the helper has WORD", 0.7, helper="h1")
    after = _sent(3, "summarise: WORD", 3.0)
    for other in (helper, after):
        evidence = _context_evidence(main, other)
        assert _context_check("context_lacks", evidence).outcome is Outcome.PASSED
        assert _context_check("context_contains", evidence).outcome is Outcome.FAILED
    # Control: the same calls as the assistant's own, within the turn.
    within = _context_evidence(main, _sent(2, "the assistant has WORD", 0.7))
    assert _context_check("context_lacks", within).outcome is Outcome.FAILED


@pytest.mark.parametrize("marker", ["__REDACTED__", "[REDACTED]", "<redacted>"])
def test_17_a_redacted_request_is_not_a_captured_one(marker) -> None:
    hidden = _sent(1, marker)
    for key in ("context_lacks", "context_contains"):
        found = _context_check(key, _context_evidence(hidden))
        assert found.unjudged is WhyUnjudged.CONTENT_NOT_CAPTURED
    # The answering call hidden: it can't show a word is absent, and can't show it's missing.
    some = _context_evidence(_sent(1, "plain request"), _sent(2, marker, 0.7))
    assert _context_check("context_lacks", some).outcome is Outcome.UNJUDGED
    assert _context_check("context_contains", some).outcome is Outcome.UNJUDGED
    # What was seen on it still decides.
    seen = _context_evidence(_sent(1, "plain request"), _sent(2, f"WORD {marker}", 0.7))
    assert _context_check("context_lacks", seen).outcome is Outcome.FAILED
    assert _context_check("context_contains", seen).outcome is Outcome.PASSED
    # Seen before it, it decides nothing: the answering call was hidden.
    earlier = _context_evidence(_sent(1, "you remember WORD"), _sent(2, marker, 0.7))
    assert _context_check("context_lacks", earlier).outcome is Outcome.UNJUDGED


def test_17_context_on_a_partial_read_decides_nothing() -> None:
    """The answering call may not have come: what was seen may be a compaction before it."""
    partial = TraceStatus.PARTIAL
    for text in ("you remember WORD", "plain request"):
        some = _context_evidence(_sent(1, text), trace=partial)
        for key in ("context_lacks", "context_contains"):
            assert _context_check(key, some).unjudged is WhyUnjudged.PARTIAL_TRACE
    # Control: read whole, the same call decides.
    whole = _context_evidence(_sent(1, "you remember WORD"))
    assert _context_check("context_lacks", whole).outcome is Outcome.FAILED


def test_17_old_evidence_with_no_helper_still_loads() -> None:
    kept = {"n": 1, "span": "a", "t": 0.1, "model": "m", "sent": {"text": "x"}, "said": {}}
    assert ModelCallRecord.model_validate(kept).helper is None
    # A turn an older adapter called only acknowledged reads with the rest of its record.
    assert TurnEnd("acknowledged") is TurnEnd.ANSWERED


async def test_17_a_helpers_model_call_comes_through_from_the_traces(clock) -> None:
    raw = {
        "turns": [
            {"message": "a"},
            {"expect_state": {"context_lacks": "WORD"}},
            {"message": "b"},
        ]
    }
    source = _Source()

    async def answers(message: str, _deadline: float) -> Turn:
        started = clock.now
        clock.now += 2
        if message == "b":
            source.spans += [
                _llm("main", started + 0.2, session="s", **{"input.value": "plain request"}),
                _llm(
                    "child",
                    started + 0.4,
                    session="child-1",
                    **{"session.parent_id": "s", "input.value": "helper has WORD"},
                ),
            ]
        return Turn("done", started, clock.now)

    played = await _play(raw, _Fake(answers), source)
    assert [c.helper is None for c in played.evidence.model_calls] == [True, False]
    assert _of(raw, played).outcome is Outcome.PASSED


# M1. An adapter that throws


@pytest.mark.parametrize(
    "error",
    [ValueError("no reply here"), RuntimeError("bad"), OSError("disk"), KeyError("reply")],
    ids=lambda e: type(e).__name__,
)
async def test_17_an_exception_from_send_is_the_adapters(clock, error) -> None:
    async def raises(_message: str, _deadline: float) -> Turn:
        raise error

    played = await _play(JUST_SAY, _Fake(raises), None)
    assert played.failure is Failure.ADAPTER and not played.gone, "the run takes two in a row"
    assert label_of([_of(JUST_SAY, played)]) == "Adapter error"


@pytest.mark.parametrize(
    ("error", "failure", "label"),
    [
        (TargetGone("gone"), Failure.UNREACHABLE, "Setup blocked"),
        (httpx.ConnectError("refused"), Failure.UNREACHABLE, "Setup blocked"),
        (TimeoutError(), Failure.TIMED_OUT, "Timed out"),
        (httpx.ReadTimeout("slow"), Failure.TIMED_OUT, "Timed out"),
        (AppError("the app said no"), Failure.APP, "Failed check"),
        (_http_error(500), Failure.APP, "Failed check"),
        (_http_error(401), Failure.REFUSED, "Setup blocked"),
        (_http_error(503), Failure.TRANSIENT, "Setup blocked"),
    ],
    ids=lambda v: v if isinstance(v, str) else type(v).__name__,
)
async def test_17_control_what_send_may_raise_keeps_its_meaning(
    clock, error, failure, label
) -> None:
    async def raises(_message: str, _deadline: float) -> Turn:
        raise error

    played = await _play(JUST_SAY, _Fake(raises), None)
    assert played.failure is failure and label_of([_of(JUST_SAY, played)]) == label


# M2. No sign of work


async def _times_out(clock: Any) -> Any:
    async def hangs(_message: str, deadline: float) -> Turn:
        clock.now = deadline
        return Turn("", deadline, deadline, error="not done", failure=Failure.TIMED_OUT)

    return _Fake(hangs)


async def test_17_a_hang_says_no_sign_of_work_not_that_it_never_started(clock) -> None:
    played = await _play(JUST_SAY, await _times_out(clock), _Source())
    found = _of(JUST_SAY, played)
    assert found.outcome is Outcome.UNJUDGED and label_of([found]) == "Setup blocked"
    assert found.checks[0].unjudged is WhyUnjudged.NEVER_STARTED
    assert "no sign of the agent working" in found.checks[0].why
    assert "never started" not in found.checks[0].why


async def test_17_a_source_that_failed_is_not_a_quiet_agent(clock) -> None:
    source = _Source()
    source.broken = True
    played = await _play(JUST_SAY, await _times_out(clock), source)
    found = _of(JUST_SAY, played)
    assert played.evidence.trace is TraceStatus.UNREAD
    assert "the source is down" in str(played.evidence.trace_note)
    assert found.outcome is Outcome.UNJUDGED and label_of([found]) == "Missing evidence"
    assert found.checks[0].unjudged is WhyUnjudged.NO_TRACES
    assert "no sign" not in found.checks[0].why

    # And for checks the traces answer, on a try that went well: nothing was read.
    async def answers(_message: str, _deadline: float) -> Turn:
        return Turn("done", clock.now, clock.now)

    raw = {"turns": [{"message": "Hi."}], "tools": {"never": ["charge_card"]}}
    played = await _play(raw, _Fake(answers), source)
    check = _of(raw, played).checks[0]
    assert check.outcome is Outcome.UNJUDGED and check.unjudged is WhyUnjudged.NO_TRACES


def _hangs_then_answers(rig: Any, hangs: int) -> None:
    real, sent = rig.agent.send, []

    async def send(*args: Any, **kwargs: Any) -> Turn:
        sent.append(1)
        if len(sent) <= hangs:
            return rig.agent._out_of_time("", rig.agent.clock.now)
        return await real(*args, **kwargs)

    rig.agent.send = send


def test_17_a_pass_with_quiet_tries_says_so_on_its_verdict_line(
    make_rig, capsys, monkeypatch
) -> None:
    rig = make_rig()
    stem = rig.add({"name": "Answers", "turns": [ASKS], "limits": {"tries": 3, "seconds": 60}})
    _hangs_then_answers(rig, 2)
    monkeypatch.setattr(cli, "Targets", lambda _config: rig.targets)
    capsys.readouterr()
    cli.main(["--config", str(rig.config_file), "run", "--target", TARGET, "--set", SET, "--yes"])
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith(stem))
    note = "2 of 3 tries couldn't be judged"
    assert note in row
    (doc,) = rig.runs.store.all(TARGET, SET)
    entry = doc["cases"][stem]
    assert _verdict(doc) == "pass", "it isn't held against the agent"
    assert case_row(stem, entry["name"], entry)["note"] == note
    assert _run_test(doc, stem, entry)["note"] == note
    assert case_detail(doc, stem)["note"] == note
    assert report_facts(doc)["held"][0]["note"] == note
    # Control: all three worked.
    well = make_rig()
    stem = well.add({"name": "Answers", "turns": [ASKS], "limits": {"tries": 3, "seconds": 60}})
    doc = asyncio.run(well.run())
    assert "note" not in _run_test(doc, stem, doc["cases"][stem])


# M3. Blaming the adapter


def test_17_an_answer_says_nothing_until_a_closed_turn_encloses_it() -> None:
    root = _llm("a", 1.0)
    assert answered_at([root], 0.0) is None, "no turn around it: it may be working yet"
    turn = _span("turn", "AGENT", 0.5)
    inner = _llm("a", 1.0, parent="step")
    step = _span("step", "CHAIN", 0.8, parent="turn")
    assert answered_at([inner, step], 0.0) is None, "a step closed, and the turn above it open"
    assert answered_at([inner, step, turn], 0.0) == 1.5


async def _hangs_on_stuck(clock: Any, *spans: Span) -> Any:
    async def hangs(_message: str, deadline: float) -> Turn:
        while clock.now < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0)
        return Turn("", deadline, deadline, error="not done", failure=Failure.TIMED_OUT)

    raw = {"name": "t", "turns": [{"message": "Hi."}], "limits": {"seconds": 300}}
    return await _play(raw, _Fake(hangs), _Source(*spans), stuck=60.0)


async def test_17_a_real_hang_after_a_bare_model_call_is_the_apps(clock) -> None:
    played = await _hangs_on_stuck(clock, _llm("a", clock.now + 1, session="s"))
    assert played.failure is Failure.TIMED_OUT and "still waiting" not in str(played.error)
    # Control: a closed turn around the same call. The adapter missed it.
    t0 = clock.now
    played = await _hangs_on_stuck(
        clock,
        _span("turn", "AGENT", t0 + 0.5, session="s"),
        _llm("a", t0 + 1, parent="turn", session="s"),
    )
    assert played.failure is Failure.ADAPTER and "still waiting" in str(played.error)
    # A side call with its own root, while the main call's tool runs, is no answer: a real hang.
    t0 = clock.now
    played = await _hangs_on_stuck(
        clock,
        _asks_for_a_tool("asks", t0 + 1, "turn", session="s"),
        _span("sr", "CHAIN", t0 + 2, "B", session="s"),
        _llm("title", t0 + 2, "B", "sr", said="Order lookup", session="s"),
    )
    assert played.failure is Failure.TIMED_OUT and "still waiting" not in str(played.error)


async def test_17_an_empty_reply_after_only_a_side_call_isnt_the_adapters(clock) -> None:
    async def answers(_message: str, _deadline: float) -> Turn:
        return Turn("", clock.now, clock.now)

    t0 = clock.now
    side = (
        _span("sr", "CHAIN", t0 + 2, "B", session="s"),
        _llm("title", t0 + 2, "B", "sr", said="Order lookup", session="s"),
    )
    source = _Source(_asks_for_a_tool("asks", t0 + 1, "turn", session="s"), *side)
    played = await _play(JUST_SAY, _Fake(answers), source)
    assert played.failure is None
    # Control: the turn's own tree said it.
    own = _Source(_llm("said", clock.now + 1, parent="turn", session="s"), *side)
    played = await _play(JUST_SAY, _Fake(answers), own)
    assert played.failure is Failure.ADAPTER and "empty reply" in str(played.error)


_CAME_BACK = {"error": "POST /chat answered 500", "failure": Failure.APP}


@pytest.mark.parametrize(
    ("reply", "turn", "failure"),
    [
        # A reply read and then an error, or a status the app really returned, are the app's.
        ("I answered", {"error": "the app fell over"}, Failure.APP),
        ("", {"error": "the app fell over"}, Failure.APP),
        ("", _CAME_BACK, Failure.APP),
        ("", {"error": "busy", "failure": Failure.TRANSIENT}, Failure.TRANSIENT),
        ("", {"error": "no", "failure": Failure.REFUSED}, Failure.REFUSED),
        # The wait itself never came back, long after the agent finished.
        ("", {"error": "not done", "failure": Failure.TIMED_OUT}, Failure.ADAPTER),
        ("", {"error": "gone", "failure": Failure.UNREACHABLE}, Failure.ADAPTER),
        ("", {"error": "broke", "failure": Failure.APP, "lost": True}, Failure.ADAPTER),
    ],
)
async def test_17_an_error_long_after_a_closed_turn_is_the_adapters_only_if_it_never_came_back(
    clock, reply, turn, failure
) -> None:
    async def errs(_message: str, _deadline: float) -> Turn:
        started = clock.now
        clock.now += 40
        return Turn(reply, started, clock.now, **turn)

    t0 = clock.now
    source = _Source(
        _span("turn", "AGENT", t0 + 0.5, session="s"),
        _llm("a", t0 + 1, parent="turn", session="s"),
    )
    played = await _play(JUST_SAY, _Fake(errs), source, stuck=120.0)
    assert played.failure is failure


@pytest.mark.parametrize(
    ("raised", "failure"),
    [(_http_error(500), Failure.APP), (httpx.ReadError("cut off"), Failure.ADAPTER)],
    ids=["status", "connection"],
)
async def test_17_a_raised_error_long_after_a_closed_turn_says_the_same(
    clock, raised, failure
) -> None:
    async def errs(_message: str, _deadline: float) -> Turn:
        clock.now += 40
        raise raised

    t0 = clock.now
    source = _Source(
        _span("turn", "AGENT", t0 + 0.5, session="s"),
        _llm("a", t0 + 1, parent="turn", session="s"),
    )
    played = await _play(JUST_SAY, _Fake(errs), source, stuck=120.0)
    assert played.failure is failure


async def test_17_control_an_empty_reply_after_a_bare_model_call_is_still_the_adapters(
    clock,
) -> None:
    async def answers(_message: str, _deadline: float) -> Turn:
        return Turn("", clock.now, clock.now)

    played = await _play(JUST_SAY, _Fake(answers), _Source(_llm("a", clock.now, session="s")))
    assert played.failure is Failure.ADAPTER and "empty reply" in str(played.error)


# M4. A turn expected to wait


async def test_17_an_expected_wait_with_no_words_isnt_the_adapters(clock) -> None:
    raw = {"turns": [{"message": "Wire it.", "blocked": True}]}

    async def waits(_message: str, _deadline: float) -> Turn:
        return Turn("", clock.now, clock.now, ended_as=TurnEnd.BLOCKED)

    source = _Source(_llm("a", clock.now, session="s", said="I need approval first."))
    played = await _play(raw, _Fake(waits), source)
    assert played.failure is None and _of(raw, played).outcome is Outcome.PASSED

    # Control: the same words and no wait, read as nothing: that is the adapter.
    async def answers(_message: str, _deadline: float) -> Turn:
        return Turn("", clock.now, clock.now)

    plain = {"turns": [{"message": "Wire it."}]}
    source = _Source(_llm("b", clock.now, session="s", said="I need approval first."))
    assert (await _play(plain, _Fake(answers), source)).failure is Failure.ADAPTER


async def test_17_a_try_that_ends_waiting_as_expected_stops_that_work(make_rig) -> None:
    expecting = {"name": "Waits", "turns": [{"message": "Hello there.", "blocked": True}]}
    rig = make_rig(["work_waits"], options={"can_block": True, "can_cancel": True})
    rig.add({**expecting, "limits": {"tries": 1}})
    doc = await rig.run()
    assert _verdict(doc) == "pass" and rig.agent.cancelled, "nothing stays waiting"
    # Control: nothing was left waiting.
    rig = make_rig(options={"can_block": True, "can_cancel": True})
    rig.add({"name": "Answers", "turns": [ASKS], "limits": {"tries": 1}})
    await rig.run()
    assert not rig.agent.cancelled


# M5. Tests left out of a run


NEEDS_MORE = {
    "name": "Needs a check it lacks",
    "turns": [{"message": "Hi."}, {"expect_state": {"no_such_check": "x"}}],
}
THE_RUN = ["run", "--target", TARGET, "--set", SET, "--yes"]


def test_17_a_cli_run_says_what_it_left_out_and_doesnt_pass(make_rig, capsys, monkeypatch) -> None:
    rig = make_rig()
    monkeypatch.setattr(cli, "Targets", lambda _config: rig.targets)
    rig.add({"name": "Answers", "turns": [ASKS], "limits": {"tries": 1}})
    argv = ["--config", str(rig.config_file), *THE_RUN]
    assert cli.main(argv) == 0, "control: nothing left out"
    rig.add(NEEDS_MORE)
    capsys.readouterr()
    code = cli.main(argv)
    out = capsys.readouterr()
    assert code != 0
    assert "needs-a-check-it-lacks" in out.out + out.err and "no_such_check" in out.out + out.err


def test_17_a_cli_run_with_only_refused_tests_plays_nothing(make_rig, capsys, monkeypatch) -> None:
    rig = make_rig()
    monkeypatch.setattr(cli, "Targets", lambda _config: rig.targets)
    rig.add(NEEDS_MORE)
    capsys.readouterr()
    assert cli.main(["--config", str(rig.config_file), *THE_RUN]) != 0
    assert "no_such_check" in "".join(capsys.readouterr())
    assert rig.agent.every_send == 0 and not rig.runs.store.all(TARGET, SET)


# M9. Grading again without a grader


async def test_17_grading_again_with_no_model_is_refused(make_rig) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    doc = await rig.run()
    rig.runs.llm = None
    with pytest.raises(ValueError, match="no model"):
        rig.runs.estimate_regrade(doc["run"])
    assert rig.runs.load(doc["run"])["judge"] == doc["judge"]


def test_17_a_grader_that_fails_leaves_the_results_standing(make_rig, capsys, monkeypatch) -> None:
    rig = make_rig()
    rig.add(_case(ASKS))
    doc = asyncio.run(rig.run())
    run, stem = doc["run"], next(iter(doc["cases"]))
    before = rig.runs.load(run)["cases"][stem]["tries"][0]
    rig.judge.faults = frozenset({"judge_outage"})
    token = rig.runs.estimate_regrade(run)["token"]
    assert asyncio.run(rig.runs.regrade(token)) == {"graded": 0, "failed": 1}
    after = rig.runs.load(run)["cases"][stem]["tries"][0]
    assert after["judgements"] == before["judgements"] and after["rubric"] == before["rubric"]

    # Told so by the CLI, which doesn't say it graded what it didn't.
    monkeypatch.setattr(cli, "Targets", lambda _config: rig.targets)
    monkeypatch.setattr(cli, "Runs", lambda *_args: rig.runs)
    capsys.readouterr()
    argv = ["--config", str(rig.config_file), "regrade", run, "--judge", "--yes"]
    assert cli.main(argv) != 0
    said = capsys.readouterr()
    assert "0 tries graded again" in said.out and "1 left as it was" in said.out + said.err

    # Control: the grader back, the judgement is added.
    rig.judge.faults = frozenset()
    rig.judge.default = "fail"
    token = rig.runs.estimate_regrade(run)["token"]
    assert asyncio.run(rig.runs.regrade(token)) == {"graded": 1, "failed": 0}
    judged = rig.runs.load(run)["cases"][stem]["tries"][0]["judgements"]
    assert [j["outcome"] for j in judged] == ["passed", "failed"]


# 18. What the last round left


def _span_asked(asked: str, said: str, parent: str | None, span: str = "a") -> Span:
    attrs = {
        "input": {"value": json.dumps({"messages": [{"role": "user", "content": asked}]})},
        "gen_ai": {"output": {"messages": json.dumps([{"role": "assistant", "content": said}])}},
    }
    return Span(span, "t", parent, "llm", "LLM", 0.0, 1.0, "OK", attrs)


def test_18_a_call_on_the_side_of_a_turn_isnt_its_answer() -> None:
    """All that's visible is a closed turn holding one model call: was it the answer?"""
    root = Span("root", "t", None, "side", "AGENT", 0.0, 1.5, "OK", {})
    side = _span_asked("Give this chat a title", "A title", "root")
    assert answered_at([side, root], 0.0, asked="Make the report") is None
    meant = _span_asked("Make the report", "Here it is", "root")
    assert answered_at([meant, root], 0.0, asked="Make the report") == 1.0, "control"


def test_18_the_context_read_is_the_call_whose_words_are_the_reply() -> None:
    answer = ModelCallRecord(
        n=1,
        span="a",
        t=0.0,
        ended=1.0,
        model="m",
        sent=Clipped(text="fact"),
        said=Clipped(text="Yes."),
    )
    late_side = ModelCallRecord(
        n=2,
        span="b",
        t=0.5,
        ended=1.2,
        model="m",
        sent=Clipped(text="x"),
        said=Clipped(text="A title"),
    )
    turn = TurnRecord(
        kind="message",
        message=Clipped(text="q"),
        reply=Clipped(text="Yes."),
        started=100.0,
        ended=101.0,
    )
    evidence = TryEvidence(
        case="c", n=1, t0=100.0, seconds=2.0, turns=(turn,), trace=TraceStatus.COMPLETE
    )
    state = StateRecord(index=0, key="context_contains", value="fact", at=0.0)
    assert _answering(state, [answer, late_side], evidence) is answer
    unmatched = evidence.model_copy(
        update={"turns": (turn.model_copy(update={"reply": Clipped(text="Other")}),)}
    )
    assert _answering(state, [answer, late_side], unmatched) is late_side, (
        "control: last that spoke"
    )


async def test_18_a_plain_app_waits_for_traces_once_a_run(make_rig, clock) -> None:
    rig = make_rig()
    rig.add({**ONE_TURN, "limits": {"tries": 3, "seconds": 30}})
    rig.agent.no_spans_on_tries = {0, 1, 2, 3}
    began = clock.now
    doc = await rig.run()
    spent = clock.now - began
    assert all(t["trace"] == "empty" for t in _tries(doc)) and len(_tries(doc)) == 3
    assert spent < 2 * TRACE_WAIT + 3 * rig.agent.turn_seconds, spent
