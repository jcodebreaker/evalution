"""A small stateful assistant with switchable faults, a stub judge, and the rig that plays them.

:class:`ScriptedAgent` is a real ``Target``, found as ``evalution.adapters.scripted_agent`` the way
``tests/fixtures/adapters/echo.py`` is. It keeps memory (facts, forget, correct), a compacting
summary and several conversations for one person, decided by rules on the message text.

Its spans are OTLP/JSON: they go through the real ``traces.otlp.decode`` into the real built-in
``SpanStore`` and are read back by the real ``Builtin`` source and ``traces.view.build``, in either
convention (``shape``: OpenInference or OpenTelemetry GenAI).

Nothing here sleeps. :class:`Clock` stands in for ``time`` and ``asyncio.sleep`` in
``evalution.runs.play`` and ``collect``, so a turn, the settle delay and the wait for traces take
no real time.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import yaml

from evalution.adapters.base import (
    AdapterInfo,
    Capabilities,
    Isolation,
    Session,
    StateResult,
    Target,
    TargetGone,
    Turn,
    part,
)
from evalution.config import TargetConfig
from evalution.config import load as load_config
from evalution.core.evidence import Failure, TurnEnd
from evalution.core.trace import Section
from evalution.judging.rubric import FILLER_INSTRUCTIONS, SCEPTIC_INSTRUCTIONS
from evalution.judging.rubric import INSTRUCTIONS as GRADER_INSTRUCTIONS
from evalution.llm import LLMError
from evalution.review.findings import REPORT_INSTRUCTIONS, RUN_INSTRUCTIONS, TRY_INSTRUCTIONS
from evalution.runs.runner import Runs
from evalution.suite import library
from evalution.suite.store import save_case, save_set
from evalution.targets import Targets
from evalution.traces import otlp

TARGET = "agent"
SET = "main"
LIBRARY = Path(__file__).resolve().parents[3] / "library"

#: What the agent can have wrong, each a switch.
BEHAVIOUR_FAULTS = frozenset(
    {
        "summary_drops_early_facts",
        "forget_says_done_only",
        "withdrawn_fact_still_sent",
        "correction_keeps_old_fact",
        # Beyond the seeded list: the memory-recalls test has no other fault its check can see.
        "forgets_to_store",
        "leak_across_tries",
        # The state read looks in the wrong place, so it finds nothing.
        "wrong_namespace",
    }
)
TRACE_FAULTS = frozenset({"content_capture_off", "late_span_batch", "partial_trace"})
RUN_FAULTS = frozenset({"sends_no_traces", "unreachable_mid_try"})
#: What an adapter can have wrong, the app doing its part: it reads its answer from the wrong place
#: (``nested_reply_read_flat``), waits for one it will never read (``polls_for_unread_reply``), and
#: on top of that the app restarts while it waits (``restart_while_polling``).
ADAPTER_FAULTS = frozenset(
    {
        "nested_reply_read_flat",
        "polls_for_unread_reply",
        "restart_while_polling",
        "ignores_deadline",
    }
)
JUDGE_FAULTS = frozenset({"judge_outage", "judge_unsure_both", "judge_blips"})
#: What the work behind a turn can do. ``delegates``: it answers "Queued!" and a child does the work
#: for ``work_seconds``, which ``send`` waits for. The rest: the work fails, or stops to wait for an
#: approval; ``send`` comes back while the work goes on; the turn runs out of time, with nothing
#: ever started (``never_starts``) or with the child still going (``work_outlasts_the_turn``).
WORK_FAULTS = frozenset(
    {
        "delegates",
        "work_fails",
        "work_waits",
        "answers_before_the_work_ends",
        "never_starts",
        "work_outlasts_the_turn",
    }
)
#: The app isn't there for the first ``blips`` messages it is sent.
APP_FAULTS = frozenset({"app_blips"})
FAULTS = (
    BEHAVIOUR_FAULTS
    | TRACE_FAULTS
    | RUN_FAULTS
    | JUDGE_FAULTS
    | ADAPTER_FAULTS
    | WORK_FAULTS
    | APP_FAULTS
)

#: Raw messages a conversation holds before it is compacted.
COMPACT_AFTER = 8
#: A late batch lands this long after its turn: past the settle delay and the wait for traces.
LATE_SECONDS = 60.0
#: ``evalution.traces.base.MAX_SPANS``: the read stops at this many AI spans.
OVER_THE_CAP = 20_000
MODEL = "scripted-model"


class Clock:
    """Stands in for ``time`` and ``asyncio.sleep`` in ``evalution.runs.play``."""

    def __init__(self, start: float) -> None:
        self.now = start
        self.on_sleep: list[Any] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        for call in self.on_sleep:
            call()
        await asyncio.sleep(0)


@dataclass
class Conversation:
    messages: list[dict[str, str]] = field(default_factory=list)
    #: ``(the note, the turn it came in)``: what the raw messages told it, not yet summarised.
    notes: list[tuple[str, int]] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    compacted: bool = False
    instruction: str | None = None
    turn: int = 0


@dataclass
class Person:
    memory: list[str] = field(default_factory=list)
    #: Withdrawn from memory, and still put in every request (``withdrawn_fact_still_sent``).
    shadow: list[str] = field(default_factory=list)
    conversation: Conversation = field(default_factory=Conversation)


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def _best(lines: Iterable[str], question: str) -> str | None:
    """The line sharing the most words with *question*; the earliest of equals."""
    wanted = _words(question)
    scored = [(len(wanted & _words(line)), -i, line) for i, line in enumerate(lines)]
    found = max(scored, default=None)
    return found[2] if found and found[0] else None


def _otlp_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


class ScriptedAgent(Target):
    capabilities = Capabilities(
        Isolation.IDENTITY,
        "A reset clears everything it keeps about the person.",
        reset=True,
        conversation_break=True,
        sections=True,
        state_checks=frozenset({"checkpoint", "memory_contains", "memory_lacks"}),
    )

    def __init__(self, target: TargetConfig) -> None:
        self.name = target.name
        self.faults = frozenset(target.options.get("faults") or ())
        if unknown := self.faults - FAULTS:
            raise ValueError(f"no such faults: {sorted(unknown)}")
        self.shape = target.options.get("shape", "openinference")
        self.capabilities = replace(
            self.capabilities,
            background=bool(target.options.get("background")),
            cancel=bool(target.options.get("can_cancel")),
            blocks=bool(target.options.get("can_block")),
        )
        self.clock: Clock | None = None
        self.store: Any = None
        self.people: dict[str, Person] = {}
        #: A turn's length in simulated seconds; its spans take their share of it.
        self.turn_seconds = 5.0
        #: ``unreachable_mid_try``: the app stops answering ("connect"), or the session goes.
        self.unreachable_how = "connect"
        #: ``forgets_to_store`` holds from this try on.
        self.store_fails_from_try = 1
        #: Tries whose spans never reach the source.
        self.no_spans_on_tries: set[int] = set()
        #: A reset keeps the session's trace value, as an adapter that resets one conversation does.
        self.stable_trace_value = False
        self.tries = 0
        self.sends = 0
        self.polled = 0
        self.every_send = 0
        #: Child work runs until this time; a message that comes before then lands on top of it.
        self.work_seconds = 30.0
        self.work_until = 0.0
        self.on_top = 0
        self.cancelled: list[str] = []
        self.closed: list[str] = []
        #: ``app_blips``: how many more messages find the app not there.
        self.blips = 1
        #: Stops the message numbered ``hold_from`` where it is, to look at a run in the middle.
        self.hold: asyncio.Event | None = None
        self.hold_from = 1
        self._ids = 0
        self._conversations = 0
        self._outbox: list[tuple[float, dict[str, Any]]] = []

    def wire(self, clock: Clock, store: Any) -> None:
        self.clock, self.store = clock, store
        clock.on_sleep.append(self.deliver)

    # The Target

    async def open(self, run: str) -> Session:
        self.people[run] = Person()
        return Session(run, "session.id", self._conversation_id(run), {"person": run})

    def _conversation_id(self, run: str) -> str:
        self._conversations += 1
        return f"scripted-{run}-c{self._conversations}"

    async def reset(self, session: Session) -> None:
        self.tries += 1
        self.sends = 0
        person = self.people[session.extra["person"]]
        if "leak_across_tries" not in self.faults:
            person.memory, person.shadow = [], []
        person.conversation = Conversation()
        if not self.stable_trace_value:
            session.trace_value = self._conversation_id(session.extra["person"])

    async def break_conversation(self, session: Session) -> None:
        self.people[session.extra["person"]].conversation = Conversation()
        session.trace_value = self._conversation_id(session.extra["person"])

    async def cancel(self, session: Session) -> None:
        if not self.capabilities.cancel:
            await super().cancel(session)
        self.cancelled.append(session.id)
        assert self.clock is not None
        self.work_until = self.clock.now

    async def close(self, session: Session) -> None:
        self.closed.append(session.id)

    async def state(self, session: Session, check: dict[str, Any]) -> StateResult:
        ((key, value),) = check.items()
        person = self.people[session.extra["person"]]
        if key == "checkpoint":
            done = person.conversation.compacted
            return StateResult(done == bool(value), f"compacted: {done}", f"compacted: {done}")
        unread = "wrong_namespace" in self.faults
        memory = [] if unread else person.memory
        found = [line for line in memory if str(value).casefold() in line.casefold()]
        said = f"{len(found)} of {len(memory)} memory item(s) contain {value!r}"
        # What it can vouch for: finding nothing where it looked can't show the thing is absent.
        observed = not (unread and key == "memory_lacks")
        return StateResult(bool(found) == (key == "memory_contains"), said, said, observed)

    def sections(self, request: dict[str, Any]) -> list[Section]:
        said = [str(m.get("content")) for m in request.get("messages") or []]
        return [Section("turns", "\n".join(said))]

    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),  # type: ignore[assignment]
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn:
        assert self.clock is not None, "wire the agent to a clock first"
        self.sends += 1
        self.every_send += 1
        if self.hold is not None and self.every_send >= self.hold_from:
            await self.hold.wait()
        if self.clock.now < self.work_until:
            self.on_top += 1
        if "app_blips" in self.faults and self.blips > 0:
            self.blips -= 1
            raise httpx.ConnectError("connection refused")
        if "never_starts" in self.faults:
            await self._until(deadline)
            return self._out_of_time("", self.clock.now)
        if "unreachable_mid_try" in self.faults and self.sends >= 2:
            if self.unreachable_how == "gone":
                raise TargetGone("the app went away")
            raise httpx.ConnectError("connection refused")
        person = self.people[session.extra["person"]]
        conversation = person.conversation
        conversation.turn += 1
        started = self.clock.now
        request = self._request(person, conversation, message)
        reply, tools = self._act(person, conversation, message)
        conversation.messages += [
            {"role": "user", "content": message},
            {"role": "assistant", "content": reply},
        ]
        trace = self._new_id(32)
        root = self._new_id(16)
        attrs = self._agent_attrs(session.trace_value, message, reply)
        spans = [self._span(trace, None, root, "turn", started, 0.0, 1.0, attrs)]
        spans.append(self._model_span(trace, root, started, 0.1, 0.2, request, reply))
        step = 0.2 / max(len(tools), 1)
        for i, (name, args, result) in enumerate(tools):
            tool = self._tool_attrs(name, args, result)
            at = 0.3 + i * step
            spans.append(
                self._span(trace, root, self._new_id(16), name, started, at, at + step, tool)
            )
        if tools:
            later = self._request(person, conversation, message)
            spans.append(self._model_span(trace, root, started, 0.5, 0.1, later, reply))
        if len(conversation.messages) >= COMPACT_AFTER:
            summarise = self._request(person, conversation, "Summarise this chat.")
            spans.append(self._model_span(trace, root, started, 0.6, 0.1, summarise, ""))
            self._compact(conversation)
        if "partial_trace" in self.faults and self.sends == 2:
            busy = self._busy_attrs(session.trace_value)
            spans += [
                self._span(trace, root, self._new_id(16), "work", started, 0.9, 0.95, busy)
                for _ in range(OVER_THE_CAP)
            ]
        self._post(spans)
        self.clock.now = started + self.turn_seconds
        if self.faults & WORK_FAULTS:
            return await self._work(reply, started, deadline)
        return await self._as_read(reply, started, deadline)

    async def _until(self, when: float) -> None:
        """Wait for simulated time, which moves with the watch on the turn."""
        assert self.clock is not None
        while True:
            if self.clock.now >= when:
                return
            await asyncio.sleep(0)

    def _out_of_time(self, reply: str, started: float) -> Turn:
        assert self.clock is not None
        error = "the turn did not finish in time"
        return Turn(reply, started, self.clock.now, error=error, failure=Failure.TIMED_OUT)

    async def _work(self, reply: str, started: float, deadline: float) -> Turn:
        """What comes after "Queued!": the work behind the turn."""
        assert self.clock is not None
        self.work_until = self.clock.now + self.work_seconds
        if "answers_before_the_work_ends" in self.faults:
            return Turn("Queued!", started, self.clock.now)
        if "work_outlasts_the_turn" in self.faults:
            await self._until(deadline)
            return self._out_of_time("", started)
        await self._until(self.work_until)
        if "work_fails" in self.faults:
            error = "the child's build step failed"
            return Turn("", started, self.clock.now, error=error, ended_as=TurnEnd.FAILED)
        if "work_waits" in self.faults:
            error = "waiting for an approval"
            return Turn(reply, started, self.clock.now, error=error, ended_as=TurnEnd.BLOCKED)
        return Turn(reply, started, self.clock.now)

    async def _as_read(self, reply: str, started: float, deadline: float) -> Turn:
        """The finished turn, as an adapter with a fault reads it. The app has done its part by now.
        A response shaped ``{"item": {"data": {"role", "content"}}}`` read as if flat comes out
        empty, and a poll until there's a reply never ends."""
        assert self.clock is not None
        if "nested_reply_read_flat" in self.faults:
            return Turn("", started, self.clock.now)
        polling = self.faults & {
            "polls_for_unread_reply",
            "restart_while_polling",
            "ignores_deadline",
        }
        if not polling:
            return Turn(reply, started, self.clock.now)
        while True:
            # Time moves with the watch on the turn: here, real reads would race a simulated clock.
            await asyncio.sleep(0)
            self.polled += 1
            if "restart_while_polling" in self.faults and self.clock.now - started >= 540:
                raise httpx.ConnectError("connection refused")
            if "ignores_deadline" not in self.faults and self.clock.now >= deadline:
                return Turn("", started, self.clock.now, error="not done in time")

    # How it behaves

    def _request(self, person: Person, conversation: Conversation, message: str) -> dict[str, Any]:
        """What the model is sent: what it remembers and the summary, the raw chat, the message."""
        system = ["You are a scripted assistant."]
        if remembered := [*person.memory, *person.shadow]:
            system += ["What you remember:", *remembered]
        if conversation.summary:
            system += ["Summary of this chat so far:", *conversation.summary]
        return {
            "system": "\n".join(system),
            "messages": [*conversation.messages, {"role": "user", "content": message}],
        }

    def _known(self, person: Person, conversation: Conversation) -> list[str]:
        notes = [note for note, _ in conversation.notes]
        return [*person.memory, *person.shadow, *conversation.summary, *notes]

    def _stores(self) -> bool:
        return "forgets_to_store" not in self.faults or self.tries < self.store_fails_from_try

    def _remember(self, person: Person, line: str) -> list[tuple[str, dict[str, str], str]]:
        if not self._stores():
            return []
        person.memory.append(line)
        return [("memory_write", {"fact": line}, "stored")]

    def _act(
        self, person: Person, conversation: Conversation, message: str
    ) -> tuple[str, list[tuple[str, dict[str, Any], str]]]:
        """The reply to *message* and the tool calls it took, changing what the agent keeps."""
        if found := re.match(r"^Please use (.+)\.$", message):
            names = found[1].split(", ")
            return "Done.", [(name, {}, f"{name} finished") for name in names]
        if found := re.match(
            r"^(?:By the way|Something to remember|For the record|Noting this down): (.+)$", message
        ):
            return "Got it.", self._remember(person, found[1])
        if found := re.match(r"^Sorry, I got that wrong\. (.+)$", message):
            return "Thanks, corrected.", self._correct(person, found[1])
        if message.startswith("Actually, please forget"):
            return "Done, I've forgotten that.", self._forget(person)
        if found := re.match(r"^One thing for all our chats from now on: please (.+)\.$", message):
            return "I'll do that from now on.", self._remember(person, f"preference: {found[1]}")
        if found := re.match(r"^From now on, please (.+)\.$", message):
            conversation.instruction = found[1]
            return "Understood.", []
        if message.startswith("You can drop that instruction"):
            if "withdrawn_fact_still_sent" not in self.faults:
                conversation.instruction = None
            return "Back to normal.", []
        if found := re.match(r"^Let's (.+?)\. Do only the first step", message):
            tools = self._remember(person, f"task: {found[1]}")
            return f"Step one of {found[1]} is done. I'll stop here.", tools
        if message.startswith("I'm back."):
            task = next((k for k in person.memory if k.startswith("task: ")), None)
            if task is None:
                return "I don't know what we were doing. Can you explain the task?", []
            said = (
                f"We were going to {task.removeprefix('task: ')}. Step one is done, so step two: "
                "day one is the old town, day two the coast, day three the hills."
            )
            return said, []
        if found := re.match(r"^Some background on .+?: (.+)$", message):
            conversation.notes.append((found[1], conversation.turn))
            return "Noted.", []
        if found := re.match(
            r"^Before we start on .+?, one decision we made: (.+?) Please keep that in mind\.$",
            message,
        ):
            conversation.notes.append((f"decision: {found[1]}", conversation.turn))
            return "Noted, I'll keep that in mind.", []
        if message.startswith("Scratch that"):
            if conversation.notes and "withdrawn_fact_still_sent" not in self.faults:
                conversation.notes.pop()
            return "Dropped.", []
        if message.startswith("Quick check"):
            known = self._known(person, conversation)
            decided = next((k for k in known if k.startswith("decision: ")), None)
            if decided is None:
                return "I can't remember. Can you remind me?", []
            return f"We decided: {decided.removeprefix('decision: ')}", []
        if found := re.match(r"^Hi, I'm (\w+), (.+?)\. (.*)$", message):
            conversation.notes.append((f"person: {found[1]} is {found[2]}", conversation.turn))
            return self._styled(person, conversation, f"Hi {found[1]}. Working on it."), []
        if message.startswith("Write a two-line intro"):
            known = self._known(person, conversation)
            who = next((k for k in known if k.startswith("person: ")), None)
            if who is None:
                return "Who are you again?", []
            return f"{who.removeprefix('person: ')}.\nGlad to have them on the team.", []
        if message.endswith("?"):
            line = _best(self._known(person, conversation), message)
            tools = [("memory_search", {"query": message}, line or "(nothing)")]
            return (line or "I don't know that.", tools)
        return self._styled(
            person,
            conversation,
            "Here you go: this week we shipped the new reports.\nNext week we start on billing.",
        ), []

    def _correct(self, person: Person, line: str) -> list[tuple[str, dict[str, str], str]]:
        if "correction_keeps_old_fact" in self.faults:
            person.memory.append(line)
            return [("memory_write", {"fact": line}, "stored")]
        facts = [i for i, known in enumerate(person.memory) if ": " not in known]
        if facts:
            person.memory[facts[-1]] = line
        return [("memory_update", {"fact": line}, "updated")]

    def _forget(self, person: Person) -> list[tuple[str, dict[str, str], str]]:
        if "forget_says_done_only" in self.faults or not person.memory:
            return []
        gone = person.memory.pop()
        if "withdrawn_fact_still_sent" in self.faults:
            person.shadow.append(gone)
        return [("memory_delete", {"fact": gone}, "deleted")]

    def _styled(self, person: Person, conversation: Conversation, text: str) -> str:
        """*text* following any standing instruction that says how a reply starts a line."""
        standing = [conversation.instruction or ""]
        standing += [k.removeprefix("preference: ") for k in person.memory if "preference: " in k]
        for instruction in standing:
            if found := re.search(r'"(.+?)"', instruction):
                text += f"\n{found[1]} done."
        return text

    def _compact(self, conversation: Conversation) -> None:
        if "summary_drops_early_facts" in self.faults:
            recent = conversation.turn - 1
            conversation.summary = [n for n, turn in conversation.notes if turn >= recent]
        else:
            conversation.summary += [note for note, _ in conversation.notes]
        conversation.notes = []
        conversation.messages = conversation.messages[-2:]
        conversation.compacted = True

    # Its spans

    def _new_id(self, size: int) -> str:
        self._ids += 1
        return f"{self._ids:0{size}x}"

    def _span(
        self,
        trace: str,
        parent: str | None,
        span_id: str,
        name: str,
        started: float,
        at: float,
        until: float,
        attrs: dict[str, Any],
    ) -> dict[str, Any]:
        begin, end = started + at * self.turn_seconds, started + until * self.turn_seconds
        return {
            "traceId": trace,
            "spanId": span_id,
            "parentSpanId": parent or "",
            "name": name,
            "kind": 1,
            "startTimeUnixNano": str(int(begin * 1e9)),
            "endTimeUnixNano": str(int(end * 1e9)),
            "status": {"code": 1},
            "attributes": [{"key": k, "value": _otlp_value(v)} for k, v in attrs.items()],
        }

    def _agent_attrs(self, conversation: str, message: str, reply: str) -> dict[str, Any]:
        if self.shape == "genai":
            return {"gen_ai.operation.name": "invoke_agent", "gen_ai.conversation.id": conversation}
        return {
            "openinference.span.kind": "AGENT",
            "session.id": conversation,
            "input.value": message,
            "output.value": reply,
        }

    def _busy_attrs(self, conversation: str) -> dict[str, Any]:
        """A span of plain work that says its session, as an app that tags every span does: the
        read that a cap cuts still knows the turn is this session's."""
        if self.shape == "genai":
            return {"gen_ai.operation.name": "plan", "gen_ai.conversation.id": conversation}
        return {"openinference.span.kind": "CHAIN", "session.id": conversation}

    def _tool_attrs(self, name: str, args: dict[str, Any], result: str) -> dict[str, Any]:
        arguments = json.dumps(args)
        if self.shape == "genai":
            return {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "gen_ai.tool.call.arguments": arguments,
                "gen_ai.tool.call.result": result,
            }
        return {
            "openinference.span.kind": "TOOL",
            "tool.name": name,
            "tool.parameters": arguments,
            "output.value": result,
        }

    def _model_attrs(self, request: dict[str, Any], reply: str) -> dict[str, Any]:
        tokens_in = math.ceil(len(json.dumps(request)) / 4)
        tokens_out = math.ceil(len(reply) / 4)
        captured = "content_capture_off" not in self.faults
        system, messages = request["system"], request["messages"]
        if self.shape == "genai":
            attrs: dict[str, Any] = {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": MODEL,
                "gen_ai.usage.input_tokens": tokens_in,
                "gen_ai.usage.output_tokens": tokens_out,
            }
            if captured:
                said = [
                    {"role": m["role"], "parts": [{"type": "text", "content": m["content"]}]}
                    for m in messages
                ]
                answer = [{"role": "assistant", "parts": [{"type": "text", "content": reply}]}]
                attrs["gen_ai.system_instructions"] = json.dumps(
                    [{"type": "text", "content": system}]
                )
                attrs["gen_ai.input.messages"] = json.dumps(said)
                attrs["gen_ai.output.messages"] = json.dumps(answer)
            return attrs
        attrs = {
            "openinference.span.kind": "LLM",
            "llm.model_name": MODEL,
            "llm.token_count.prompt": tokens_in,
            "llm.token_count.completion": tokens_out,
        }
        if captured:
            sent = [{"role": "system", "content": system}, *messages]
            for i, message in enumerate(sent):
                attrs[f"llm.input_messages.{i}.message.role"] = message["role"]
                attrs[f"llm.input_messages.{i}.message.content"] = message["content"]
            attrs["llm.output_messages.0.message.role"] = "assistant"
            attrs["llm.output_messages.0.message.content"] = reply
        return attrs

    def _model_span(
        self,
        trace: str,
        root: str,
        started: float,
        at: float,
        length: float,
        request: dict[str, Any],
        reply: str,
    ) -> dict[str, Any]:
        attrs = self._model_attrs(request, reply)
        return self._span(trace, root, self._new_id(16), MODEL, started, at, at + length, attrs)

    def _post(self, spans: list[dict[str, Any]]) -> None:
        """Queue a turn's spans. A late exporter holds back only the newest turn's."""
        assert self.clock is not None
        if self.tries in self.no_spans_on_tries:
            return
        now = self.clock.now
        if "late_span_batch" in self.faults:
            self._outbox = [(min(at, now), span) for at, span in self._outbox]
            self._outbox += [(now + LATE_SECONDS, span) for span in spans]
            return
        self._outbox += [(now, span) for span in spans]

    def deliver(self) -> None:
        """What has reached its time, through the real OTLP decoder into the real store."""
        assert self.clock is not None
        now = self.clock.now
        due = [span for at, span in self._outbox if at <= now]
        self._outbox = [(at, span) for at, span in self._outbox if at > now]
        if self.store is None or not due:
            return
        resource = {"key": "evalution.target", "value": {"stringValue": self.name}}
        body = {
            "resourceSpans": [
                {"resource": {"attributes": [resource]}, "scopeSpans": [{"spans": due}]}
            ]
        }
        rows, rejected = otlp.decode(json.dumps(body).encode(), otlp.JSON)
        if rejected:
            raise RuntimeError(f"{rejected} spans rejected by the OTLP decoder")
        self.store.add(rows, now=now)


