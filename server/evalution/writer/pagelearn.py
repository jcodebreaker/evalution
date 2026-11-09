"""Setting up UI testing in one flow from the page (docs/plans/ui-testing-ease.md, 2): the test
writer learns the target's page map as it does in the chat (:mod:`evalution.writer.paging`), and
the page follows it as live steps, with no chat open.

The person presses **Set up UI testing** on the offer card or on "How tests reach", which says that
trying the map sends one message to evalution's copy of the app. That is their yes to the
rehearsal, given before the agent starts, as starting isolation is (``confirm``). A
:class:`PageLearn` then:

1. opens a writer session of its own on one of the target's sets and asks the agent to learn the
   page (or learn it again, saying why, for a map gone stale or broken);
2. follows each tool call the agent makes (:attr:`~evalution.writer.agent.Agent.watching`) as a
   step: reading the page's code, looking at the page, finding its message box, Send and replies,
   proposing a map, trying it with one message;
3. when the agent shows a map (``reply(page_map=…)``) and ends its turn, approves its rehearsal
   as the chat's button would (:meth:`~evalution.writer.agent.Agent.approve`); a failed
   rehearsal's revision is approved the same way, up to the writer's own limit;
4. ends ``done`` once a tried map waits for approval (its screenshots and video on the card, and
   one **Approve**), else ``failed`` with what the agent said last.

The map itself is still approved by the person, once, on the card. The session stays among the
target's chats, so the whole conversation can be read after.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evalution.llm import cut
from evalution.project import pagemaps
from evalution.suite.store import list_sets, own_sets, save_set
from evalution.writer.paging import MAX_REVISIONS

if TYPE_CHECKING:
    from evalution.targets import Targets
    from evalution.writer.agent import Agent

_logger = logging.getLogger(__name__)

#: Seconds between looks at the agent's session while it works.
POLL_SECONDS = 0.5
#: How long one setup may take in all, revisions included.
MAX_SECONDS = 45 * 60
#: The set a session is opened on when the target has none: the one Connect makes.
FIRST_SET = "main"
#: How much of what the agent said last a failed setup keeps.
SAID_CHARS = 1_500

CODE, LOOK, FIND, PROPOSE, REHEARSE = "code", "look", "find", "propose", "rehearse"
#: The agent's tools, as the steps the page shows.
TOOL_STEPS = {
    "read_page_code": CODE,
    "look_at_page": LOOK,
    "try_page_entry": FIND,
    "propose_page_map": PROPOSE,
    "rehearse_page_map": REHEARSE,
}
LABELS = {
    CODE: "Read {target}'s page code",
    LOOK: "Look at the page on evalution's copy",
    FIND: "Find the message box, Send and the replies",
    PROPOSE: "Propose how tests reach the page",
    REHEARSE: "Try it with one message",
}
#: What the agent is told the person agreed to, after what it's asked.
AGREED = (
    "(Asked from the page's Set up UI testing, which follows your steps without the chat. The "
    "admin agreed there that trying the map sends one real message to evalution's copy: show the "
    "map with reply(page_map=<id>) and end your turn, and the page approves its rehearsal.)"
)


def learn_words(targets: Targets, name: str) -> str:
    """What the agent is asked: to learn *name*'s page, or to learn it again, saying why."""
    kept = pagemaps.in_use(targets, name)
    if kept is not None and kept.status == "broken":
        return (
            f"The page map of {name} no longer matches its page: it has no "
            f"{', '.join(kept.missing)}. Learn the page again and propose a new map."
        )
    if kept is not None and kept.status == "stale":
        files = ", ".join(kept.stale_files) or "a file it rests on"
        return (
            f"The page map of {name} is out of date: a commit changed {files}. Learn the page "
            "again and propose a new map."
        )
    return (
        f"Learn how tests reach {name}'s page: read its page code, look at it on evalution's "
        "copy, and propose a page map."
    )


def _step(key: str, target: str) -> dict[str, Any]:
    label = LABELS[key].format(target=target)
    return {"key": key, "label": label, "state": "waiting", "seconds": None, "detail": None}


@dataclass
class PageLearn:
    """One target's page being learned, as the page follows it."""

    target: str
    #: The writer session doing it.
    session: str
    started: float = field(default_factory=time.time)
    #: ``running``, ``done`` (a tried map waits for approval) or ``failed``.
    state: str = "running"
    steps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    ended: float | None = None
    #: The maps whose rehearsal this setup approved, in order.
    approved: list[str] = field(default_factory=list)
    _began: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = [_step(key, self.target) for key in LABELS]

    def _end(self, step: dict[str, Any], state: str, now: float) -> None:
        began = self._began.pop(step["key"], None)
        if began is not None:
            step["seconds"] = round(now - began, 1)
        step["state"] = state

    def stage(self, key: str) -> None:
        """*key*'s step begins, and those before it are done (the agent may skip one: a map
        resting on code alone needs no look). Going back to an earlier step, as a revision does,
        starts it again and leaves the later ones waiting."""
        keys = [s["key"] for s in self.steps]
        if key not in keys or self.state != "running":
            return
        at = keys.index(key)
        now = time.monotonic()
        for step in self.steps[:at]:
            if step["state"] in ("waiting", "running"):
                self._end(step, "done", now)
        for step in self.steps[at + 1 :]:
            if step["state"] == "running":
                self._end(step, "waiting", now)
            if step["state"] == "failed":
                step["state"], step["detail"] = "waiting", None
        step = self.steps[at]
        if step["state"] != "running":
            step["state"], step["detail"] = "running", None
            self._began[key] = now

    def called(self, tool: str, _args: dict[str, Any]) -> None:
        """The agent called *tool*."""
        if (key := TOOL_STEPS.get(tool)) is not None:
            self.stage(key)

    def finish(self) -> None:
        """A tried map waits for approval: every step done."""
        now = time.monotonic()
        for step in self.steps:
            if step["state"] in ("waiting", "running", "failed"):
                self._end(step, "done", now)
        self.state, self.ended = "done", time.time()

    def fail(self, error: str) -> None:
        now = time.monotonic()
        for step in self.steps:
            if step["state"] == "running":
                self._end(step, "failed", now)
                step["detail"] = error
        self.state, self.ended, self.error = "failed", time.time(), error

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started": self.started,
            "ended": self.ended,
            "steps": [dict(s) for s in self.steps],
            "error": self.error,
            "session": self.session,
        }


