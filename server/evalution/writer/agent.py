"""The evals agent (docs/architecture.md "Knowing a target" and
"Drift"): a tool loop that proposes cases for a target, adds them once the admin approves, keeps
the target's profile, reads drift and reviews runs. It never runs anything: the admin does.

* **Sessions** are documents in the workspace's storage
  (:class:`evalution.writer.sessions.SessionStore`), one per target and set. Besides the
  contract's fields, a session keeps the model transcript, the cases it wrote, and when it last
  showed a plan.
* **The loop** runs in a background task on the strong model of evalution's own connection
  (:mod:`evalution.llm`). It ends when the model ends its turn, on Stop, after
  :data:`MAX_STEPS` tool calls or :data:`MAX_SECONDS`, or when the model asks for the same call
  :data:`SAME_CALL_TIMES` times in a row. A person can always say go on.
* **Learning** (the page's "Learn" and "Learn again", :mod:`evalution.writer.learn`) runs on a
  transcript of its own: its brief and its steps, never the thread's earlier turns. The thread
  keeps only what was asked and what the writer said. It calls no model when nothing changed.
* **Each step resends** the instructions and the transcript, so the call asks the provider to
  cache that prefix, a file read again unchanged is not sent again, and each turn's calls,
  characters, tokens and time are kept on the session (:mod:`evalution.writer.usage`).
* **A long chat** is summarized past :data:`HISTORY_CHARS`: the older exchanges become one summary
  written by the light model, the newest stay word for word. If that fails, they are cut instead.
  The page's chat is kept apart and loses nothing.
* **Tools** (:mod:`evalution.writer.tools`) run in process over the functions the routes use; the
  model speaks only through ``reply``, so the page gets ``plan`` and ``review`` as data. Every tool
  result reaches the model fenced as data. Code is read only through the target's ``code`` scope
  (:mod:`evalution.code`).
* **Approval** is explicit. Every plan the agent shows gets an id; ``save_case`` names one and
  is refused unless that plan was approved and not used yet. The page's "Add to tests" approves
  it (:meth:`Agent.approve`); in words, the agent calls ``approve(plan_id)`` only when the admin
  clearly said so, and only after the admin spoke since the plan. A question never approves.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import secrets
import time
from collections.abc import Mapping
from typing import Any

from evalution.code import CodeError
from evalution.config import ConfigError
from evalution.llm import LLM, LLMError, Reply, cut, fenced
from evalution.project import drift, understanding
from evalution.project.profile import required_clean
from evalution.runs.runner import Runs
from evalution.suite.cases import CaseError, meaning_changes, parse_case
from evalution.suite.store import case_stems, list_sets, load_case
from evalution.writer import learn
from evalution.writer.linecheck import swapped
from evalution.writer.prompts import INSTRUCTIONS, SUMMARY_INSTRUCTIONS
from evalution.writer.schemas import TOOLS
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import HANDLERS, OUTPUT_CHARS, AgentTurn, Tools, activity_line, say
from evalution.writer.usage import TurnUsage

_logger = logging.getLogger(__name__)

#: Tool calls one admin message may lead to.
MAX_STEPS = 40
#: Tool calls a learn may make before it is told to save: its brief holds what it needs.
LEARN_READS = 4
#: Output tokens a step of the writer may use: a whole understanding is saved in one call.
WRITER_TOKENS = 32_000
#: The answer to a call that came cut at the output limit.
CUT_OFF = (
    "Not run: your reply was cut off at the output limit, so this call may be missing its end. "
    "Save in two steps: write_understanding with the sections and nodes first, then "
    "patch_understanding with the rest."
)
SAVE_NOW = (
    "You have read enough: write the understanding now, in one write_understanding call, from "
    "the brief and what you read."
)
#: Failed learns kept on a session for diagnosis, and how much of each.
FAILED_KEPT = 3
FAILED_STEPS = 20
FAILED_CHARS = 2_000
#: How long one admin message may keep it working.
MAX_SECONDS = 20 * 60
#: The same call this many times in a row is a loop: the last isn't run.
SAME_CALL_TIMES = 3
#: Activity lines the page gets: what the agent did last, newest at the end.
ACTIVITY_SHOWN = 12
#: Past this, the older exchanges in the transcript are summarized.
HISTORY_CHARS = 300_000
#: What stays word for word when they are.
KEEP_CHARS = 100_000
#: Each message's share of what the summary is written from.
DIGEST_CHARS = 2_000
SUMMARY_TOKENS = 1_500
EARLIER_SAID = "(Earlier conversation, summarized to save room.)"
EARLIER_CUT = "(Earlier conversation was cut to save room.)"
#: The set's cases go into the instructions in full up to this; past it, by name.
CASES_CHARS = 30_000
PROFILE_CHARS = 60_000


class AgentBusyError(Exception):
    """The session is working; a new message waits until it is not."""


def public(session: Mapping[str, Any]) -> dict[str, Any]:
    """The ``AgentSession`` of *session*."""
    return {
        "id": session["id"],
        "target": session["target"],
        "set": session["set"],
        "status": session["status"],
        "messages": session["messages"],
        "activity": session.get("activity", [])[-ACTIVITY_SHOWN:],
        "usage": session.get("usage", []),
        "failed_learns": session.get("failed_learns", []),
    }


def _repair(history: list[dict[str, Any]]) -> None:
    """Give every tool call left without a result (the loop was stopped) one, so the transcript
    reads as a valid conversation."""
    answered = {m["tool_call_id"] for m in history if m.get("role") == "tool"}
    for index in range(len(history) - 1, -1, -1):
        for call in reversed(history[index].get("tool_calls") or []):
            if call["id"] not in answered:
                history.insert(
                    index + 1,
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": "(stopped before it finished)",
                    },
                )


def _size(history: list[dict[str, Any]]) -> int:
    return len(json.dumps(history, ensure_ascii=False))


def _fold_point(history: list[dict[str, Any]]) -> int | None:
    """Where the words kept start: a person's message, the earliest that leaves at most
    :data:`KEEP_CHARS`, and always the newest. ``None`` when nothing before it is left to
    summarize."""
    start = 1 if str(history[0].get("content")).startswith(EARLIER_SAID) else 0
    people = [i for i, m in enumerate(history) if i > start and m.get("role") == "user"]
    for index in people:
        if _size(history[index:]) <= KEEP_CHARS:
            return index
    return people[-1] if people else None


def _digest(messages: list[dict[str, Any]]) -> str:
    """*messages* as the summary reads them: an earlier summary whole, the rest short."""
    lines = []
    for message in messages:
        text = str(message.get("content") or "")
        if text.startswith(EARLIER_SAID):
            lines.append(text)
            continue
        who = {"user": "Admin", "assistant": "Agent"}.get(message["role"], "Tool result")
        if text:
            lines.append(f"{who}: {cut(text, DIGEST_CHARS)}")
        for call in message.get("tool_calls") or []:
            args = json.dumps(call["arguments"], ensure_ascii=False)
            lines.append(f"Agent called {call['name']}: {cut(args, DIGEST_CHARS)}")
    return "\n".join(lines)


def _trim(history: list[dict[str, Any]]) -> None:
    """Get the transcript under :data:`HISTORY_CHARS` when a summary didn't: the oldest tool results
    go first, then the oldest exchanges whole, so that it still starts on a person's message."""
    size = _size(history)
    for message in history:
        if size <= HISTORY_CHARS:
            return
        if message.get("role") == "tool" and len(message["content"]) > 40:
            size -= len(message["content"])
            message["content"] = "(cut to save room)"
    while size > HISTORY_CHARS:
        start = 1 if history[0].get("content") == EARLIER_CUT else 0
        later = [i for i, m in enumerate(history) if i > start and m.get("role") == "user"]
        if not later:
            return
        history[: later[0]] = [{"role": "user", "content": EARLIER_CUT}]
        size = _size(history)


