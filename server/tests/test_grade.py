"""The rubric decides a reply: every line or N of them; an unsure line is graded again."""

import json

import httpx
import pytest

from evalution import llm
from evalution.config import ModelConfig
from evalution.core.evidence import (
    Clipped,
    MemoryRecord,
    ModelCallRecord,
    StateRecord,
    ToolRecord,
    TraceStatus,
    TryEvidence,
    TurnRecord,
)
from evalution.core.judgement import Basis, Outcome, Role, WhyUnjudged
from evalution.core.rules import basis_of, judge_try, line_reason, steps_bind
from evalution.judging import rubric
from evalution.judging.checks import code_checks
from evalution.judging.memory import grade_memory
from evalution.judging.render import BUDGET, NEW_CONVERSATION, render
from evalution.judging.rubric import (
    INSTRUCTIONS,
    REFERENCE_LINE,
    SCEPTIC_INSTRUCTIONS,
    TIE_BREAK_INSTRUCTIONS,
    grade_reply,
    parse_grade,
)
from evalution.review.findings import try_input
from evalution.suite.cases import CaseError, parse_case

RUBRIC = ["Links both decks", "Says they share a look", "Short"]


def _line(ok: bool, sure: bool = True) -> dict:
    return {"pass": ok, "sure": sure, "reason": "r"}


def _answer(*lines: dict) -> str:
    numbered = [{"id": n, "line": "x", **line} for n, line in enumerate(lines, 1)]
    return json.dumps({"lines": numbered})


def _evidence(reply: str = "reply", **fields) -> TryEvidence:
    said = TurnRecord(
        kind="message",
        message=Clipped(text="hi"),
        reply=Clipped(text=reply),
        started=0.0,
        ended=1.0,
    )
    return TryEvidence(case="c", n=1, t0=0.0, seconds=1.0, turns=(said,), **fields)


def _caller(*answers: str):
    seen: list[str] = []

    async def call(*, instructions: str, input_text: str) -> str:
        seen.append(input_text)
        return answers[len(seen) - 1]

    call.seen = seen  # type: ignore[attr-defined]
    return call