def _shown_maps(session: dict[str, Any], since: float) -> list[str]:
    """The page maps the agent showed the admin since *since*, oldest first."""
    return [
        str(m["page_map"]["id"])
        for m in session.get("messages", [])
        if m.get("role") == "agent"
        and float(m.get("at") or 0) >= since
        and isinstance(m.get("page_map"), dict)
    ]


def _last_said(session: dict[str, Any], since: float) -> str | None:
    said = [
        m.get("text") or ""
        for m in session.get("messages", [])
        if m.get("role") == "agent" and float(m.get("at") or 0) >= since
    ]
    return cut(said[-1], SAID_CHARS) if said and said[-1] else None


class PageLearns:
    """The page's learning of each target's page map, one at a time per target, each in the
    background."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.jobs: dict[str, PageLearn] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def targets(self) -> Targets:
        return self.agent.targets

    def running(self, target: str | None = None) -> bool:
        """Whether *target*'s page is being learned; with none, any target's."""
        if target is None:
            return any(not t.done() for t in self._tasks.values())
        task = self._tasks.get(target)
        return task is not None and not task.done()

    def _set(self, target: str) -> str:
        docs = self.targets.docs
        found = own_sets(docs, target) or list_sets(docs, target)
        if found:
            return found[0]
        save_set(docs, target, FIRST_SET, {"name": "Main"})
        return FIRST_SET

    async def start(self, target: str, set_name: str | None = None) -> PageLearn:
        """Start the agent learning *target*'s page, on *set_name* (default: its first set).

        :raises RuntimeError: One is already going for *target*.
        :raises LookupError: *set_name* isn't one of its sets.
        """
        if self.running(target):
            raise RuntimeError("The agent is learning this app's page already.")
        if set_name is None:
            set_name = await asyncio.to_thread(self._set, target)
        opened = await self.agent.create(target, set_name)
        job = PageLearn(target, str(opened["id"]))
        self.jobs[target] = job
        text = await asyncio.to_thread(learn_words, self.targets, target)
        self.agent.watching[job.session] = job.called
        job.stage(CODE)
        try:
            await self.agent.message(job.session, f"{text}\n\n{AGREED}")
        except Exception:
            self.agent.watching.pop(job.session, None)
            raise
        task = asyncio.create_task(self._follow(job))
        self._tasks[target] = task
        task.add_done_callback(lambda _t: self.agent.watching.pop(job.session, None))
        return job

    def _proposed_since(self, target: str, since: float) -> bool:
        try:
            kept = pagemaps.store(self.targets).proposed(target)
        except ValueError:
            return False
        return kept is not None and kept.updated >= since

    async def _follow(self, job: PageLearn) -> None:
        try:
            async with asyncio.timeout(MAX_SECONDS):
                while True:
                    await asyncio.sleep(POLL_SECONDS)
                    if self.agent.working(job.session):
                        continue
                    if await asyncio.to_thread(self._proposed_since, job.target, job.started):
                        job.finish()
                        return
                    session = await self.agent.raw(job.session)
                    shown = [p for p in _shown_maps(session, job.started) if p not in job.approved]
                    if shown and len(job.approved) <= MAX_REVISIONS:
                        job.approved.append(shown[-1])
                        job.stage(REHEARSE)
                        await self.agent.approve(job.session, shown[-1])
                        continue
                    said = _last_said(session, job.started)
                    job.fail(said or "The agent stopped without a map that showed its reply.")
                    return
        except TimeoutError:
            job.fail("It took too long, and was stopped.")
            await self._stop_agent(job)
        except asyncio.CancelledError:
            job.fail("Stopped before it finished.")
            await self._stop_agent(job)
            raise
        except Exception as exc:
            _logger.warning("page learn: %s failed", job.target, exc_info=True)
            job.fail(str(exc) or type(exc).__name__)

    async def _stop_agent(self, job: PageLearn) -> None:
        with contextlib.suppress(Exception):
            await asyncio.shield(self.agent.stop(job.session))

    async def stop(self, target: str) -> PageLearn | None:
        """Stop *target*'s setup and its agent; the job as it ended, or ``None`` for none."""
        task = self._tasks.get(target)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return self.jobs.get(target)

    def view(self, target: str) -> dict[str, Any] | None:
        job = self.jobs.get(target)
        return job.view() if job is not None else None

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
