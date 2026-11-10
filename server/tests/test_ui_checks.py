"""UI checks: what a test's page may show and hide (``page.shows``, ``page.hides``, read on the page
by code) and how it looks (``page.looks``, a yes/no question a vision model answers about a turn's
screenshot). Their validation, their readings kept with a try, how code judges them from those
readings, how a vision model's answer reads, and what a target with no page map does with them."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import ModelConfig, load
from evalution.core.case import UiElement
from evalution.core.evidence import (
    Clipped,
    ElementReading,
    PageRecord,
    PageTurn,
    TryEvidence,
    TurnRecord,
)
from evalution.core.judgement import CheckKind, Decider, Outcome, Role, WhyUnjudged
from evalution.core.rules import judge_try, judgement
from evalution.judging import vision
from evalution.judging.checks import code_checks, page_checks
from evalution.llm import LLM, anthropic_body, openai_body
from evalution.review.testplan import explain
from evalution.runs.page.target import nearest, snapshot_candidates
from evalution.runs.runner import Runs
from evalution.storage import Database
from evalution.suite.cases import UI_NEEDS_PAGE, CaseError, parse_case, ui_refusal
from evalution.suite.store import import_sets
from evalution.targets import Targets

THUMBS = {"role": "button", "name": "Thumbs up", "within": "reply"}
PNG = b"\x89PNG\r\n\x1a\nnot really a picture"


def _case(page: dict[str, Any], turns: int = 2) -> Any:
    return parse_case(
        {
            "name": "Rates a reply",
            "turns": [{"message": f"Message {i}"} for i in range(1, turns + 1)],
            "page": page,
        },
        "set/case",
        "case",
    )


# Validation


def test_the_thumbs_up_case_reads() -> None:
    case = parse_case(
        {
            "name": "Shows a thumbs-up under the reply",
            "turns": [{"message": "Make an HTML page about Acme"}],
            "tools": {"expected": [{"name": "spawn_agent"}]},
            "page": {"shows": [THUMBS]},
        },
        "set/case",
        "case",
    )
    assert case.shows == (UiElement(role="button", name="Thumbs up", within="reply"),)
    assert case.ui_checks and not case.hides and not case.looks


def test_every_field_of_an_element_and_a_look_reads() -> None:
    case = _case(
        {
            "shows": [
                {"test_id": "rate", "text": "Rate", "within": "page", "count": 2, "turn": 2},
                {"role": "img", "count": {"min": 1, "max": 3}},
            ],
            "hides": [{"text": "Something went wrong"}],
            "looks": [{"question": "Is the chart drawn?", "turn": 1}],
            "decides": True,
        }
    )
    first, second = case.shows
    assert (first.test_id, first.text, first.within, first.least, first.most, first.turn) == (
        "rate",
        "Rate",
        "page",
        2,
        2,
        2,
    )
    assert (second.least, second.most, second.turn) == (1, 3, None)
    assert case.hides[0].text == "Something went wrong"
    assert case.looks[0].question == "Is the chart drawn?" and case.looks[0].turn == 1


@pytest.mark.parametrize(
    ("page", "said"),
    [
        ({"shows": [{"within": "reply"}]}, "page.shows[0]: name the element by role"),
        ({"shows": [{"role": "thumbsup"}]}, "page.shows[0].role: 'thumbsup' isn't an ARIA role"),
        ({"shows": [{"name": "Send"}]}, "page.shows[0].name: a name needs its role"),
        ({"shows": [{"role": "button", "turn": 3}]}, "page.shows[0].turn: the test has 2"),
        ({"hides": [{"text": "x", "turn": 0}]}, "page.hides[0].turn: must be a message turn"),
        ({"hides": [{"text": "x", "count": 1}]}, "page.hides[0].count: an element the page"),
        ({"shows": [{"text": "x", "within": "chat"}]}, "page.shows[0].within: must be one of"),
        ({"shows": [{"text": "x", "count": 0}]}, "page.shows[0].count: must be at least 1"),
        ({"shows": [{"text": "x", "count": {"min": 3, "max": 2}}]}, "max must be at least min"),
        ({"shows": [{"text": "x", "css": ".up"}]}, "page.shows[0].css: unknown field"),
        ({"shows": [{"text": ""}]}, "page.shows[0].text: must be a non-empty string"),
        ({"looks": [{"question": "  "}]}, "page.looks[0].question: must be a yes/no question"),
        ({"looks": [{"question": "Is it? " * 60}]}, "at most 300 characters"),
        ({"looks": [{"question": "Is it red?", "turn": 9}]}, "page.looks[0].turn: the test has"),
        ({"looks": ["Is it red?"]}, "page.looks[0]: must be an object"),
    ],
)
def test_a_bad_ui_check_is_refused_naming_its_field(page: dict[str, Any], said: str) -> None:
    with pytest.raises(CaseError, match=None) as caught:
        _case(page)
    assert said in str(caught.value)


def test_a_ui_check_is_refused_only_on_a_test_that_says_it_plays_through_the_api() -> None:
    case = _case({"shows": [THUMBS]})
    # A target with no page map yet keeps it: its UI checks wait for the page (no_page_map).
    assert ui_refusal(case) is None
    said = parse_case(
        {
            "name": "Rates",
            "turns": [{"message": "hi"}],
            "page": {"shows": [THUMBS]},
            "plays": "api",
        },
        "set/case",
        "case",
    )
    refused = ui_refusal(said)
    assert refused is not None and refused.startswith(UI_NEEDS_PAGE) and "plays: api" in refused
    plain = parse_case({"name": "P", "turns": [{"message": "hi"}], "plays": "api"}, "s/c", "c")
    assert ui_refusal(plain) is None
    with pytest.raises(CaseError, match="plays: must be api"):
        parse_case({"name": "P", "turns": [{"message": "hi"}], "plays": "page"}, "s/c", "c")


@pytest.fixture
def client(tmp_path):
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    main = tmp_path / "sets" / "bot" / "main"
    main.mkdir(parents=True)
    (main / "set.json").write_text(json.dumps({"name": "Main"}))
    (main / "greets.case.json").write_text(
        json.dumps({"name": "Greets", "turns": [{"message": "hi"}]})
    )
    import_sets(Database.open(load(tmp_path / "evalution.yaml").data_dir).docs(), tmp_path / "sets")
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as made:
        yield made


def test_a_ui_check_for_a_target_with_no_page_map_is_saved(client) -> None:
    """Draft first, learn after: the app has no page map, and the test is saved all the same."""
    test = {"name": "Rates", "turns": [{"message": "hi"}], "page": {"shows": [THUMBS]}}
    made = client.post("/v1/evals/sets/main/cases", json=test)
    assert made.status_code == 200, made.text
    url, text = "/v1/evals/sets/main/cases/greets/file", json.dumps(test)
    asked = client.put(url, content=text, headers={"content-type": "application/json"})
    assert asked.status_code == 409, "a change to what it checks is shown first, not refused"
    edited = client.put(
        url,
        content=text,
        headers={"content-type": "application/json", "x-reviewed": asked.json()["token"]},
    )
    assert edited.status_code == 200, edited.text
    shown = client.get("/v1/evals/sets/main/cases/greets").json()
    assert shown["page"] == {"shows": [THUMBS], "hides": [], "looks": []}


def test_a_ui_check_on_a_test_that_plays_through_the_api_says_why_not(client) -> None:
    test = {
        "name": "Rates",
        "turns": [{"message": "hi"}],
        "page": {"shows": [THUMBS]},
        "plays": "api",
    }
    made = client.post("/v1/evals/sets/main/cases", json=test)
    assert made.status_code == 400
    assert UI_NEEDS_PAGE in made.json()["detail"]
    edited = client.put(
        "/v1/evals/sets/main/cases/greets/file",
        content=json.dumps(test),
        headers={"content-type": "application/json"},
    )
    assert edited.status_code == 400 and UI_NEEDS_PAGE in edited.json()["detail"]
    plain = {"name": "Plain", "turns": [{"message": "hi"}]}
    assert client.post("/v1/evals/sets/main/cases", json=plain).status_code == 200


# Element checks, judged by code from the readings a try kept


def _key(**locator: str) -> str:
    return UiElement(**locator).key  # type: ignore[arg-type]


THUMBS_KEY = _key(role="button", name="Thumbs up")


def _turn(reply: str = "Here is your page", error: str | None = None) -> TurnRecord:
    return TurnRecord(
        kind="message",
        message=Clipped(text="Make an HTML page about Acme"),
        reply=Clipped(text=reply),
        started=0.0,
        ended=1.0,
        error=error,
        shown=Clipped(text=reply),
    )


def _try(
    *readings: tuple[ElementReading, ...],
    errors: tuple[str | None, ...] = (),
    filler_first: bool = False,
) -> TryEvidence:
    """A try of one message turn per *readings*, each turn's page holding those readings and a
    screenshot ``turn-<n>.png``."""
    turns = [_turn(error=errors[i] if i < len(errors) else None) for i in range(len(readings))]
    pages = [
        PageTurn(shown_at=1.0, shot=f"turn-{i}.png", elements=found)
        for i, found in enumerate(readings, 1)
    ]
    if filler_first:
        filler = TurnRecord(
            kind="filler",
            message=Clipped(text="by the way"),
            reply=Clipped(text="ok"),
            started=0.0,
            ended=0.5,
        )
        turns.insert(0, filler)
        pages.insert(0, PageTurn(shown_at=0.5, shot="turn-0.png"))
    return TryEvidence(
        case="case",
        n=1,
        t0=0.0,
        seconds=2.0,
        turns=tuple(turns),
        page=PageRecord(turns=tuple(pages), files=tuple(p.shot for p in pages if p.shot)),
    )


def _checks(page: dict[str, Any], evidence: TryEvidence, turns: int = 2) -> list[Any]:
    found = page_checks(_case(page, turns), evidence)
    return [c for c in found if c.kind is CheckKind.PAGE_ELEMENT]


def test_an_element_present_under_every_reply_passes() -> None:
    there = (ElementReading(key=THUMBS_KEY, count=1),)
    (check,) = _checks({"shows": [THUMBS]}, _try(there, there))
    assert (check.id, check.outcome, check.decided_by, check.role) == (
        "element:shows:0",
        Outcome.PASSED,
        Decider.CODE,
        Role.GUIDE,
    )
    assert check.why == "found button 'Thumbs up' under the reply of each of turns 1, 2"
    refs = [r.ref for r in check.evidence]
    assert refs == ["page:1", "shot:turn-1.png", "page:2", "shot:turn-2.png"]


def test_an_element_missing_under_one_reply_fails_naming_the_nearest() -> None:
    there = (ElementReading(key=THUMBS_KEY, count=1),)
    gone = (ElementReading(key=THUMBS_KEY, count=0, nearest=("button 'Copy'", "button 'Share'")),)
    (check,) = _checks({"shows": [THUMBS], "decides": True}, _try(there, gone))
    assert check.outcome is Outcome.FAILED and check.role is Role.MUST
    assert check.why == (
        "no button named 'Thumbs up' under turn 2's reply; nearest: button 'Copy', button 'Share'"
    )
    assert [r.ref for r in check.evidence] == ["page:2", "shot:turn-2.png"]


def test_found_says_how_many_on_one_turn() -> None:
    there = (ElementReading(key=THUMBS_KEY, count=1),)
    (check,) = _checks({"shows": [{**THUMBS, "turn": 2}]}, _try((), there))
    assert check.outcome is Outcome.PASSED
    assert check.why == "found 1 button 'Thumbs up' under turn 2's reply"


def test_an_element_that_must_not_show_fails_when_it_does() -> None:
    key = _key(text="Something went wrong", within="page")
    hidden = {"text": "Something went wrong", "within": "page"}
    (held,) = _checks({"hides": [hidden]}, _try((ElementReading(key=key, count=0),)), 1)
    assert held.id == "element:hides:0" and held.outcome is Outcome.PASSED
    assert held.why == "no text 'Something went wrong' on the page after turn 1"
    (broke,) = _checks({"hides": [hidden]}, _try((ElementReading(key=key, count=2),)), 1)
    assert broke.outcome is Outcome.FAILED
    assert broke.why == (
        "found 2 text 'Something went wrong' on the page after turn 1, which must not show"
    )


@pytest.mark.parametrize(
    ("count", "seen", "outcome", "said"),
    [
        (2, 2, Outcome.PASSED, "found 2"),
        (2, 3, Outcome.FAILED, "wanted exactly 2"),
        ({"min": 2}, 1, Outcome.FAILED, "wanted at least 2"),
        ({"min": 1, "max": 2}, 3, Outcome.FAILED, "wanted 1 to 2"),
        ({"max": 2}, 2, Outcome.PASSED, "found 2"),
    ],
)
def test_a_count_holds_the_element_to_how_many(count, seen, outcome, said) -> None:
    key = _key(role="img", within="page")
    element = {"role": "img", "within": "page", "count": count}
    (check,) = _checks({"shows": [element]}, _try((ElementReading(key=key, count=seen),)), 1)
    assert check.outcome is outcome and said in check.why


def test_turn_picks_the_tests_message_turn_past_filler() -> None:
    """``turn`` counts the test's message turns: a filler message before it doesn't move it."""
    there = (ElementReading(key=THUMBS_KEY, count=1),)
    gone = (ElementReading(key=THUMBS_KEY, count=0),)
    evidence = _try(gone, there, filler_first=True)
    (second,) = _checks({"shows": [{**THUMBS, "turn": 2}]}, evidence)
    assert second.outcome is Outcome.PASSED
    assert [r.ref for r in second.evidence] == ["page:3", "shot:turn-2.png"]
    (first,) = _checks({"shows": [{**THUMBS, "turn": 1}]}, evidence)
    assert first.outcome is Outcome.FAILED and "turn 1's reply" in first.why


def test_within_reads_its_own_reading() -> None:
    """The same locator under the reply and on the page are two readings."""
    on_page = _key(role="button", name="Thumbs up", within="page")
    readings = (ElementReading(key=THUMBS_KEY, count=0), ElementReading(key=on_page, count=3))
    page = {"shows": [THUMBS, {**THUMBS, "within": "page"}]}
    reply, whole = _checks(page, _try(readings), 1)
    assert reply.outcome is Outcome.FAILED and whole.outcome is Outcome.PASSED
    assert whole.why == "found 3 button 'Thumbs up' on the page after turn 1"


def test_a_reply_that_never_showed_has_nothing_to_look_under() -> None:
    nowhere = (ElementReading(key=THUMBS_KEY, count=0, scope=False),)
    (shows,) = _checks({"shows": [THUMBS]}, _try(nowhere), 1)
    assert shows.outcome is Outcome.FAILED
    assert shows.why.startswith(
        "no button named 'Thumbs up' under turn 1's reply: turn 1's reply never showed"
    )
    (hides,) = _checks({"hides": [THUMBS]}, _try(nowhere), 1)
    assert (hides.outcome, hides.unjudged) == (Outcome.UNJUDGED, WhyUnjudged.NO_REPLY)


def test_what_cant_be_said_is_not_judged() -> None:
    broken = _try((ElementReading(key=THUMBS_KEY, error="boom"),))
    (unread,) = _checks({"shows": [THUMBS]}, broken, 1)
    assert (unread.outcome, unread.unjudged) == (Outcome.UNJUDGED, WhyUnjudged.NOT_SUPPORTED)
    assert "boom" in unread.why
    (never,) = _checks({"shows": [THUMBS]}, _try(()), 1)
    assert (never.outcome, never.unjudged) == (Outcome.UNJUDGED, WhyUnjudged.NOT_SUPPORTED)
    assert "wasn't looked for after turn 1" in never.why
    (errored,) = _checks({"shows": [{**THUMBS, "turn": 1}]}, _try((), errors=("boom",)), 1)
    assert (errored.outcome, errored.unjudged) == (Outcome.UNJUDGED, WhyUnjudged.NO_REPLY)
    (none,) = _checks({"shows": [THUMBS]}, _try((), errors=("boom",)), 1)
    assert none.why == "no turn had a reply to look under"
    (short,) = _checks({"shows": [{**THUMBS, "turn": 2}]}, _try(()))
    assert short.why == "the try ended before turn 2"


def test_a_kept_try_is_judged_again_the_same_from_its_readings() -> None:
    """The readings are stored with the try: judging the stored evidence again, with no browser,
    comes out the same."""
    gone = (ElementReading(key=THUMBS_KEY, count=0, nearest=("button 'Copy'",)),)
    evidence = _try((ElementReading(key=THUMBS_KEY, count=1),), gone)
    stored = json.loads(evidence.capped().model_dump_json())
    again = TryEvidence.model_validate(stored)
    page = {"shows": [THUMBS], "hides": [{"role": "button", "name": "Thumbs down"}]}
    assert _checks(page, again) == _checks(page, evidence)
    assert again.page is not None and again.page.turns[1].elements == gone


def test_old_evidence_reads_with_no_readings() -> None:
    stored = json.loads(_try(()).model_dump_json())
    for turn in stored["page"]["turns"]:
        del turn["elements"]
    found = TryEvidence.model_validate(stored).page
    assert found is not None and found.turns[0].elements == ()


def test_an_element_check_guides_unless_the_page_decides() -> None:
    gone = (ElementReading(key=THUMBS_KEY, count=0),)
    case = _case({"shows": [THUMBS]}, 1)
    found = code_checks(case, _try(gone), [], 1.0, {"tools": 0.8, "reply": 0.7})
    check = next(c for c in found.checks if c.kind is CheckKind.PAGE_ELEMENT)
    assert check.role is Role.GUIDE and check.outcome is Outcome.FAILED


# The nearest elements, from an accessibility snapshot

SNAPSHOT = """\
- log "Messages":
  - article:
    - paragraph: Echo: hi
    - button "Copy"
    - button "Thumbs down"
