"""The stateful test library: every template loads and fills into a valid case, fit verdicts,
adapting with profile evidence, and the conversation break it relies on."""

import json
from pathlib import Path

import httpx
import pytest
import yaml

from evalution import adapters
from evalution.adapters.base import Capabilities, Isolation, MemoryRead, Session, Target, Turn
from evalution.config import load
from evalution.llm import LLM
from evalution.results.store import FileStore
from evalution.runs.play import play_try
from evalution.runs.runner import Runs
from evalution.suite import planning
from evalution.suite.benchmarks import happened, overview, report, sample, to_case, untested
from evalution.suite.cases import CaseError, parse_case
from evalution.suite.library import (
    KINDS,
    PLACEHOLDERS,
    LibraryError,
    adapt,
    dummy_values,
    fit,
    load_templates,
    parse_template,
)
from evalution.suite.planning import probe, read_plan
from evalution.targets import Targets
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionFiles
from evalution.writer.tools import AgentTurn

LIBRARY = Path(__file__).resolve().parents[2] / "library"
STATEFUL = {"reset", "state", "sections", "isolation", "conversation_break"}
CHECKS = frozenset({"checkpoint", "memory_contains", "memory_lacks", "file"})
PROFILE = """# What the app does
Plans trips for families [trace]
Keeps long-term memory of the person's preferences [code: docs/memory.md]
Long chats are compacted into a checkpoint [trace]
"""


def _values(names: tuple[str, ...]) -> dict:
    values = dummy_values(names)
    for name in names:
        if PLACEHOLDERS[name][0] == "profile":
            values[name] = {"value": "trip planning", "evidence": "plans trips for families"}
    return values


def test_every_template_loads_and_fills_into_a_case() -> None:
    templates = load_templates(LIBRARY)
    assert len(templates) >= 10
    for template in templates:
        filled = adapt(template, _values(template.placeholders), PROFILE)
        case = parse_case(filled["case"], template.id)
        assert case.source == f"library: {template.id}"
        assert "{{" not in json.dumps(filled["case"])
        assert {e["placeholder"] for e in filled["evidence"]} == {
            n for n in template.placeholders if PLACEHOLDERS[n][0] == "profile"
        }


def test_a_template_must_declare_what_its_case_uses() -> None:
    case = {"name": "x", "turns": [{"message": "{{fact}}"}, {"break": "conversation"}]}
    meta = {"title": "t", "checks": "c", "why": "w", "est_turns": 2}
    with pytest.raises(LibraryError, match="also uses conversation_break"):
        parse_template({**meta, "needs": [], "case": case}, "t")
    with pytest.raises(LibraryError, match="no placeholder"):
        parse_template({**meta, "needs": [], "case": {"name": "{{nope}}", "turns": []}}, "t")
    parse_template({**meta, "needs": ["conversation_break"], "case": case}, "t")


def test_fit_verdicts() -> None:
    by_id = {t.id: t for t in load_templates(LIBRARY)}
    forget = by_id["forget-on-request"]
    assert fit(forget, STATEFUL, CHECKS, True, PROFILE)["verdict"] == "fits"
    http = fit(forget, {"isolation", "parallel"}, None, True, PROFILE)
    assert http["verdict"] == "needs" and http["label"] == "Needs state checks"
    assert http["missing"] == ["state", "conversation_break"]
    older = fit(forget, STATEFUL, frozenset({"memory_contains"}), True, PROFILE)
    assert older["missing"] == ["check:memory_lacks"]
    assert fit(forget, STATEFUL, CHECKS, False, PROFILE)["missing"] == ["traces"]
    assert fit(forget, STATEFUL, CHECKS, True, "")["label"] == "Needs a profile"
    stateless = fit(forget, STATEFUL, CHECKS, True, "Answers billing questions [trace]")
    assert stateless == {
        "verdict": "not_applicable",
        "label": "Not for this agent",
        "missing": ["memory"],
        "reason": "Its profile doesn't mention memory.",
    }
    plain = by_id["withdrawn-instruction"]
    assert fit(plain, set(), None, False, "")["verdict"] == "fits"


