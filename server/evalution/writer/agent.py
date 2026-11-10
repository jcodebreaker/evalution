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
* **Learning** (the page's "Learn", "Learn again", "Learn this area" and "Learn everything",
  connect's first learn, a chat's "learn it", :mod:`evalution.writer.learn`) is a background job
  (:class:`evalution.project.learning.Learns`), not a chat turn: one per target, with no clock,
  on a session of its own (its usage and failed steps), never the thread's. A chat that asks
  for one answers one line that points to What it learned and goes on; the job outlives it. It
  calls no model when nothing changed. The writer asking for an area
  (``read_understanding(area=...)``) starts that area's learn and waits for it, bounded.
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
  it (:meth:`Agent.approve`) and saves its file there and then, with no model turn, unless a data
  file its turns send is still to be written (the writer then writes it and saves); in words,
  the agent calls ``approve(plan_id)`` only when the admin clearly said so, and only after the
  admin spoke since the plan. A question never approves.
  An isolation proposal is approved the same way, and only then can ``rehearse_isolation`` run
  it (:mod:`evalution.writer.isolating`); so is a page map, and only then can
  ``rehearse_page_map`` spend a message on it (:mod:`evalution.writer.paging`).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from typing import Any

from evalution.code import CodeError
from evalution.config import ConfigError
from evalution.llm import LLM, LLMError, Reply, cut, fenced
from evalution.project import drift, learning, understanding
from evalution.project.profile import required_clean
from evalution.runs.runner import Runs
from evalution.suite.cases import CaseError, meaning_changes, parse_case
from evalution.suite.store import case_stems, list_sets, load_case
from evalution.writer import learn
from evalution.writer.linecheck import swapped
from evalution.writer.prompts import INSTRUCTIONS, SUMMARY_INSTRUCTIONS
from evalution.writer.schemas import TOOLS
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import (
    HANDLERS,
    OUTPUT_CHARS,
    AgentTurn,
    Tools,
    activity_line,
    files_missing,
    said_lately,
    save_plan,
    say,
)
from evalution.writer.usage import TurnUsage

_logger = logging.getLogger(__name__)

#: Tool calls one admin message may lead to.
MAX_STEPS = 40
#: Output tokens a step of the writer may use: a whole understanding is saved in one call.
WRITER_TOKENS = 32_000
#: The answer to a call that came cut at the output limit.
CUT_OFF = (
    "Not run: your reply was cut off at the output limit, so this call may be missing its end. "
    "Save in two steps: write_understanding with the sections and nodes first, then "
    "patch_understanding with the rest."
)
#: Failed learns kept on a session for diagnosis, and how much of each.
FAILED_KEPT = 3
FAILED_STEPS = 20
FAILED_CHARS = 2_000
#: How long one admin message may keep it working. A learn is a job of its own, with no clock.
MAX_SECONDS = 20 * 60
#: How long the writer waits for an area it asked for to be learned; past it, it goes on with
#: what is known and the learn goes on by itself.
AREA_WAIT_SECONDS = 8 * 60
#: What a chat that asks for a learn says: the learn is a job, followed on What it learned.
LEARNING_SAID = "Learning {target}. Follow it on What it learned."
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
        "learn": session.get("learn"),
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