- button "Settings"
- heading "Results" [level=2]
"""


def test_the_nearest_elements_come_from_the_snapshot_most_alike_first() -> None:
    found = snapshot_candidates(SNAPSHOT)
    assert ("button", "Copy") in found and ("text", "Echo: hi") in found
    assert ("heading", "Results") in found
    said = nearest(UiElement(role="button", name="Thumbs up"), found)
    assert said[0] == "button 'Thumbs down'" and len(said) == 3
    assert all(s.startswith("button") for s in said), "its own role first"
    by_id = nearest(UiElement(test_id="thumbs-up"), found, ["thumbs-down", "copy"])
    assert by_id[0] == "test id 'thumbs-down'"


# A target with no page map: the UI checks wait, the rest still runs


def test_with_no_page_the_ui_checks_are_not_judged_and_the_rest_decides() -> None:
    case = parse_case(
        {
            "name": "Rates",
            "turns": [{"message": "hi"}],
            "reply": {"contains": ["hello"]},
            "page": {
                "shows": [THUMBS],
                "hides": [{"text": "Oops"}],
                "looks": [{"question": "Is there a thumbs-up?"}],
                "decides": True,
            },
        },
        "set/case",
        "case",
    )
    evidence = TryEvidence(case="case", n=1, t0=0.0, seconds=1.0, turns=(_turn("hello there"),))
    found = code_checks(case, evidence, [], 1.0, {"tools": 0.8, "reply": 0.7})
    ui = [c for c in found.checks if c.kind is CheckKind.PAGE_ELEMENT]
    assert [c.id for c in ui] == ["element:shows:0", "element:hides:0"]
    for check in ui:
        assert (check.outcome, check.unjudged, check.role) == (
            Outcome.UNJUDGED,
            WhyUnjudged.NO_PAGE_MAP,
            Role.GUIDE,
        )
        assert check.why == "the page hasn't been learned yet"
    assert judge_try(found.checks) is Outcome.PASSED, "the reply's words still decide"


async def test_with_no_page_a_look_is_not_judged_and_no_model_is_asked() -> None:
    case = _case({"looks": [{"question": "Is there a thumbs-up?"}]}, 1)
    evidence = TryEvidence(case="case", n=1, t0=0.0, seconds=1.0, turns=(_turn(),))

    async def looker(**_: Any) -> str:
        raise AssertionError("no model is asked")

    (check,) = await vision.look_checks(case, evidence, looker, "m", lambda _: PNG)
    assert (check.id, check.kind, check.unjudged, check.role) == (
        "look:0",
        CheckKind.PAGE_LOOK,
        WhyUnjudged.NO_PAGE_MAP,
        Role.GUIDE,
    )


# Questions about a screenshot, put to a fake model


@pytest.fixture(autouse=True)
def _forget_answers():
    vision.forget()
    yield
    vision.forget()


def _model(kind: str, answer: Any, seen: list[dict[str, Any]]) -> LLM:
    """An LLM on a fake transport: each call is kept in *seen*; *answer* is the text the model
    says, or an ``httpx.Response`` it answers with."""

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if isinstance(answer, httpx.Response):
            return answer
        if kind == "anthropic":
            return httpx.Response(200, json={"content": [{"type": "text", "text": answer}]})
        choice = {"message": {"content": answer}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice]})

    config = ModelConfig(
        kind=kind, base_url="http://model", api_key_env=None, models={"strong": "m"}
    )
    return LLM(config, transport=httpx.MockTransport(handle))


async def _look(llm: LLM, page: dict[str, Any] | None = None) -> Any:
    case = _case(page or {"looks": [{"question": "Is there a thumbs-up under the reply?"}]}, 1)
    looker, model = llm.looker("judge"), llm.model_id("judge")
    (check,) = await vision.look_checks(case, _try(()), looker, model, lambda _: PNG)
    return check


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
async def test_yes_passes_with_the_models_reason_and_the_screenshot(kind) -> None:
    seen: list[dict[str, Any]] = []
    said = '{"answer": "yes", "reason": "A thumbs-up button sits under the reply."}'
    check = await _look(_model(kind, said, seen))
    assert (check.id, check.kind, check.decided_by, check.outcome) == (
        "look:0",
        CheckKind.PAGE_LOOK,
        Decider.MODEL,
        Outcome.PASSED,
    )
    assert check.why == "A thumbs-up button sits under the reply."
    assert [r.ref for r in check.evidence] == ["page:1", "shot:turn-1.png"]
    (body,) = seen
    encoded = base64.b64encode(PNG).decode()
    content = body["messages"][-1]["content"]
    if kind == "anthropic":
        assert content[0] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": encoded},
        }
    else:
        assert content[0] == {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encoded}"},
        }
    assert content[1]["type"] == "text" and "thumbs-up under the reply" in content[1]["text"]


async def test_no_fails_and_the_page_deciding_makes_it_a_must() -> None:
    llm = _model("anthropic", '{"answer": "no", "reason": "Only a Copy button shows."}', [])
    check = await _look(llm, {"looks": [{"question": "Is there a thumbs-up?"}], "decides": True})
    assert (check.outcome, check.role, check.why) == (
        Outcome.FAILED,
        Role.MUST,
        "Only a Copy button shows.",
    )


async def test_unsure_is_never_a_pass_or_a_fail() -> None:
    said = '{"answer": "unsure", "reason": "The bottom of the reply is cut off."}'
    check = await _look(_model("openai", said, []))
    assert (check.outcome, check.unjudged) == (Outcome.UNJUDGED, WhyUnjudged.JUDGE_UNSURE)
    assert check.why == "The bottom of the reply is cut off."


async def test_an_answer_that_doesnt_read_is_asked_again_then_not_judged() -> None:
    seen: list[dict[str, Any]] = []
    check = await _look(_model("anthropic", '{"answer": "probably"}', seen))
    assert (check.outcome, check.unjudged) == (Outcome.UNJUDGED, WhyUnjudged.JUDGE_FAILED)
    assert check.why.startswith("the grader's answer didn't read")
    assert len(seen) == 2, "asked once more"


async def test_a_model_that_cant_read_images_says_so() -> None:
    refused = httpx.Response(
        400, json={"error": {"message": "This model does not support image input"}}
    )
    check = await _look(_model("openai", refused, []))
    assert (check.outcome, check.unjudged, check.why) == (
        Outcome.UNJUDGED,
        WhyUnjudged.NO_VISION,
        "the grader model can't read images",
    )


async def test_another_refusal_is_the_graders_failure() -> None:
    check = await _look(_model("anthropic", httpx.Response(401, json={"error": "bad key"}), []))
    assert (check.unjudged, check.why) == (
        WhyUnjudged.JUDGE_FAILED,
        "the grader's model call failed",
    )


async def test_an_answer_is_kept_by_screenshot_question_and_model() -> None:
    seen: list[dict[str, Any]] = []
    llm = _model("anthropic", '{"answer": "yes", "reason": "It shows."}', seen)
    await _look(llm)
    await _look(llm)
    assert len(seen) == 1, "the same screenshot, question and model ask once"
    await _look(llm, {"looks": [{"question": "Is the page blue?"}]})
    assert len(seen) == 2


async def test_no_screenshot_kept_is_not_judged() -> None:
    llm = _model("anthropic", '{"answer": "yes", "reason": "x"}', [])
    case = _case({"looks": [{"question": "Is it?"}]}, 1)
    shotless = _try(()).model_copy(update={"page": PageRecord(turns=(PageTurn(shown_at=1.0),))})
    (check,) = await vision.look_checks(case, shotless, llm.looker("judge"), "m", lambda _: PNG)
    assert (check.unjudged, check.why) == (
        WhyUnjudged.NO_SCREENSHOT,
        "no screenshot of turn 1 was kept to look at",
    )
    (gone,) = await vision.look_checks(case, _try(()), llm.looker("judge"), "m", lambda _: None)
    assert gone.unjudged is WhyUnjudged.NO_SCREENSHOT and "no longer kept" in gone.why


def test_text_only_calls_are_unchanged() -> None:
    """A message with plain text content reads as it always did, for both providers."""
    messages = [{"role": "user", "content": "hello"}]
    assert anthropic_body("m", "s", messages, [], 10)["messages"] == [
        {"role": "user", "content": "hello"}
    ]
    assert openai_body("m", "s", messages, [], 10)["messages"][1] == {
        "role": "user",
        "content": "hello",
    }


async def test_a_run_asks_its_grader_about_the_screenshot_it_kept(tmp_path) -> None:
    """The runner reads the turn's screenshot from the try's files and asks the set's grader."""
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"web_dir": "none"}))
    seen: list[dict[str, Any]] = []
    llm = _model("anthropic", '{"answer": "yes", "reason": "It is there."}', seen)
    runs = Runs(Targets(load(tmp_path / "evalution.yaml")), llm)
    assert runs.store.files is not None
    runs.store.files.put("r1", "case", 1, "turn-1.png", PNG)
    case = _case({"looks": [{"question": "Is there a thumbs-up?"}]}, 1)
    doc = {"run": "r1", "judge": {"key": "judge"}}
    (check,) = await runs._looks(doc, case, _try(()), 1)
    assert check.outcome is Outcome.PASSED and check.why == "It is there."
    image = seen[0]["messages"][0]["content"][0]["source"]["data"]
    assert base64.b64decode(image) == PNG
    assert vision.PROMPT_HASH and len(vision.PROMPT_HASH) == 12