def create(target: TargetConfig) -> ScriptedAgent:
    return ScriptedAgent(target)


async def _check(client: Any, options: dict[str, Any], lookup: Any) -> dict[str, Any]:
    return part(True, "It is scripted")


INFO = AdapterInfo(
    name="scripted_agent",
    label="Scripted agent",
    options=("faults", "shape"),
    required=(),
    secrets=(),
    check=_check,
)


# The judge

_RUBRIC = re.compile(r"\[Data: the rubric\.[^\]]*\]\n(.*?)\n\[End of data\]", re.DOTALL)
_REPLY = re.compile(r"\[Data: the final reply\.[^\]]*\]\n(.*?)\n\[End of data\]", re.DOTALL)
_DID = re.compile(r"\[Data: what the assistant did\.[^\]]*\]\n(.*?)\n\[End of data\]", re.DOTALL)


def final_reply(grader_input: str) -> str:
    """The reply as the judge was shown it."""
    found = _REPLY.search(grader_input)
    return found[1] if found else ""


def what_it_did(grader_input: str) -> str:
    """What the assistant did, as the judge was shown it."""
    found = _DID.search(grader_input)
    return found[1] if found else ""


class StubJudge:
    """A ``Caller``. It reads no evidence: its answers are scripted, so a verdict that rests on it
    shows what was scripted and nothing about the agent.

    An answer is ``pass``, ``fail``, ``unsure_pass``, ``unsure_fail``, ``garbage`` or ``outage``.

    :param lines: The answer for each rubric line holding this text; else *default*.
    :param gradings: One answer per grading call, in order, for every line of that call. A line
        is graded twice only when the first grading wasn't sure.
    """

    def __init__(
        self,
        faults: Iterable[str] = (),
        *,
        default: str = "pass",
        lines: dict[str, str] | None = None,
        gradings: Sequence[str] = (),
    ) -> None:
        self.faults = frozenset(faults)
        self.default = default
        self.lines = lines or {}
        self.gradings = list(gradings)
        self.inputs: list[str] = []
        self.filled = 0
        #: ``judge_blips``: how many more calls fail on something passing.
        self.blips = 1
        self.calls_failed = 0
        if "judge_unsure_both" in self.faults:
            self.default = "unsure_pass"

    async def __call__(self, *, instructions: str, input_text: str) -> str:
        if instructions == FILLER_INSTRUCTIONS:
            self.filled += 1
            return f"Another small thing on the list, item {self.filled}."
        if "judge_outage" in self.faults:
            raise LLMError("the model did not answer")
        if "judge_blips" in self.faults and self.blips > 0:
            self.blips -= 1
            self.calls_failed += 1
            raise LLMError("model answered 503: busy", transient=True)
        if instructions in (GRADER_INSTRUCTIONS, SCEPTIC_INSTRUCTIONS):
            self.inputs.append(input_text)
            return self._grade(input_text)
        return json.dumps(self._analysis(instructions))

    def _grade(self, input_text: str) -> str:
        found = _RUBRIC.search(input_text)
        rubric = [re.sub(r"^\d+\. ", "", line) for line in (found[1] if found else "").splitlines()]
        whole = self.gradings.pop(0) if self.gradings else None
        answers = [
            whole or next((a for k, a in self.lines.items() if k in line), self.default)
            for line in rubric
        ]
        if "outage" in answers:
            raise LLMError("the model did not answer")
        if "garbage" in answers:
            return "I'd rather not say."
        out = [
            {
                "line": line,
                "pass": answer.endswith("pass"),
                "sure": not answer.startswith("unsure"),
                "reason": f"scripted: {answer}",
                "quotes": [],
            }
            for line, answer in zip(rubric, answers, strict=True)
        ]
        held = sum(1 for line in out if line["pass"])
        return json.dumps({"lines": out, "score": held / len(out) if out else 1.0})

    @staticmethod
    def _analysis(instructions: str) -> dict[str, Any]:
        if instructions == TRY_INSTRUCTIONS:
            return {
                "summary": "scripted review",
                "cause": "scripted cause",
                "evidence": [],
                "fix": "scripted fix",
                "suspected": "app",
            }
        if instructions == RUN_INSTRUCTIONS:
            return {"summary": "scripted run review", "patterns": []}
        if instructions == REPORT_INSTRUCTIONS:
            return {"causes": [], "next": []}
        raise AssertionError("a model call the proof does not know")