def _keep_failed(
    session: dict[str, Any], history: list[dict[str, Any]], area: str | None = None
) -> None:
    """Keep the last steps of a learn, or of one of its areas, that saved nothing, capped, so why
    can be read later."""
    steps = []
    for message in history[-FAILED_STEPS:]:
        entry: dict[str, Any] = {"role": message["role"]}
        if message.get("content"):
            entry["content"] = cut(str(message["content"]), FAILED_CHARS)
        if message.get("tool_calls"):
            entry["tool_calls"] = cut(json.dumps(message["tool_calls"]), FAILED_CHARS)
        steps.append(entry)
    kept = session.setdefault("failed_learns", [])
    kept.append({"at": time.time(), **({"area": area} if area else {}), "steps": steps})
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
        #: Each target's learn, a background job.
        self.learns = learning.Learns(self.targets, self._run_learn)
        self.tools.learn_area = self._learn_for_writer
        self._lock = asyncio.Lock()
        #: Told of each tool call a session's loop makes, by session id: what follows a session
        #: on the page without its chat (:mod:`evalution.writer.pagelearn`).
        self.watching: dict[str, Callable[[str, dict[str, Any]], None]] = {}

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

    async def raw(self, session_id: str) -> dict[str, Any]:
        """The whole session, its plans and work included, as the loop keeps it: read, never
        changed.

        :raises LookupError: No such session.
        """
        return await self._session(session_id)

    def learning(self, target: str) -> bool:
        """Whether a learn of *target* is going in this process: a learn's progress that says
        so with none going was cut with an earlier process."""
        return self.learns.running(target)

    def start_learn(
        self,
        target: str,
        areas: tuple[str, ...] | list[str] = (),
        *,
        everything: bool = False,
        force: bool = False,
        by: str = "page",
    ) -> learning.Job:
        """Start a learn of *target* in the background, or answer the one going (an area asked
        for meanwhile is learned by it before its overview).

        :raises LookupError: No such target.
        :raises RuntimeError: No model is set up.
        """
        if target not in self.targets.config.targets:
            raise LookupError(target)
        if not self.llm:
            raise RuntimeError("No model is set up yet: choose one in Settings, Model.")
        return self.learns.start(target, areas, everything=everything, force=force, by=by)

    async def _run_learn(self, job: learning.Job) -> None:
        """A learn job, on a session of its own: its usage (what ``learn --report`` reads), its
        failed steps and its progress are kept there, never on a chat's."""
        sets = await asyncio.to_thread(list_sets, self.targets.docs, job.target)
        session = job.session
        session.update(
            {
                "id": job.id,
                "target": job.target,
                "set": sets[0] if sets else "main",
                "created_at": job.started,
                "status": "working",
                "messages": [],
                "cases": [],
                "history": [],
                "learn_job": True,
            }
        )
        brief = f"Learn {job.target}: build its profile from its recent traces and its code."
        await self._work(session, brief, True, job)
        said = [m["text"] for m in session["messages"] if m["role"] == "agent"]
        job.reply = said[-1] if said else None
        if session.get("failed"):
            job.error = str(session["failed"])

    async def _learn_for_writer(self, target: str, area: str) -> learning.Job | None:
        """The test writer needs *area*: it is learned by a job (the one going takes it), and the
        writer waits for it, at most :data:`AREA_WAIT_SECONDS`."""
        try:
            self.start_learn(target, [area], by="writer")
        except (LookupError, RuntimeError):
            return None
        return await self.learns.wait(target, AREA_WAIT_SECONDS)

    async def close(self) -> None:
        await self.learns.close()

    def working(self, session_id: str) -> bool:
        """Whether the session's loop is going."""
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

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
        if learn:
            await self._learn_from_chat(session, text)
            return public(session)
        said = f"{text}\n\n(This is about run {run}.)" if run else text
        await self._ask(session, text, said)
        return public(session)

    async def _learn_from_chat(self, session: dict[str, Any], text: str) -> None:
        """A chat asks to learn its app: a learn job starts (or the one going answers), and the
        chat says so in one line and goes on. Its thread keeps what was asked and said."""
        target = str(session["target"])
        session["asks"] = int(session.get("asks", 0)) + 1
        say(session, "admin", text)
        try:
            self.start_learn(target, by="chat")
            said = LEARNING_SAID.format(target=target)
        except RuntimeError as exc:
            said = str(exc)
        say(session, "agent", said)
        if self.working(str(session["id"])):
            # Its loop is mid-turn: it reads this at its next step, not as a turn of its own.
            session.setdefault("inbox", []).append(f"{text} ({said})")
        else:
            session["history"] += [
                {"role": "user", "content": text},
                {"role": "assistant", "content": said, "tool_calls": []},
            ]
        await self._save(session)

    async def learn_area(self, session_id: str, area: str, force: bool = False) -> dict[str, Any]:
        """ "Learn this area" from a chat's session: a learn job of *area* alone, even when it
        weighs nothing; *force*: even when what it was learned from has not moved. The learn
        going learns it before its overview. The chat's session is answered as it is.

        :raises LookupError: No such session.
        :raises RuntimeError: No model is set up.
        """
        session = await self._session(session_id)
        self.start_learn(str(session["target"]), [area], force=force)
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
        if plan.get("isolation"):
            await self._ask(
                session,
                "Rehearse this isolation plan",
                f"The admin approved isolation plan {plan_id}. Rehearse it with "
                f"rehearse_isolation(plan_id={plan_id!r}).",
                approves=plan_id,
            )
            return public(session)
        if plan.get("page_map"):
            await self._ask(
                session,
                "Try this page map with one message",
                f"The admin approved page map {plan_id}. Try it on evalution's copy with "
                f"rehearse_page_map(plan_id={plan_id!r}): it sends one real message.",
                approves=plan_id,
            )
            return public(session)
        # A plan whose data files are still to be written goes to the writer, which writes them.
        test = plan.get("file")
        if isinstance(test, dict) and not files_missing(self.targets, session, test):
            await self._save_approved(session, plan_id, plan)
            return public(session)
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

    async def _save_approved(self, session: dict[str, Any], plan_id: str, plan: dict) -> None:
        """Save an approved plan's test with no model turn: the file can't change once approved.
        The thread says it, and the writer is told at its next step, or its next turn."""
        session["asks"] = int(session.get("asks", 0)) + 1
        say(session, "admin", "Add it to the tests", approves=plan_id)
        found = await save_plan(self.targets, session, plan_id, plan, plan.get("replaces"))
        if isinstance(found, dict):
            name = (plan.get("file") or {}).get("name") or found["case"]
            say(
                session,
                "agent",
                f"Added '{name}' to the tests as {found['case']}. It's ready to run from the page.",
                saved=found["case"],
            )
            told = f"was saved as {found['case']} in {found['set']}: nothing to do for it"
        else:
            why = found.removeprefix("Refused: ")
            say(session, "agent", f"I couldn't add it: {why}")
            told = f"couldn't be saved: {why}"
        session.setdefault("inbox", []).append(
            f"(The admin approved plan {plan_id} on the page, and it {told}.)"
        )
        await self._save(session)

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
        # An area's claims go only when the conversation names it; the rest by title.
        profile = understanding.writer_profile(
            self.targets, session["target"], said_lately(session)
        )
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

    async def _work(
        self,
        session: dict[str, Any],
        text: str,
        learn_asked: bool = False,
        job: learning.Job | None = None,
    ) -> None:
        """One turn of *session* on *text*; *learn_asked*: a learn, on the session of its *job*
        (what it asks for: areas, everything, force), with no clock."""
        thread: list[dict[str, Any]] = session["history"]
        _repair(thread)
        # What reached the inbox while it was idle (a plan changed on the page) comes first.
        asked = "\n\n".join([*session.pop("inbox", []), text])
        # A learn starts from its brief alone, never from the thread's earlier turns (an earlier
        # learn's brief among them): the thread only gets what was asked and said, at the end.
        history = [{"role": "user", "content": asked}] if learn_asked else thread
        if learn_asked:
            session.pop("code_seen", None)
            session.pop("learn", None)
        else:
            thread.append({"role": "user", "content": asked})
        said_from = len(session["messages"])
        turn = AgentTurn(session)
        usage = TurnUsage(learn=learn_asked)
        steps = 0
        last_call, same = None, 0
        output = ""
        # A learn has no clock: it ends when its areas are done or their budgets are spent.
        limit = asyncio.timeout(None if learn_asked else MAX_SECONDS)
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

                    async def chat(
                        key: str, system: str, messages: list, tools: list, tokens: int
                    ) -> Reply:
                        return await self._chat(
                            usage,
                            key,
                            system,
                            messages,
                            tools=tools,
                            max_tokens=tokens,
                            light=key == "light",
                        )

                    conf = self.targets.conf(session["target"]).code
                    wanted = (conf.area_model if conf else None) or "strong"
                    area_key = (
                        "light" if wanted == "light" and self.llm.model_id("light") else "strong"
                    )
                    _doing(session, "Reading the code")

                    def stage(name: str) -> None:
                        usage.stage(name)
                        if job is not None:
                            # What a restart finds: the stage it was cut at.
                            self.learns.stage(job)

                    begun = await learn.begin(
                        self.targets,
                        turn,
                        ask,
                        chat,
                        (area_key, "strong"),
                        stage,
                        asked=tuple(job.areas) if job else (),
                        force=bool(job and job.force),
                        everything=bool(job and job.everything),
                    )
                    usage.note(begun.stats)
                    usage.areas = begun.areas
                    for area, steps_kept in begun.failed:
                        _keep_failed(session, steps_kept, area)
                    if begun.nothing or begun.done:
                        say(session, "agent", begun.nothing or begun.done or "")
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
                if learn_asked:
                    session["failed"] = cut(str(exc) or type(exc).__name__, 300)
            session["status"] = "idle"
        finally:
            usage.record(session)
            # A learn its areas made keeps their failures itself; the writer's own is kept here.
            if learn_asked and not turn.understood and turn.map is None:
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
            if (watch := self.watching.get(turn.session["id"])) is not None:
                watch(name, args)
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