# Where a try's UI check results are sent: the try's "how it was decided"


async def test_ui_results_are_sent_with_their_turn_why_reason_and_evidence() -> None:
    """``explain.code.checks`` holds the page's elements, ``explain.model.checks`` its looks, each
    with the test's message turn (filler not counted), why, the reason it wasn't judged and where
    it looked: what the page's step by step reads."""
    found = ElementReading(key=THUMBS_KEY, count=1)
    evidence = _try((found,), filler_first=True)
    case = _case({"shows": [THUMBS], "looks": [{"question": "Is there a thumbs-up?"}]}, 1)
    code = code_checks(case, evidence, [], 1.0, {"tools": 0.8, "reply": 0.7})

    refused = httpx.Response(
        400, json={"error": {"message": "This model does not support image input"}}
    )
    llm = _model("openai", refused, [])
    looker, model = llm.looker("judge"), llm.model_id("judge")
    looked = await vision.look_checks(case, evidence, looker, model, lambda _: PNG)
    judged = judgement([*code.checks, *looked], None, code.route)
    told = explain(judged, {}, 0, {}, evidence)
    (element,) = [r for r in told["code"]["checks"] if r["kind"] == "page_element"]
    assert element == {
        "id": "element:shows:0",
        "kind": "page_element",
        "text": "found 1 button 'Thumbs up' under turn 1's reply",
        "outcome": "passed",
        "guide": True,
        "why": "found 1 button 'Thumbs up' under turn 1's reply",
        "turn": 1,
        "unjudged": None,
        "evidence": [{"ref": "page:2"}, {"ref": "shot:turn-1.png"}],
    }
    (look,) = told["model"]["checks"]
    assert (look["id"], look["turn"], look["unjudged"], look["outcome"]) == (
        "look:0",
        1,
        "no_vision",
        "unjudged",
    )
    assert look["evidence"] == [{"ref": "page:2"}, {"ref": "shot:turn-1.png"}]
    # A rubric line's row stays as it was.
    assert all("turn" not in r for r in told["code"]["checks"] if r["kind"] != "page_element")


async def test_with_no_page_map_a_ui_check_reads_no_page_map_not_page_map() -> None:
    case = _case({"shows": [THUMBS], "looks": [{"question": "Is there a thumbs-up?"}]}, 1)
    evidence = TryEvidence(case="case", n=1, t0=0.0, seconds=1.0, turns=(_turn(),))
    code = code_checks(case, evidence, [], 1.0, {"tools": 0.8, "reply": 0.7})
    looked = await vision.look_checks(case, evidence, None, None, lambda _: None)
    ui = [
        c
        for c in [*code.checks, *looked]
        if c.kind in (CheckKind.PAGE_ELEMENT, CheckKind.PAGE_LOOK)
    ]
    assert [c.unjudged for c in ui] == [WhyUnjudged.NO_PAGE_MAP] * 2
    assert (
        WhyUnjudged.NO_PAGE_MAP.value == "no_page_map" and WhyUnjudged.PAGE_MAP.value == "page_map"
    )