class StubLLM:
    """The part of ``LLM`` the runner uses: every model key is the one judge."""

    def __init__(self, judge: StubJudge) -> None:
        self.judge = judge

    def caller(self, key: str) -> StubJudge:
        return self.judge

    def model_id(self, key: str) -> str:
        return MODEL


# The values each library test is filled with

#: Every line carries the evidence tag ``library.adapt`` asks for.
PROFILE = """\
Helps a small team draft updates and plan trips [admin]
Keeps long-term memory of what the person tells it [admin]
Long chats are compacted into a checkpoint [admin]
"""
FACT_WORD, NEW_FACT_WORD, DISTRACTOR_WORD = "Lisbon", "Porto", "Madrid"
EFFECT_WORDS = "In short:"
_FROM_PROFILE = "draft updates and plan trips"
VALUES: dict[str, Any] = {
    "topic": {"value": "the team's updates", "evidence": _FROM_PROFILE},
    "ask": {"value": "Draft a two-line update for the team.", "evidence": _FROM_PROFILE},
    "task": {"value": "plan a three-day trip", "evidence": _FROM_PROFILE},
    "filler_topic": {"value": "the week ahead", "evidence": _FROM_PROFILE},
    "fact": f"Our office moved to {FACT_WORD} in March.",
    "fact_word": FACT_WORD,
    "fact_question": "Where did our office go?",
    "new_fact": f"Our office moved to {NEW_FACT_WORD} in March.",
    "new_fact_word": NEW_FACT_WORD,
    "distractor": f"Our warehouse moved to {DISTRACTOR_WORD} in June.",
    "distractor_word": DISTRACTOR_WORD,
    "preference": f'end every answer with a line starting "{EFFECT_WORDS}"',
    "effect": f'ends with a line starting "{EFFECT_WORDS}"',
    "name": "Ana",
    "role": "the head of sales",
    "n": 12,
    "seconds": 3_600,
}