def test_adapt_needs_evidence_from_the_profile() -> None:
    template = next(t for t in load_templates(LIBRARY) if t.id == "withdrawn-instruction")
    values = _values(template.placeholders)
    values["ask"] = {"value": "Plan a weekend in Porto", "evidence": "keeps a diary"}
    values["seconds"] = 5
    with pytest.raises(LibraryError) as caught:
        adapt(template, values, PROFILE)
    assert "ask: evidence must quote a line" in str(caught.value)
    assert "seconds: a whole number from 30" in str(caught.value)


class FakeTarget:
    name = "fake"

    def __init__(self, conversation_break: bool) -> None:
        self.capabilities = Capabilities(Isolation.NONE, "", conversation_break=conversation_break)
        self.sent: list[str] = []

    async def send(self, session, message, *, files=(), stop_after=None, deadline):
        self.sent.append(message)
        return Turn(reply=f"re: {message}", started=0.0, ended=0.0)

    async def break_conversation(self, session: Session) -> None:
        self.sent.append("<break>")
        session.trace_value = "thread-2"


CASE = {
    "name": "Remembers",
    "turns": [{"message": "I'm Ana"}, {"break": "conversation"}, {"message": "Who am I?"}],
}


async def test_a_break_turn_starts_a_new_conversation(tmp_path) -> None:
    case = parse_case(CASE, "x")
    assert [t.is_message for t in case.turns] == [True, False, True]
    target = FakeTarget(True)
    session = Session("s", "session.id", "thread-1")
    played = await play_try(target, session, case, tmp_path, filler=None, source=None)
    assert played.error is None and target.sent == ["I'm Ana", "<break>", "Who am I?"]
    assert played.earlier_values == ["thread-1"] and played.reply_text == "re: Who am I?"

    plain = FakeTarget(False)
    played = await play_try(plain, session, case, tmp_path, filler=None, source=None)
    assert played.error == "this target can't start a new conversation (no conversation_break)"
    assert plain.sent == ["I'm Ana"]
    with pytest.raises(CaseError, match="a break turn is only"):
        parse_case({"name": "x", "turns": [{"break": "person"}]}, "x")


async def test_the_agent_adapts_only_what_fits(tmp_path) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "library_dir": str(LIBRARY),
                "targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}},
            }
        )
    )
    (tmp_path / "sets" / "bot" / "main").mkdir(parents=True)
    (tmp_path / "sets" / "bot" / "main" / "set.json").write_text(json.dumps({"name": "Main"}))
    config = load(tmp_path / "evalution.yaml")
    agent = Agent(
        Runs(Targets(config), FileStore(config.data_dir), None), SessionFiles(tmp_path), None
    )
    opened = await agent.create("bot", "main")
    turn = AgentTurn(await agent._session(opened["id"]))
    rows = {r["id"]: r["fit"]["label"] for r in await agent.tools.list_library(turn, {})}
    assert rows["preference-across-conversations"] == "Needs new conversations"
    assert rows["long-chat-keeps-name-role"] == "Fits"
    assert rows["withdrawn-instruction"] == "Fits"
    refused = await agent.tools.adapt_template(turn, {"id": "forget-on-request"})
    assert refused.startswith("Refused: Needs state checks.")
    # The owner's words go in as they are; the time limit comes from what messages take.
    words = {
        "test_name": "Gives the total",
        "prompt": "What's the total of order 7?",
        "exact": "4,828.13",
        "outcome": "Gives the total",
        "must_not": "making up a figure",
    }
    made = await agent.tools.adapt_template(turn, {"id": "exact-answer", "values": words})
    assert made["case"]["reply"]["contains"] == ["4,828.13"]
    assert made["case"]["limits"]["seconds"] == 60
    typed = {**words, "seconds": 900}
    made = await agent.tools.adapt_template(turn, {"id": "exact-answer", "values": typed})
    assert made["case"]["limits"]["seconds"] == 900, "control: a limit asked for stays"
    shown = await agent.tools.adapt_template(turn, {"id": "withdrawn-instruction"})
    assert set(shown["fill"]) == {"preference", "ask", "filler_topic", "effect", "seconds"}