async def test_a_passing_failure_is_retried_once_and_only_the_answer_costs(monkeypatch) -> None:
    monkeypatch.setattr(llm, "ATTEMPTS", 1)
    waited: list[float] = []

    async def wait(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(rubric, "_sleep", wait)
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
    good = {"choices": [{"message": {"content": _answer(_line(True))}}], "usage": usage}

    def model(answers: list[httpx.Response]) -> llm.LLM:
        config = ModelConfig(
            kind="openai",
            base_url="http://model",
            api_key_env=None,
            models={"strong": "m"},
            prices={"m": {"input": 2.0, "output": 0.0}},
        )
        return llm.LLM(
            config,
            transport=httpx.MockTransport(lambda _: answers.pop(0)),
        )

    calm = model([httpx.Response(200, json=good)])
    graded = await grade_reply(calm.caller("strong"), _evidence(), "", ["x"])
    assert graded.retried is None and calm.spent_usd == 2.0 and not waited, "control"

    busy = model([httpx.Response(503, text="busy"), httpx.Response(200, json=good)])
    graded = await grade_reply(busy.caller("strong"), _evidence(), "", ["x"])
    assert graded.checks[0].outcome is Outcome.PASSED and "503" in graded.retried
    assert busy.spent_usd == 2.0, "one answer, one charge"
    assert len(waited) == 1

    # It says no: the same again wouldn't change that.
    waited.clear()
    refused = model([httpx.Response(401, text="no")])
    graded = await grade_reply(refused.caller("strong"), _evidence(), "", ["x"])
    assert graded.retried is None and not waited
    assert graded.checks[0].unjudged is WhyUnjudged.JUDGE_FAILED

    down = model([httpx.Response(503, text="busy"), httpx.Response(503, text="busy")])
    graded = await grade_reply(down.caller("strong"), _evidence(), "", ["x"])
    assert graded.checks[0].unjudged is WhyUnjudged.JUDGE_FAILED and graded.retried
    assert down.spent_usd == 0.0


async def test_the_score_is_the_share_of_lines_that_held() -> None:
    caller = _caller(
        # A number of the model's own, as older grader answers carry, counts for nothing.
        json.dumps({**json.loads(_answer(_line(True), _line(False), _line(False))), "score": 0.95}),
    )
    graded = await grade_reply(caller, _evidence(), "", RUBRIC)
    assert graded.score == 0.333
    assert [c.outcome for c in graded.checks] == [Outcome.PASSED, Outcome.FAILED, Outcome.FAILED]
    assert len(caller.seen) == 1  # sure of every line: graded once


def _sceptic_caller(*answers: str):
    """The answers in order, and which prompt each call got."""
    seen: list[tuple[str, str]] = []

    async def call(*, instructions: str, input_text: str) -> str:
        seen.append((instructions, input_text))
        return answers[len(seen) - 1]

    call.seen = seen  # type: ignore[attr-defined]
    return call


async def test_only_an_unsure_line_is_graded_again_sceptically() -> None:
    caller = _sceptic_caller(
        _answer(_line(True), _line(True), _line(False, sure=False)),
        json.dumps({"lines": [{"id": 3, **_line(True)}]}),
    )
    graded = await grade_reply(caller, _evidence(), "", RUBRIC)
    assert [i for i, _ in caller.seen] == [INSTRUCTIONS, SCEPTIC_INSTRUCTIONS]
    again = caller.seen[1][1]
    assert "3. Short" in again and "Links both decks" not in again
    assert [c.outcome for c in graded.checks] == [Outcome.PASSED] * 3
    assert graded.score == 1.0  # the second grading decided that line
    assert graded.lines[2]["split"] and graded.lines[2]["second"]["pass"] is True


@pytest.mark.parametrize(
    ("second", "want", "why", "score"),
    [
        (_line(True, sure=False), Outcome.UNJUDGED, WhyUnjudged.JUDGE_UNSURE, None),
        (_line(False, sure=False), Outcome.UNJUDGED, WhyUnjudged.JUDGE_UNSURE, None),
        (_line(False), Outcome.FAILED, None, 0.0),
    ],
)
async def test_a_second_grading(second, want, why, score) -> None:
    caller = _sceptic_caller(
        _answer(_line(True, sure=False)), json.dumps({"lines": [{"id": 1, **second}]})
    )
    graded = await grade_reply(caller, _evidence(), "", RUBRIC[:1])
    assert graded.checks[0].outcome is want and graded.checks[0].unjudged is why
    assert graded.score == score


async def test_a_tie_break_gets_the_final_word_only_when_asked_for() -> None:
    def unsure() -> tuple[str, str]:
        return _answer(_line(True, sure=False)), _answer(_line(False, sure=False))

    alone = await grade_reply(_caller(*unsure()), _evidence(), "", RUBRIC[:1])
    assert alone.checks[0].unjudged is WhyUnjudged.JUDGE_UNSURE

    decider = _sceptic_caller(_answer({"pass": False, "reason": "firm figures"}))
    decided = await grade_reply(_caller(*unsure()), _evidence(), "", RUBRIC[:1], decider=decider)
    assert decided.checks[0].outcome is Outcome.FAILED and decided.lines[0]["tie_break"]
    assert decider.seen[0][0] == TIE_BREAK_INSTRUCTIONS and decided.score == 0.0

    sure = _sceptic_caller()
    settled = await grade_reply(
        _caller(_answer(_line(True))), _evidence(), "", RUBRIC[:1], decider=sure
    )
    assert not sure.seen and settled.checks[0].outcome is Outcome.PASSED

    down = await grade_reply(
        _caller(*unsure()), _evidence(), "", RUBRIC[:1], decider=_caller("nope", "nope")
    )
    assert down.checks[0].unjudged is WhyUnjudged.JUDGE_UNSURE, "no tie-break leaves it as it was"


@pytest.mark.parametrize("answer", ["I'd rather not", json.dumps({"lines": []})])
async def test_a_grader_that_does_not_read_leaves_it_unjudged(answer) -> None:
    graded = await grade_reply(_caller(answer, answer), _evidence(), "ref", RUBRIC)
    assert graded.error and graded.score is None
    assert {c.outcome for c in graded.checks} == {Outcome.UNJUDGED}
    assert {c.unjudged for c in graded.checks} == {WhyUnjudged.JUDGE_FAILED}


async def test_n_of_the_lines_decide_with_the_lines_as_guides() -> None:
    caller = _caller(_answer(_line(True), _line(False), _line(True)))
    graded = await grade_reply(caller, _evidence(), "", RUBRIC, need=2)
    whole, *lines = graded.checks
    assert (whole.id, whole.role, whole.outcome) == ("rubric", Role.MUST, Outcome.PASSED)
    assert {c.role for c in lines} == {Role.GUIDE}
    assert judge_try(graded.checks) is Outcome.PASSED


async def test_a_reference_decides_only_when_it_is_all_there_is() -> None:
    caller = _caller(_answer(_line(False)))
    alone = await grade_reply(caller, _evidence(), "ref", [], mark=0.7)
    assert [(c.id, c.role, c.outcome) for c in alone.checks] == [
        ("reference", Role.MUST, Outcome.FAILED)
    ]
    assert alone.score == 0.0 and not alone.lines
    assert f"1. {REFERENCE_LINE}" in caller.seen[0]
    beside = await grade_reply(
        _caller(_answer(_line(True), _line(True), _line(True), _line(False))),
        _evidence(),
        "ref",
        RUBRIC,
        mark=0.7,
    )
    assert beside.checks[-1].role is Role.GUIDE
    assert judge_try(beside.checks) is Outcome.PASSED
    assert beside.score == 0.75 and len(beside.lines) == len(RUBRIC)


async def test_memory_is_measured_committed_then_written() -> None:
    def said(holds: bool) -> str:
        return json.dumps({"holds": holds})

    want = {"want": "Hawaii"}
    only_written = MemoryRecord(
        **want, committed=Clipped(text="old notes"), written=Clipped(text="went to Hawaii")
    )
    caller = _caller(said(False), said(True))
    assert await grade_memory(caller, only_written) == {
        "committed": False,
        "written": True,
        "reached": None,
        "compacted": None,
    }
    assert "old notes" in caller.seen[0] and "went to Hawaii" in caller.seen[1]

    committed = MemoryRecord(
        **want, committed=Clipped(text="Hawaii"), written=Clipped(text="Hawaii")
    )
    caller = _caller(said(True))
    assert (await grade_memory(caller, committed))["written"] is True
    assert len(caller.seen) == 1

    empty = MemoryRecord(**want)
    assert (await grade_memory(_caller(), empty))["committed"] is False
    unread = await grade_memory(_caller("nonsense", "nonsense"), committed)
    assert (unread["committed"], unread["written"]) == (None, None)
    no_read = await grade_memory(_caller(), MemoryRecord(**want, supported=False, compacted=True))
    assert (no_read["committed"], no_read["compacted"]) == (None, True)

    def call(text: str, elided: int = 0, helper: str | None = None) -> ModelCallRecord:
        sent = Clipped(text=text, elided=elided)
        return ModelCallRecord(
            n=1, span="s", t=0.0, model="m", sent=sent, said=Clipped(), helper=helper
        )

    calls = [call("early"), call("the last call"), call("a helper's", helper="h")]
    caller = _caller(said(True))
    assert (await grade_memory(caller, empty, calls))["reached"] is True
    assert "the last call" in caller.seen[0] and "helper" not in caller.seen[0]
    cut = await grade_memory(_caller(said(False)), empty, [call("part", elided=5)])
    assert cut["reached"] is None, "with part of it missing, a no can't be told"
    assert (await grade_memory(_caller(said(False)), empty, [call("all")]))["reached"] is False
    assert await grade_memory(_caller(), None) is None


async def test_what_was_cut_is_on_the_check() -> None:
    evidence = _evidence(tools=(_tool(1, "x" * 90_000),))
    caller = _caller(_answer(_line(True), _line(True), _line(True)))
    graded = await grade_reply(caller, evidence, "", RUBRIC)
    assert graded.checks[0].cut == graded.cut > 60_000


def _tool(n: int, result: str = "ok", helper: str | None = None) -> ToolRecord:
    return ToolRecord(
        id=f"t{n}",
        name=f"tool_{n}",
        args=Clipped(text='{"a": 1}'),
        result=Clipped(text=result),
        start=float(n),
        helper=helper,
    )


async def test_the_grader_sees_what_the_app_did() -> None:
    evidence = _evidence(tools=(_tool(1, "saved decks/churn.deck.html", "h1"),))
    caller = _caller(_answer(_line(True), _line(False), _line(False)))
    await grade_reply(
        caller, evidence, "ref", RUBRIC, check_lines=["- check state:file: matched (found it)"]
    )
    text = caller.seen[0]
    assert "[Data: what the assistant did." in text
    assert "helper called tool_1" in text and "-> saved decks/churn.deck.html" in text
    assert "check state:file: matched" in text


def test_answers_are_matched_by_number_or_text_not_by_place() -> None:
    answer = {
        "lines": [
            {"id": 2, "line": "ignored", "pass": False, "reason": "second"},
            {"line": " links  BOTH decks ", "pass": True, "reason": "first"},
            {"id": "3", "pass": True, "reason": "third"},
            {"id": 9, "pass": True, "reason": "a line nobody asked about"},
        ],
        "score": 0.5,
    }
    graded = parse_grade(json.dumps(answer), RUBRIC)
    assert [line["reason"] for line in graded.lines] == ["first", "second", "third"]
    assert [line["line"] for line in graded.lines] == RUBRIC


def test_an_answer_short_of_a_line_does_not_read() -> None:
    answer = {"lines": [{"id": 1, "pass": True}, {"id": 2, "pass": True}], "score": 0.5}
    with pytest.raises(ValueError, match="no answer for 'Short'"):
        parse_grade(json.dumps(answer), RUBRIC)


def test_a_long_tool_result_keeps_its_end_and_says_what_went() -> None:
    long = "start " + "x" * 90_000 + " the end"
    found = render(_evidence(tools=(_tool(1, long),)), "ref", [(1, "A line")])
    assert "the end" in found.text and "chars cut]" in found.text
    assert found.cut > 60_000 and len(found.text) < BUDGET + 2_000


def test_what_is_never_cut() -> None:
    tools = tuple(_tool(n, "r" * 2_000) for n in range(400))
    checks = [f"- check state:{n}: matched (held)" for n in range(50)]
    found = render(_evidence(tools=tools), "ref " * 200, [(1, "line " * 500)], checks)
    assert all(line in found.text for line in checks)
    assert ("ref " * 200).strip() in found.text and ("line " * 500).strip() in found.text
    assert "tool_0(" in found.text and "tool_399(" in found.text
    assert "items cut" in found.text
    assert found.cut > 0 and len(found.text) < BUDGET + 3_000


def test_it_says_when_it_cannot_know_what_the_app_did() -> None:
    for status, words in (
        (TraceStatus.NO_SOURCE, "no traces"),
        (TraceStatus.EMPTY, "no traces arrived"),
        (TraceStatus.PARTIAL, "incomplete"),
    ):
        assert words in render(_evidence(trace=status), "", []).text


def test_state_records_read_back() -> None:
    state = StateRecord(index=0, key="memory_lacks", value="x", at=1.0, ok=True, observed=False)
    assert TryEvidence.model_validate_json(_evidence(states=(state,)).model_dump_json()).states == (
        state,
    )


def test_reply_pass_is_checked() -> None:
    base = {"name": "n", "turns": [{"message": "hi"}]}
    case = parse_case({**base, "reply": {"rubric": RUBRIC, "pass": 2}}, "c")
    assert case.reply_pass == 2
    with pytest.raises(CaseError, match=r"reply\.pass"):
        parse_case({**base, "reply": {"rubric": RUBRIC, "pass": 4}}, "c")
    with pytest.raises(CaseError, match=r"reply\.contains"):
        parse_case({**base, "reply": {"contains": "one word"}}, "c")
    with pytest.raises(CaseError, match=r"results\[0\]\.want"):
        parse_case({**base, "tools": {"results": [{"name": "t"}]}}, "c")


def test_code_decides_the_reply_words_and_what_tools_returned() -> None:
    def outcomes(reply: dict, results: list, evidence: TryEvidence) -> dict:
        case = parse_case(
            {
                "name": "n",
                "turns": [{"message": "hi"}],
                "reply": reply,
                "tools": {"results": results},
            },
            "c",
        )
        found = code_checks(case, evidence, [], 1.0, {"tools": 0.8}).checks
        return {c.id: c.outcome for c in found}, found, case

    words = {"contains": ["4,828.13", "NORTH  total"], "lacks": ["pandas"]}
    held, found, case = outcomes(words, [], _evidence("The **4,828.13** north total."))
    assert set(held.values()) == {Outcome.PASSED} and len(held) == 3
    assert basis_of(found) is Basis.CHECKED and steps_bind(case) is False
    # Lacking a word alone shows nothing happened, and an empty reply passes neither way.
    lacks, found, _ = outcomes({"lacks": ["pandas"]}, [], _evidence("Done."))
    assert lacks == {"reply:lacks:0": Outcome.PASSED} and basis_of(found) is Basis.NONE
    assert outcomes({"lacks": ["x"]}, [], _evidence(""))[0]["reply:lacks:0"] is Outcome.FAILED

    def tool(result: str, id_: str = "s1") -> ToolRecord:
        return ToolRecord(
            id=id_, name="deck", args=Clipped(), result=Clipped(text=result), start=0.0
        )

    want = [{"name": "deck", "path": "deck.slides.0", "want": 2}]
    id_ = "result:0:deck"
    good = tool('{"deck": {"slides": [2]}}', "s2")
    complete = TraceStatus.COMPLETE
    # Any call will do; none that did, with every call seen, fails; part of the traces can't say.
    seen = _evidence(tools=(tool('{"deck": {"slides": [9]}}'), good), trace=complete)
    assert outcomes({}, want, seen)[0][id_] is Outcome.PASSED
    assert basis_of(outcomes({}, want, seen)[1]) is Basis.CHECKED
    wrong = _evidence(tools=(tool('{"deck": {"slides": [9]}}'),), trace=complete)
    assert outcomes({}, want, wrong)[0][id_] is Outcome.FAILED
    assert outcomes({}, want, _evidence(trace=complete))[0][id_] is Outcome.FAILED
    assert outcomes({}, want, _evidence(trace=TraceStatus.PARTIAL))[0][id_] is Outcome.UNJUDGED
    prose = _evidence(tools=(tool("not json"),), trace=complete)
    assert outcomes({}, want, prose)[0][id_] is Outcome.UNJUDGED
    text = [{"name": "deck", "want": {"contains": "NOT"}}]
    assert outcomes({}, text, prose)[0][id_] is Outcome.PASSED


def test_one_line_asked_takes_its_one_answer_whatever_it_numbers_it() -> None:
    answer = json.dumps({"lines": [{"id": 1, **_line(True)}], "score": 0.5})
    assert parse_grade(answer, ["The second line"], [2]).lines[0]["pass"] is True


def test_a_line_reads_as_the_grading_that_decided_it() -> None:
    first = {"pass": True, "sure": False, "reason": "miscounted"}
    second = {"pass": True, "sure": True, "reason": "two sentences"}
    assert line_reason({**first, "second": second}) == "two sentences"
    assert line_reason({**first, "sure": True, "second": second}) == "miscounted"
    assert line_reason({**first, "second": {**second, "sure": False}}) == "two sentences"
    assert line_reason(first) == "miscounted"


def test_the_grader_is_told_where_a_new_conversation_starts() -> None:
    def said(message: str, reply: str) -> TurnRecord:
        return TurnRecord(
            kind="message",
            message=Clipped(text=message),
            reply=Clipped(text=reply),
            started=0.0,
            ended=1.0,
        )

    turns = (said("Plan a trip.", "Step one done."), said("Where were we?", "No idea."))
    made = TryEvidence(case="c", n=1, t0=0.0, seconds=1.0, turns=turns, breaks=(1,))
    text = render(made, "", []).text
    assert text.index("Plan a trip") < text.index(NEW_CONVERSATION) < text.index("Where were we")
    plain = made.model_copy(update={"breaks": ()})
    assert NEW_CONVERSATION not in render(plain, "", []).text, "control: one conversation"
    assert "breaks" in TryEvidence.model_fields, "and evidence stored before it still loads"
    entry = {
        "turns": [{"message": "Plan a trip."}, {"break": "conversation"}, {"message": "Where?"}],
        "name": "n",
    }
    attempt = {
        "n": 1,
        "tools": 1,
        "reply": 1,
        "seconds": 1,
        "rubric": [],
        "steps": [],
        "timeline": [],
        "judgements": [],
    }
    assert NEW_CONVERSATION in try_input(entry, attempt, [])