def _refused(output: str) -> bool:
    return output.startswith(("Refused", "Error"))


def _keep_failed(session: dict[str, Any], history: list[dict[str, Any]]) -> None:
    """Keep the last steps of a learn that saved nothing, capped, so why can be read later."""
    steps = []
    for message in history[-FAILED_STEPS:]:
        entry: dict[str, Any] = {"role": message["role"]}
        if message.get("content"):
            entry["content"] = cut(str(message["content"]), FAILED_CHARS)
        if message.get("tool_calls"):
            entry["tool_calls"] = cut(json.dumps(message["tool_calls"]), FAILED_CHARS)
        steps.append(entry)
    kept = session.setdefault("failed_learns", [])
    kept.append({"at": time.time(), "steps": steps})
    del kept[:-FAILED_KEPT]


def _doing(session: dict[str, Any], text: str) -> None:
    """Add *text* to the activity the page shows."""
    activity = session.setdefault("activity", [])
    activity.append({"at": time.time(), "text": text})
    del activity[: -ACTIVITY_SHOWN * 4]


class Agent:
    """The agent sessions of this server, and their running loops."""

    def __init__(self, runs: Runs, sessions: SessionStore, llm: LLM | None) -> None:
        self.runs = runs
        self.targets = runs.targets
        self.sessions = sessions
        self.llm = llm
        self.tools = Tools(runs, self._save)
        self._live: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    # Sessions

    async def _save(self, session: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(session)
        async with self._lock:
            await asyncio.to_thread(self.sessions.write, snapshot)

    async def create(self, target: str, set_name: str) -> dict:
        """Open a session on *set_name* of *target*.

        :raises LookupError: An unknown target or set.
        """
        if set_name not in list_sets(self.targets.docs, target):
            raise LookupError(f"no set {set_name}")
        session: dict[str, Any] = {
            "id": f"a{secrets.token_hex(8)}",
            "target": target,
            "set": set_name,
            "created_at": time.time(),
            "status": "idle",
            "messages": [],
            "cases": [],
            "history": [],
        }
        await self._save(session)
        return public(session)

    async def _session(self, session_id: str) -> dict[str, Any]:
        """:raises LookupError: No such session."""
        session = self._live.get(session_id) or await asyncio.to_thread(
            self.sessions.load, session_id
        )
        if session is None:
            raise LookupError(session_id)
        # Its loop went with an earlier server process; "waiting_budget" is a status of older ones.
        if session["status"] in ("working", "waiting_budget") and session_id not in self._tasks:
            session["status"] = "idle"
        return session

    async def get(self, session_id: str) -> dict[str, Any]:
        return public(await self._session(session_id))

    async def message(
        self, session_id: str, text: str, run: str | None = None, *, learn: bool = False
    ) -> dict[str, Any]:
        """Add the admin's message and start the agent on it; while it works, the message is
        read at its next step. *run* is the run it is about: the agent is told, and it stays out
        of what the admin is shown to have said. *learn*: the message asks it to learn the target
        again, which starts from what changed.

        :raises LookupError: No such session.
        """
        session = await self._session(session_id)
        said = f"{text}\n\n(This is about run {run}.)" if run else text
        await self._ask(session, text, said, learn=learn)
        return public(session)

    async def approve(self, session_id: str, plan_id: str) -> dict[str, Any]:
        """The admin approves plan *plan_id* (the page's "Add to tests"): the agent is told to
        add its case.

        :raises LookupError: No such session or plan.
        """
        session = await self._session(session_id)
        plan = (session.get("plans") or {}).get(plan_id)
        if plan is None:
            raise LookupError(plan_id)
        plan["approved"] = "admin"
        edited = (
            " The admin swapped rubric lines on the page first: save_case(plan_id) alone saves the "
            "test as it is now; read_case shows it after."
            if plan.get("edited")
            else ""
        )
        await self._ask(
            session,
            "Add it to the tests",
            f"The admin approved plan {plan_id}. Add its case with save_case(plan_id={plan_id!r})."
            + edited,
            approves=plan_id,
        )
        return public(session)

    async def use_line(self, session_id: str, plan_id: str, line: str) -> dict[str, Any]:
        """The admin takes the suggestion flagged on rubric *line* of plan *plan_id* (the page's
        "Use this"): the plan's test changes in place, before anyone approved it, so approval still
        covers exactly what the admin saw.

        :raises LookupError: No such session, plan or flagged line.
        :raises ValueError: The plan was approved already.
        """
        session = await self._session(session_id)
        plan = (session.get("plans") or {}).get(plan_id)
        if plan is None:
            raise LookupError(plan_id)
        if plan.get("approved") or plan.get("saved"):
            raise ValueError("that plan was approved already")
        weak = plan.get("weak_lines") or []
        flag = next((f for f in weak if f["line"] == line and f.get("use")), None)
        if flag is None:
            raise LookupError(line)
        file = swapped(plan["file"], line, flag["use"])
        parse_case(file, "plan.file")
        plan["file"], plan["edited"] = file, True
        use = flag["use"]
        put = use["line"] if "line" in use else json.dumps({"reply": use}, ensure_ascii=False)
        # The writer reads it at its next step, or at the start of its next turn: it plans on.
        session.setdefault("inbox", []).append(
            f"The admin changed plan {plan_id} on the page: rubric line {line!r} is now "
            + fenced("what replaced it", put)
        )
        plan["weak_lines"] = [f for f in weak if f is not flag]
        said = (m.get("plan") or {} for m in session["messages"])
        shown = next((p for p in said if p.get("id") == plan_id), {})
        shown["weak_lines"] = plan["weak_lines"]
        if plan.get("replaces") and shown:
            old = await asyncio.to_thread(
                load_case, self.targets.docs, session["target"], session["set"], plan["replaces"]
            )
            shown["changes"] = meaning_changes(old.raw, file)
        await self._save(session)
        return public(session)

    async def _ask(
        self, session: dict[str, Any], shown: str, text: str, *, learn: bool = False, **extra: Any
    ) -> None:
        """The admin says *shown* (in the thread); the agent reads *text*, now or at its next
        step while it works (a learn asked meanwhile is then an ordinary message)."""
        session["asks"] = int(session.get("asks", 0)) + 1
        say(session, "admin", shown, **extra)
        if session["status"] == "working":
            session.setdefault("inbox", []).append(text)
        else:
            self._start(session, text, learn)
        await self._save(session)

    async def stop(self, session_id: str) -> dict[str, Any]:
        session = await self._session(session_id)
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        session["status"] = "stopped"
        await self._save(session)
        return public(session)

    # The loop

    def _start(self, session: dict[str, Any], text: str, learn: bool = False) -> None:
        session["status"] = "working"
        self._live[session["id"]] = session
        self._tasks[session["id"]] = asyncio.get_running_loop().create_task(
            self._work(session, text, learn)
        )

    def _instructions(self, session: Mapping[str, Any], learning: bool = False) -> str:
        """The instructions, with this session's target, set, profile and cases. *learning*: a
        learn's brief carries what it understood, so the profile is left out and the cases go by
        name; resent at every step, they would cost more than the work."""
        docs, target, set_name = self.targets.docs, session["target"], session["set"]
        cases, broken = [], []
        for stem in case_stems(docs, target, set_name):
            try:
                cases.append(load_case(docs, target, set_name, stem))
            except CaseError as exc:  # one bad file must not stop the agent on its whole set
                broken.append(f"{stem}: does not load ({cut(str(exc), 200)})")
        full = "\n\n".join(f"{c.case}:\n{json.dumps(c.raw, ensure_ascii=False)}" for c in cases)
        full = "\n".join([full, *broken]).strip()
        if learning or len(full) > CASES_CHARS:
            full = "\n".join(f"{c.case}: {c.name}" for c in cases) + "\n(read_case for one)"
        profile = understanding.writer_profile(self.targets, session["target"])
        changes = len(drift.read(self.targets, session["target"]).get("changes") or [])
        adapter = self.targets.conf(session["target"]).adapter
        clean: list[str] = []
        try:
            declared = self.targets.adapter(session["target"]).capabilities
            can = ", ".join(declared.names())
            clean = sorted(declared.starts_clean())
        except (ConfigError, ImportError):
            can = ""
        required = required_clean(self.targets, session["target"])
        return (
            f"{INSTRUCTIONS}\nThis session's target is {session['target']!r} (adapter "
            f"{adapter!r}, capabilities: {can or 'none'}; a try starts clean of "
            f"{', '.join(clean) or 'nothing it can promise'}), its set {session['set']!r}. "
            + (
                f"The admin asks every test to start clean of {', '.join(required)}: put that in "
                "needs_clean. "
                if required
                else ""
            )
            + f"Drift: {changes} change(s) since the profile.\n\n"
            + (
                ""
                if learning
                else fenced("the target's profile", cut(profile, PROFILE_CHARS) or "(none yet)")
                + "\n\n"
            )
            + fenced("the set's cases", full or "(none yet)")
        )

    async def _chat(
        self,
        usage: TurnUsage,
        key: str,
        system: str,
        messages: list[dict[str, Any]],
        *,
        light: bool = False,
        **kw: Any,
    ) -> Reply:
        """One model call of this turn, counted; *light*: one of the learn's file summaries."""
        assert self.llm is not None
        reply = await self.llm.chat(key, system, messages, **kw)
        tools = kw.get("tools") or ()
        usage.add(
            len(system) + _size(messages) + (_size(list(tools)) if tools else 0), reply, light
        )
        return reply

    async def _work(self, session: dict[str, Any], text: str, learn_asked: bool = False) -> None:
        thread: list[dict[str, Any]] = session["history"]
        _repair(thread)
        # What reached the inbox while it was idle (a plan changed on the page) comes first.
        asked = "\n\n".join([*session.pop("inbox", []), text])
        # A learn starts from its brief alone, never from the thread's earlier turns (an earlier
        # learn's brief among them): the thread only gets what was asked and said, at the end.
        history = [{"role": "user", "content": asked}] if learn_asked else thread
        if learn_asked:
            session.pop("code_seen", None)
        else:
            thread.append({"role": "user", "content": asked})
        said_from = len(session["messages"])
        turn = AgentTurn(session)
        usage = TurnUsage(learn=learn_asked)
        steps = 0
        last_call, same = None, 0
        output = ""
        limit = asyncio.timeout(MAX_SECONDS)
        try:
            if not self.llm:
                say(session, "agent", "No model is set up yet: choose one in Settings, Model.")
                session["status"] = "idle"
                return
            async with limit:
                if learn_asked:
                    light = "light" if self.llm.model_id("light") else "strong"

                    async def ask(system: str, text: str) -> str:
                        said = [{"role": "user", "content": text}]
                        return (await self._chat(usage, light, system, said, light=True)).text

                    _doing(session, "Reading the code")
                    begun = await learn.begin(self.targets, turn, ask)
                    usage.note(begun.stats)
                    if begun.nothing:
                        say(session, "agent", begun.nothing)
                        session["status"] = "idle"
                        return
                    if begun.text:
                        history[-1]["content"] += "\n\n" + begun.text
                instructions = await asyncio.to_thread(self._instructions, session, learn_asked)
                while True:
                    if _size(history) > HISTORY_CHARS:
                        await self._fold(session, history, usage)
                        _trim(history)
                        # What was read before may be gone from the transcript now.
                        session.pop("code_seen", None)
                    sent = copy.deepcopy(history)
                    reply = await self._chat(
                        usage,
                        "strong",
                        instructions,
                        sent,
                        tools=TOOLS,
                        cache=True,
                        max_tokens=WRITER_TOKENS,
                    )
                    history.append(
                        {"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls}
                    )
                    if reply.cut and reply.tool_calls:
                        # Cut at the output limit: its calls may have lost their ends (a save
                        # without its nodes), so none runs, and it is told why.
                        for call in reply.tool_calls:
                            history.append(
                                {"role": "tool", "tool_call_id": call["id"], "content": CUT_OFF}
                            )
                        continue
                    if not reply.tool_calls:
                        if reply.text and not turn.replied:
                            say(session, "agent", reply.text)
                        if learn_asked and (
                            left := await asyncio.to_thread(learn.left_out, self.targets, turn)
                        ):
                            history.append({"role": "user", "content": left})
                            continue
                        break
                    stopped = None
                    for call in reply.tool_calls:
                        this = json.dumps([call["name"], call["arguments"]], sort_keys=True)
                        same = same + 1 if this == last_call else 1
                        last_call = this
                        if same >= SAME_CALL_TIMES:
                            stopped = (
                                "I stopped: I kept repeating the same step. What should I try?"
                            )
                            # Say why, when the step kept being refused: that is what to fix.
                            if _refused(output):
                                stopped += f" It was refused: {cut(output, 600)}"
                            break
                        usage.step(call["name"])
                        output = await self._call(turn, call["name"], call["arguments"])
                        history.append(
                            {"role": "tool", "tool_call_id": call["id"], "content": output}
                        )
                        steps += 1
                        if steps >= MAX_STEPS:
                            stopped = f"I stopped after {MAX_STEPS} steps. Say go on."
                            break
                    # A learn has its brief: past a few lookups, every step resends it all.
                    reads = learn_asked and not turn.understood and steps >= LEARN_READS
                    if reads and not turn.nudged:
                        turn.nudged = True
                        history.append({"role": "user", "content": SAVE_NOW})
                    for said in session.pop("inbox", []):
                        history.append(
                            {"role": "user", "content": f"The admin says, while you work: {said}"}
                        )
                    if learn_asked and turn.patch is not None and turn.understood and not stopped:
                        # A patch saved ends the learn, its note the reply, unless the code still
                        # names what it left out: then once more.
                        left = await asyncio.to_thread(learn.left_out, self.targets, turn)
                        if not left:
                            say(session, "agent", turn.note or "I've updated what I understood.")
                            break
                        turn.understood = False
                        history.append({"role": "user", "content": left})
                    await self._save(session)
                    if stopped:
                        say(session, "agent", stopped)
                        break
            session["status"] = "idle"
        except asyncio.CancelledError:
            session["status"] = "stopped"
            raise
        except Exception as exc:
            if limit.expired():
                minutes = round(MAX_SECONDS / 60)
                say(session, "agent", f"I stopped after {minutes} minutes. Say go on.")
            else:
                _logger.warning("agent: session %s failed", session["id"], exc_info=True)
                say(session, "agent", f"Something went wrong: {cut(str(exc), 300)}")
            session["status"] = "idle"
        finally:
            usage.record(session)
            if learn_asked and not turn.understood:
                _keep_failed(session, history)
            if learn_asked:
                # What it read is in the learn's transcript, not the thread's.
                session.pop("code_seen", None)
                new = session["messages"][said_from:]
                replied = "\n".join(m["text"] for m in new if m["role"] == "agent")
                thread += [
                    {"role": "user", "content": asked},
                    {"role": "assistant", "content": replied or "(learned)", "tool_calls": []},
                ]
            # Saved before it leaves the live ones: a reader then goes to the file, so that must
            # already hold this turn.
            await asyncio.shield(self._save(session))
            if self._tasks.get(session["id"]) is asyncio.current_task():
                self._tasks.pop(session["id"], None)
                self._live.pop(session["id"], None)

    async def _fold(
        self, session: dict[str, Any], history: list[dict[str, Any]], usage: TurnUsage
    ) -> None:
        """Past :data:`HISTORY_CHARS`, put the older exchanges into one summary, which the next one
        folds in. A failed call leaves the history as it was, for :func:`_trim`."""
        if _size(history) <= HISTORY_CHARS or (at := _fold_point(history)) is None:
            return
        assert self.llm is not None  # _work doesn't get this far without one
        _doing(session, "Summarizing the earlier chat")
        try:
            reply = await self._chat(
                usage,
                "light" if self.llm.model_id("light") else "strong",
                SUMMARY_INSTRUCTIONS,
                [{"role": "user", "content": fenced("the conversation", _digest(history[:at]))}],
                max_tokens=SUMMARY_TOKENS,
            )
        except LLMError:
            _logger.warning("agent: session %s: no summary", session["id"], exc_info=True)
            return
        if summary := reply.text.strip():
            said = f"{EARLIER_SAID}\n{fenced('the summary', summary)}"
            history[:at] = [{"role": "user", "content": said}]

    async def _call(self, turn: AgentTurn, name: str, args: dict[str, Any]) -> str:
        """Run tool *name*; its result for the model, fenced as data unless it is a refusal."""
        try:
            handler = HANDLERS.get(name)
            if handler is None:
                return f"Error: no tool {name}"
            # Steps count across the session, so a result can point back to an earlier one.
            turn.session["steps"] = int(turn.session.get("steps") or 0) + 1
            if doing := activity_line(name, args):
                _doing(turn.session, doing)
                await self._save(turn.session)
            result = await handler(self.tools, turn, args)
        except (CaseError, CodeError, ValueError, LookupError, TypeError, OSError) as exc:
            return f"Error: {cut(str(exc), 500)}"
        if isinstance(result, str):
            return result
        text = json.dumps(result, ensure_ascii=False)
        return fenced(f"the result of {name}", cut(text, OUTPUT_CHARS))