def test_the_kinds_ask_for_the_owners_words_and_fit_what_the_app_can_do() -> None:
    kinds = {t.kind: t for t in load_templates(LIBRARY) if t.kind}
    assert sorted(kinds) == sorted(KINDS)
    for template in kinds.values():
        asked = [a["name"] for a in template.asks()]
        assert {"prompt", "outcome", "must_not"} <= set(asked)

    plain = ({"isolation", "parallel"}, None, True, PROFILE, frozenset({"conversation"}))
    assert fit(kinds["exact"], *plain)["verdict"] == "fits"
    assert kinds["exact"].case["reply"]["contains"] == ["{{exact}}"]
    assert fit(kinds["hard_input"], *plain)["verdict"] == "fits"
    # One slip is what an attack test is for; a bad input gets the usual room to vary.
    assert kinds["attack"].case["must_hold"] is True and "must_hold" not in kinds["hard_input"].case
    assert fit(kinds["tools"], *plain)["verdict"] == "fits"
    assert "results" in kinds["tools"].case["tools"]
    assert fit(kinds["tools"], {"isolation"}, None, False, PROFILE)["missing"] == ["traces"]

    waits = fit(kinds["delegation"], *plain)
    assert waits["verdict"] == "needs" and waits["label"] == "Needs waiting on background work"
    able = ({"isolation", "background"}, None, True, PROFILE, frozenset())
    assert fit(kinds["delegation"], *able)["verdict"] == "fits", "control: it declares it"

    memory = kinds["memory"]
    assert memory.case["needs_clean"] == ["memory"] and memory.state_keys == ("memory_contains",)
    lacking = fit(memory, *plain)
    assert lacking["missing"][:2] == ["state", "conversation_break"]
    assert "clean:memory" in lacking["missing"]
    assert "Needs a clean start of memory" in lacking["reason"]
    full = (STATEFUL, CHECKS, True, PROFILE, frozenset({"memory", "conversation"}))
    assert fit(memory, *full)["verdict"] == "fits"


def _question(qid: str, kind: str) -> dict:
    return {
        "question_id": qid,
        "question_type": kind,
        "question": "Where did I go?",
        "answer": "Hawaii",
        "question_date": "2023/05/30",
        "haystack_dates": ["2023/05/20", "2023/05/21"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I went to Hawaii."},
                {"role": "assistant", "content": "x"},
            ],
            [{"role": "user", "content": "Hi again."}, {"role": "user", "content": "Bye."}],
        ],
    }


def test_a_benchmark_question_plays_its_history_then_asks_in_a_new_conversation() -> None:
    case = to_case(_question("q1", "knowledge-update"))
    said = parse_case(case, "c")
    assert said.source == "longmemeval:knowledge-update" and "Hawaii" in said.rubric[0]
    assert ["break" in turn for turn in case["turns"]] == [
        False,
        True,
        False,
        False,
        False,
        True,
        False,
    ]
    assert (
        case["turns"][-3] == {"memory_holds": "Hawaii"} and said.turns[-3].memory_holds == "Hawaii"
    )
    assert not said.turns[-3].is_message
    assert case["turns"][0]["message"].startswith("[This conversation is on 2023/05/20]")
    assert case["turns"][-1]["message"].startswith("[Today is 2023/05/30]")
    assert not any(turn.get("message") == "x" for turn in case["turns"]), (
        "only what the person said"
    )
    made_up = to_case(_question("q2_abs", "multi-session"))
    assert (
        made_up["source"].endswith(":unanswerable")
        and "doesn't know" in made_up["reply"]["rubric"][0]
    )


def test_one_conversation_plays_every_session_with_no_break() -> None:
    case = to_case(_question("q1", "knowledge-update"), "same")
    assert not any("break" in turn for turn in case["turns"])
    dated = [t["message"] for t in case["turns"] if "conversation is on" in t.get("message", "")]
    assert len(dated) == 2, "each session keeps its date"
    assert case["turns"][-2] == {"memory_holds": "Hawaii"}
    parse_case(case, "c")


class _Plannable:
    def __init__(self, breaks: bool) -> None:
        self.capabilities = Capabilities(Isolation.NONE, "", conversation_break=breaks)


