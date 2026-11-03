"""The evals agent (docs/architecture.md "Knowing a target" and
"Drift"): a tool loop that proposes cases for a target, adds them once the admin approves, keeps
the target's profile, reads drift and reviews runs. It never runs anything: the admin does.

* **Sessions** are JSON files next to the runs (:class:`evalution.writer.sessions.SessionFiles`),
  one per target and set. Besides the contract's fields, a session keeps the model transcript, the
  cases it wrote, and when it last showed a plan.
* **The loop** runs in a background task on the strong model of evalution's own connection
  (:mod:`evalution.llm`). It ends when the model ends its turn, on Stop, after
  :data:`MAX_STEPS` tool calls or :data:`MAX_SECONDS`, or when the model asks for the same call
  :data:`SAME_CALL_TIMES` times in a row. A person can always say go on.
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
from evalution.core.case import CASE_SUFFIX
from evalution.llm import LLM, cut, fenced
from evalution.project import drift
from evalution.project.profile import read_profile, required_clean
from evalution.runs.runner import Runs
from evalution.suite.cases import CaseError, list_sets, load_case
from evalution.writer.prompts import INSTRUCTIONS
from evalution.writer.schemas import TOOLS
from evalution.writer.sessions import SessionFiles
from evalution.writer.tools import HANDLERS, OUTPUT_CHARS, AgentTurn, Tools, activity_line, say

_logger = logging.getLogger(__name__)

#: Tool calls one admin message may lead to.
MAX_STEPS = 40
#: How long one admin message may keep it working.
MAX_SECONDS = 20 * 60
#: The same call this many times in a row is a loop: the last isn't run.
SAME_CALL_TIMES = 3
#: Activity lines the page gets: what the agent did last, newest at the end.
ACTIVITY_SHOWN = 12
#: Past this, the oldest tool results in the transcript are cut, then the oldest exchanges.
HISTORY_CHARS = 300_000
EARLIER_CUT = "(Earlier conversation was cut to save room.)"
#: The set's cases go into the instructions in full up to this; past it, by name.
CASES_CHARS = 30_000
PROFILE_CHARS = 20_000


class AgentBusyError(Exception):
    """The session is working; a new message waits until it is not."""


def public(session: Mapping[str, Any]) -> dict[str, Any]:
    """The ``AgentSession`` of *session*."""
    return {
        "id": session["id"],
        "target": session["target"],
        "set": session["set"],
        "status": session["status"],
        "spent_usd": round(float(session["model_usd"]), 4),
        "messages": session["messages"],
        "activity": session.get("activity", [])[-ACTIVITY_SHOWN:],
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


def _trim(history: list[dict[str, Any]]) -> None:
    """Get the transcript under :data:`HISTORY_CHARS`: the oldest tool results go first, then the
    oldest exchanges whole, so that it still starts on a person's message. The page's chat is kept
    apart and loses nothing."""
    size = len(json.dumps(history, ensure_ascii=False))
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
        size = len(json.dumps(history, ensure_ascii=False))


class Agent:
    """The agent sessions of this server, and their running loops."""

    def __init__(self, runs: Runs, files: SessionFiles, llm: LLM | None) -> None:
        self.runs = runs
        self.targets = runs.targets
        self.files = files
        self.llm = llm
        self.tools = Tools(runs, self._save)
        self._live: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    # Sessions

    async def _save(self, session: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(session)
        async with self._lock:
            await asyncio.to_thread(self.files.write, snapshot)

    async def create(self, target: str, set_name: str) -> dict:
        """Open a session on *set_name* of *target*.

        :raises LookupError: An unknown target or set.
        """
        if set_name not in list_sets(self.targets.sets(target)):
            raise LookupError(f"no set {set_name}")
        session: dict[str, Any] = {
            "id": f"a{secrets.token_hex(8)}",
            "target": target,
            "set": set_name,
            "created_at": time.time(),
            "status": "idle",
            "model_usd": 0.0,
            "messages": [],
            "cases": [],
            "history": [],
        }
        await self._save(session)
        return public(session)

    async def _session(self, session_id: str) -> dict[str, Any]:
        """:raises LookupError: No such session."""
        session = self._live.get(session_id) or await asyncio.to_thread(self.files.load, session_id)
        if session is None:
            raise LookupError(session_id)
        # Its loop went with an earlier server process, or stopped on a spending limit, now gone.
        if session["status"] in ("working", "waiting_budget") and session_id not in self._tasks:
            session["status"] = "idle"
        return session

    async def get(self, session_id: str) -> dict[str, Any]:
        return public(await self._session(session_id))

    async def message(self, session_id: str, text: str, run: str | None = None) -> dict[str, Any]:
        """Add the admin's message and start the agent on it; while it works, the message is
        read at its next step. *run* is the run it is about: the agent is told, and it stays out
        of what the admin is shown to have said.

        :raises LookupError: No such session.
        """
        session = await self._session(session_id)
        await self._ask(session, text, f"{text}\n\n(This is about run {run}.)" if run else text)
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
        await self._ask(
            session,
            "Add it to the tests",
            f"The admin approved plan {plan_id}. Add its case with save_case(plan_id={plan_id!r}).",
            approves=plan_id,
        )
        return public(session)

    async def _ask(self, session: dict[str, Any], shown: str, text: str, **extra: Any) -> None:
        """The admin says *shown* (in the thread); the agent reads *text*, now or at its next
        step while it works."""
        session["asks"] = int(session.get("asks", 0)) + 1
        say(session, "admin", shown, **extra)
        if session["status"] == "working":
            session.setdefault("inbox", []).append(text)
        else:
            self._start(session, text)
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

    def _start(self, session: dict[str, Any], text: str) -> None:
        session["status"] = "working"
        self._live[session["id"]] = session
        self._tasks[session["id"]] = asyncio.get_running_loop().create_task(
            self._work(session, text)
        )

    def _instructions(self, session: Mapping[str, Any]) -> str:
        """The instructions, with this session's target, set, profile and cases."""
        root = self.targets.sets(session["target"])
        cases, broken = [], []
        for path in sorted((root / session["set"]).glob(f"*{CASE_SUFFIX}")):
            stem = path.name[: -len(CASE_SUFFIX)]
            try:
                cases.append(load_case(session["set"], stem, root))
            except CaseError as exc:  # one bad file must not stop the agent on its whole set
                broken.append(f"{stem}: does not load ({cut(str(exc), 200)})")
        full = "\n\n".join(f"{c.case}:\n{json.dumps(c.raw, ensure_ascii=False)}" for c in cases)
        full = "\n".join([full, *broken]).strip()
        if len(full) > CASES_CHARS:
            full = "\n".join(f"{c.case}: {c.name}" for c in cases) + "\n(read_case for one)"
        profile = read_profile(self.targets, session["target"])
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
            + f"Spent ${session['model_usd']:.2f}. "
            f"Drift: {changes} change(s) since the profile.\n\n"
            + fenced("the target's profile", cut(profile, PROFILE_CHARS) or "(none yet)")
            + "\n\n"
            + fenced("the set's cases", full or "(none yet)")
        )

    async def _work(self, session: dict[str, Any], text: str) -> None:
        history: list[dict[str, Any]] = session["history"]
        _repair(history)
        history.append({"role": "user", "content": text})
        turn = AgentTurn(session)
        steps = 0
        last_call, same = None, 0
        limit = asyncio.timeout(MAX_SECONDS)
        try:
            if not self.llm:
                say(session, "agent", "No model is set up yet: choose one in Settings, Model.")
                session["status"] = "idle"
                return
            instructions = await asyncio.to_thread(self._instructions, session)
            async with limit:
                while True:
                    _trim(history)
                    reply = await self.llm.chat(
                        "strong", instructions, copy.deepcopy(history), TOOLS
                    )
                    session["model_usd"] = float(session["model_usd"]) + reply.usd
                    history.append(
                        {"role": "assistant", "content": reply.text, "tool_calls": reply.tool_calls}
                    )
                    if not reply.tool_calls:
                        if reply.text and not turn.replied:
                            say(session, "agent", reply.text)
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
                            break
                        output = await self._call(turn, call["name"], call["arguments"])
                        history.append(
                            {"role": "tool", "tool_call_id": call["id"], "content": output}
                        )
                        steps += 1
                        if steps >= MAX_STEPS:
                            stopped = f"I stopped after {MAX_STEPS} steps. Say go on."
                            break
                    for said in session.pop("inbox", []):
                        history.append(
                            {"role": "user", "content": f"The admin says, while you work: {said}"}
                        )
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
            # Saved before it leaves the live ones: a reader then goes to the file, so that must
            # already hold this turn.
            await asyncio.shield(self._save(session))
            if self._tasks.get(session["id"]) is asyncio.current_task():
                self._tasks.pop(session["id"], None)
                self._live.pop(session["id"], None)

    async def _call(self, turn: AgentTurn, name: str, args: dict[str, Any]) -> str:
        """Run tool *name*; its result for the model, fenced as data unless it is a refusal."""
        try:
            handler = HANDLERS.get(name)
            if handler is None:
                return f"Error: no tool {name}"
            if doing := activity_line(name, args):
                activity = turn.session.setdefault("activity", [])
                activity.append({"at": time.time(), "text": doing})
                del activity[: -ACTIVITY_SHOWN * 4]
                await self._save(turn.session)
            result = await handler(self.tools, turn, args)
        except (CaseError, CodeError, ValueError, LookupError, TypeError, OSError) as exc:
            return f"Error: {cut(str(exc), 500)}"
        if isinstance(result, str):
            return result
        text = json.dumps(result, ensure_ascii=False)
        return fenced(f"the result of {name}", cut(text, OUTPUT_CHARS))