class Rig:
    """One target (the scripted agent), its set, the real runner and the stub judge, in *root*."""

    def __init__(
        self,
        root: Path,
        clock: Clock,
        faults: Iterable[str] = (),
        *,
        shape: str = "openinference",
        judge: StubJudge | None = None,
        check_adapters: bool = False,
        traces_options: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.faults = frozenset(faults)
        traces: dict[str, Any] = {**(traces_options or {})}
        block = {
            "adapter": "scripted_agent",
            "faults": sorted(self.faults),
            "shape": shape,
            **(options or {}),
        }
        (root / "evalution.yaml").write_text(
            yaml.safe_dump(
                {
                    "library_dir": str(LIBRARY),
                    "traces": traces,
                    "check_adapters": check_adapters,
                    "targets": {TARGET: block},
                }
            )
        )
        self.config_file = root / "evalution.yaml"
        self.config = load_config(self.config_file)
        self.targets = Targets(self.config)
        self.judge = judge or StubJudge(self.faults)
        self.runs = Runs(self.targets, StubLLM(self.judge))  # type: ignore[arg-type]
        self.docs = self.targets.docs
        save_set(self.docs, TARGET, SET, {"name": "Proof"})
        self.agent: ScriptedAgent = self.targets.adapter(TARGET)  # type: ignore[assignment]
        # Its spans reach nothing evalution reads.
        sent_to = None if "sends_no_traces" in self.faults else self.targets.store
        self.agent.wire(clock, sent_to)

    def fill(self, template_id: str) -> str:
        """Library test *template_id* through the real fit and fill paths, saved as a case."""
        template = library.find(LIBRARY, template_id)
        capabilities = self.agent.capabilities
        fit = library.fit(
            template, set(capabilities.names()), capabilities.state_checks, True, PROFILE
        )
        if fit["verdict"] != "fits":
            raise RuntimeError(f"{template_id} doesn't fit the scripted agent: {fit}")
        filled = library.adapt(template, VALUES, PROFILE)
        return save_case(self.docs, TARGET, SET, filled["case"])

    def add(self, case: dict[str, Any]) -> str:
        return save_case(self.docs, TARGET, SET, case)

    async def run(self, tries: int | None = None) -> dict[str, Any]:
        """Every case of the set, played by the real runner; the stored run document."""
        estimate = self.runs.estimate(TARGET, SET, None, tries)
        started = await self.runs.start(estimate["token"])
        await self.runs.wait()
        doc = self.runs.load(started["run"])
        assert doc is not None
        return doc