def _plan_answer(**changed) -> str:
    base = {
        "verdict": "run",
        "boundary": "same",
        "size": "s",
        "sample": 2,
        "how": "h",
        "why": "w",
        "measures": "m",
        "warnings": [],
        "evidence": ["Each  person has ONE conversation."],
    }
    return json.dumps({**base, **changed})


NOTES = "Notes.\nEach person has one conversation.\nMore."


def test_a_plan_holds_only_if_code_agrees_with_it() -> None:
    target = _Plannable(breaks=False)
    plan = read_plan(_plan_answer(), NOTES, target)
    assert (plan["boundary"], plan["size"], plan["sample"]) == ("same", "s", 2)
    for wrong, said in [
        ({"size": "quick"}, "tests nothing"),
        ({"boundary": "break"}, "can't break"),
        ({"boundary": "wipe"}, "boundary"),
        ({"sample": 99}, "sample"),
        ({"evidence": ["Something the notes never said."]}, "word for word"),
        ({"why": ""}, "why"),
    ]:
        with pytest.raises(ValueError, match=said):
            read_plan(_plan_answer(**wrong), NOTES, target)
    assert "boundary" not in read_plan(_plan_answer(verdict="cannot", size="quick"), NOTES, target)
    allowed = read_plan(_plan_answer(boundary="break", size="quick"), NOTES, _Plannable(True))
    assert allowed["size"] == "quick", "a break clears the context, so a short history can test it"


async def test_the_probe_says_what_it_found() -> None:
    class Stub(Target):
        name = "stub"
        capabilities = Capabilities(Isolation.NONE, "", conversation_break=True)

        def __init__(self) -> None:
            self.broke = 0
            self.closed = False
            self.told = ""

        async def open(self, run: str) -> Session:
            return Session("s", "session.id", "s")

        async def break_conversation(self, session: Session) -> None:
            self.broke += 1

        async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
            if "is called" in message:
                self.told = message.split("is called ")[1].split(".")[0]
            return Turn(self.told if "called?" in message else "ok", 0.0, 0.0)

        async def close(self, session: Session) -> None:
            self.closed = True

    stub = Stub()
    found = await probe(stub, "break")
    assert found["ok"] and found["answered"] and not found["memory_readable"]
    assert stub.broke == 2 and stub.closed
    same = Stub()
    assert (await probe(same, "same"))["ok"] and same.broke == 0


class _RemembersByProfile(Target):
    """A made-up app whose new chats read a saved profile."""

    name = "bot"
    capabilities = Capabilities(Isolation.IDENTITY, "", conversation_break=True, reset=True)

    def __init__(self) -> None:
        self.told: list[str] = []
        self.breaks = 0

    async def open(self, run: str) -> Session:
        return Session(run, "session.id", run)

    async def break_conversation(self, session: Session) -> None:
        self.breaks += 1

    async def send(self, session, message, *, files=(), stop_after=None, deadline) -> Turn:
        self.told.append(message)
        if "parrot called?" in message:
            return Turn(self.told[0].split("is called ")[1].split(".")[0], 0.0, 0.0)
        return Turn("ok", 0.0, 0.0)

    async def close(self, session: Session) -> None:
        return None

    async def read_memory(self, session: Session) -> MemoryRead:
        return MemoryRead("\n".join(self.told))

    async def reset(self, session: Session) -> None:
        self.told.clear()


class _OneThreadForever(_RemembersByProfile):
    """A made-up app with one conversation per person, and no way to start another."""

    capabilities = Capabilities(Isolation.IDENTITY, "", conversation_break=False, reset=True)


@pytest.mark.parametrize(
    ("app", "boundary", "breaks"),
    [(_RemembersByProfile, "break", True), (_OneThreadForever, "same", False)],
)
async def test_any_app_goes_from_plan_to_report(
    tmp_path, monkeypatch, app, boundary, breaks
) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}},
                "model": {
                    "kind": "openai",
                    "base_url": "http://model",
                    "models": {"strong": "m", "light": "m"},
                },
            }
        )
    )
    config = load(tmp_path / "evalution.yaml")
    folder = config.data_dir / "benchmarks" / "longmemeval"
    folder.mkdir(parents=True)
    data = [_question(f"q{n}", "single-session-user") for n in range(3)]
    (folder / "longmemeval_s_cleaned.json").write_text(json.dumps(data))
    target = app()
    monkeypatch.setitem(adapters._built, "bot", target)

    def model(request: httpx.Request) -> httpx.Response:
        asked = json.dumps(json.loads(request.content))
        if "You plan how a memory benchmark" in asked:
            said = _plan_answer(boundary=boundary, sample=None, evidence=["conversation break"])
        elif "whether a text an assistant had" in asked:
            said = json.dumps({"holds": "Hawaii" in asked.split("the text")[-1]})
        else:
            said = json.dumps({"lines": [{"id": 1, "pass": True, "sure": True, "reason": "r"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": said}}], "usage": {}})

    llm = LLM(config.model, transport=httpx.MockTransport(model))
    targets = Targets(config)
    runs = Runs(targets, FileStore(config.data_dir), llm)
    plan = await planning.plan_for(targets, "bot", target, llm.caller("strong"), config.data_dir)
    assert plan["boundary"] == boundary and plan["probe"]["ok"] and plan["probe"]["answered"]
    assert plan["questions"] == 3 and plan["messages"] == 12

    made = planning.approve(targets, "bot")
    assert made["set"] == ("longmemeval-s-same" if boundary == "same" else "longmemeval-s")
    target.breaks = 0
    target.told.clear()
    started = await runs.start(runs.estimate("bot", made["set"], None, 1)["token"])
    await runs.wait()
    run = runs.load(started["run"])
    assert run["status"] == "done" and run["plan"]["boundary"] == boundary
    found = report(run)
    assert (found["right"], found["judged"]) == (3, 3)
    assert (found["memory"]["measured"], found["memory"]["committed"]) == (3, 3)
    assert (target.breaks > 0) is breaks, "the plan decides, with nothing special in the adapter"
    await llm.close()
    await targets.close()


async def test_an_app_that_cant_start_clean_is_told_so_without_asking_a_model(tmp_path) -> None:
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    config = load(tmp_path / "evalution.yaml")

    class Shared(_RemembersByProfile):
        capabilities = Capabilities(Isolation.NONE, "")

    async def never(**_: object) -> str:
        raise AssertionError("no model is needed for this")

    plan = await planning.plan_for(Targets(config), "bot", Shared(), never, config.data_dir)
    assert plan["verdict"] == "cannot" and "earlier memory" in plan["why"]


def test_one_conversation_that_never_compacted_says_so() -> None:
    found = {"memory": {"compact_measured": 2, "compacted": 0}}
    assert untested(found, {"boundary": "same"})
    assert "didn't test memory" in happened(found, {"boundary": "same"})
    assert not untested(found, {"boundary": "break"})
    assert not untested({"memory": {"compact_measured": 2, "compacted": 1}}, {"boundary": "same"})
    assert not untested({"memory": {"compact_measured": 0, "compacted": 0}}, {"boundary": "same"})


def test_a_benchmark_sample_is_stable_and_leaves_out_what_cannot_play() -> None:
    entries = [_question(f"q{n}", "multi-session") for n in range(30)]
    entries += [_question("q-a", "single-session-assistant"), _question("q-b_abs", "multi-session")]
    first = [e["question_id"] for e in sample(entries, 5)]
    assert first == [e["question_id"] for e in sample(list(reversed(entries)), 5)]
    assert len(first) == 6 and "q-a" not in first and "q-b_abs" in first
    assert len(sample(entries, None)) == 31


def test_a_benchmark_report_adds_up(tmp_path: Path) -> None:
    def tried(outcome: str, source: str) -> dict:
        check = {"id": "rubric:0", "kind": "rubric", "decided_by": "model", "role": "must"}
        made = {
            "outcome": outcome,
            "checks": [{**check, "outcome": outcome, "why": "w"}],
            "rules": 2,
        }
        memory = {"committed": outcome == "passed", "written": True}
        spent = {"tokens": 10, "usd": 0.5, "seconds": 3.0, "messages": 2, "models": ["m"]}
        attempt = {"n": 1, "judgements": [made], "memory": memory, **spent}
        return {"source": source, "tries": [attempt]}

    run = {
        "run": "r",
        "set": "longmemeval-quick",
        "status": "running",
        "current": {"name": "q", "stage": "playing", "turn": 2, "turns": 9},
        "cases": {
            "a": tried("passed", "longmemeval:multi-session"),
            "b": tried("failed", "longmemeval:multi-session"),
            "c": tried("failed", "longmemeval:temporal-reasoning"),
            "d": tried("passed", "longmemeval:multi-session:unanswerable"),
            "e": tried("failed", "someone-else"),
            "f": {"source": "longmemeval:multi-session", "tries": []},
        },
    }
    found = report(run)
    assert (found["right"], found["judged"], found["accuracy"]) == (1, 3, 1 / 3)
    assert found["task_average"] == 0.25
    assert found["kinds"]["multi-session"] == {"right": 1, "judged": 2}
    assert (found["admitted"], found["unanswerable"]) == (1, 1)
    assert {k: found["memory"][k] for k in ("measured", "written", "committed", "used")} == {
        "measured": 3,
        "written": 3,
        "committed": 1,
        "used": 1,
    }
    assert found["write_recall"] == 1 / 3

    shown = overview("bot", tmp_path, [run])["runs"][0]
    assert shown["totals"] == {
        "tokens": 40,
        "usd": 2.0,
        "median_seconds": 3.0,
        "models": ["m"],
    }, "only the benchmark's own questions count"
    assert (
        shown["current"]["turn"] == 2
        and overview("bot", tmp_path, [{**run, "status": "done"}])["runs"][0]["current"] is None
    )
    played, waiting = shown["questions"][0], shown["questions"][-1]
    assert (played["tokens"], played["messages"], played["models"]) == (10, 2, ["m"])
    assert played["trace_url"] is None and (waiting["tokens"], waiting["models"]) == (None, [])
    bare = overview("bot", tmp_path, [{**run, "cases": {"f": run["cases"]["f"]}}])["runs"][0]
    assert bare["happened"] == "", "nothing played, nothing to say"
    assert bare["totals"] == {"tokens": None, "usd": None, "median_seconds": None, "models": []}
    empty = {**run, "status": "stopped", "cases": {"f": run["cases"]["f"]}}
    assert overview("bot", tmp_path, [empty])["runs"] == [], "stopped with nothing played"


def test_what_happened_is_said_from_the_numbers() -> None:
    def said(right=0, judged=10, admitted=0, unanswerable=0, unjudged=0, memory=None) -> str:
        return happened(
            {
                "right": right,
                "judged": judged,
                "admitted": admitted,
                "unanswerable": unanswerable,
                "unjudged": unjudged,
                "memory": dict(
                    zip(
                        ("measured", "written", "committed", "used"),
                        memory or (0,) * 4,
                        strict=True,
                    )
                ),
            }
        )

    assert said(judged=0) == ""
    assert said(judged=2, unjudged=4) == "Too few questions to say. 4 couldn't be judged."
    assert said(judged=0, unjudged=9) == "Too few questions to say. 9 couldn't be judged."
    assert said(memory=(10, 2, 2, 1)).startswith("Lost most facts before saving: 8 of 10 never")
    assert said(memory=(10, 9, 3, 1)).startswith("Wrote facts but didn't commit them: 6 of 10")
    assert said(memory=(10, 9, 8, 1)).startswith("Saved facts but didn't use them: 8 of 10 saved")
    assert said(9, memory=(10, 9, 8, 7)) == "9 of 10 right, with 8 of 10 facts saved."
    assert said(4) == "4 of 10 right; memory wasn't measured."
    assert said(4, memory=(3, 0, 0, 0)) == "4 of 10 right.", "too few facts to say more"
    assert said(judged=0, admitted=3, unanswerable=6).startswith("Said it didn't know 3 of 6")
    assert said(9, admitted=1, unanswerable=5).endswith("Made up 4 of 5 answers it couldn't know.")
    assert said(1, admitted=5, unanswerable=5).endswith("may only mean it remembers little.")
