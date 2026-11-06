"""The adapter contract kit (docs/adapters.md): ``evalution adapter test``, and :func:`run` for
anyone who wants it in their own tests.

It plays a short sequence over a real target and checks the adapter, never the agent: sessions are
unique, a turn returns a reply and ends, the deadline holds, ``close`` works from a rebuilt
``Session``, what is declared really works (``probe`` on a session yet to send anything), and the
session's trace value finds spans. What an app answers is not judged, only that the adapter read an
answer. It spends :func:`messages_needed` messages. Nothing here raises for an adapter that doesn't
work: that is what it reports, naming what isn't supported, with no stack trace.

The traces live above this layer, so the caller hands in a :data:`SpanCount`.

``tests/test_adapter_conformance.py`` checks the same promises offline, for the built-in adapters.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import re
import secrets
import tempfile
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from evalution.adapters.base import (
    NO_FILES,
    NO_STOP,
    AdapterInfo,
    Capabilities,
    Session,
    Target,
    Turn,
    part,
)
from evalution.core import net

Status = Literal["ok", "failed", "skipped"]
#: ``(session, start, end)``: how many AI spans the session's trace value finds in that time.
SpanCount = Callable[[Session, float, float], Awaitable[int]]
OnStep = Callable[["Step"], None]

MESSAGE = "Reply with only the word OK."
#: Long enough that a slow app is still working when a short deadline passes.
LONG_MESSAGE = "Count from 1 to 500, one number per line."
#: Sent to state checks: words no app holds, so the answer doesn't matter, only that it comes.
PROBE = "evalution-contract-probe"
_404ISH = frozenset({404, 405, 501})
#: The fields of :class:`Capabilities` that say how a session behaves, and aren't a thing to try.
_DESCRIPTIVE = frozenset({"isolation", "isolation_note", "clean"})
_EXERCISED = frozenset(
    {"reset", "conversation_break", "files", "stop", "state_checks", "probe", "sections"}
)
_NOT_EXERCISED = {
    "parallel": "two turns at once aren't tried here",
    "background": "work after an answer can't be told from outside",
}
_VERSION = re.compile(r"\d+(?:\.\d+)*")
_BOUND = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*$")


@dataclass(frozen=True)
class Step:
    name: str
    status: Status
    detail: str = ""


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)
    #: Messages really sent, which a failure can leave below what was planned.
    messages: int = 0

    @property
    def ok(self) -> bool:
        return all(step.status != "failed" for step in self.steps)


def messages_needed(capabilities: Capabilities) -> int:
    """What :func:`run` sends when all goes well: one to see a turn end and one to see a deadline
    hold, and one for each of ``reset``, ``conversation_break``, ``files`` and ``stop`` declared."""
    extras = (
        capabilities.reset,
        capabilities.conversation_break,
        capabilities.files,
        capabilities.stop,
    )
    return 2 + sum(extras)


def explain(what: str, exc: BaseException) -> str:
    """*exc* in words: above all, whether the thing tried isn't supported."""
    if isinstance(exc, NotImplementedError):
        return f"{what}: declared, but the adapter doesn't implement it ({exc})"
    if isinstance(exc, httpx.HTTPStatusError):
        request, code = exc.request, exc.response.status_code
        # Without the login or the query: they can hold a key.
        where = f"{request.method} {net.no_login(str(request.url))}"
        if code in _404ISH:
            return f"{what}: not supported by this app: {where} answered {code}"
        return f"{what}: {where} answered {code}"
    if isinstance(exc, httpx.TransportError):
        return f"{what}: couldn't reach the app ({type(exc).__name__})"
    if isinstance(exc, TimeoutError):
        return f"{what}: no answer in time"
    return f"{what}: {type(exc).__name__}: {exc}"


class _Sequence:
    """One pass, keeping every session it opened, so all of them get closed."""

    def __init__(
        self,
        target: Target,
        on_step: OnStep | None,
        limit: float,
        short: float,
        grace: float,
    ) -> None:
        self.target = target
        self.on_step = on_step
        self.limit = limit
        self.short = short
        self.grace = grace
        self.report = Report()
        self.sessions: list[Session] = []
        self.token = secrets.token_hex(3)

    def said(self, name: str, status: Status, detail: str = "") -> None:
        step = Step(name, status, detail)
        self.report.steps.append(step)
        if self.on_step is not None:
            self.on_step(step)

    async def bounded[T](self, work: Coroutine[Any, Any, T], seconds: float | None = None) -> T:
        """Whatever an adapter does, it doesn't hold this up for ever."""
        return await asyncio.wait_for(work, self.limit if seconds is None else seconds)

    async def open(self, run: str) -> Session:
        session = await self.bounded(self.target.open(run))
        self.sessions.append(session)
        return session

    async def send(self, session: Session, message: str = MESSAGE, **given: Any) -> Turn:
        self.report.messages += 1
        deadline = given.pop("deadline", time.time() + self.limit)
        bound = deadline - time.time() + self.grace
        send = self.target.send(session, message, deadline=deadline, **given)
        return await self.bounded(send, bound)

    def shape(self, info: AdapterInfo | None) -> None:
        declared, target = self.target.capabilities, type(self.target)
        replaced = {
            "reset": (declared.reset, target.reset is not Target.reset),
            "conversation_break": (
                declared.conversation_break,
                target.break_conversation is not Target.break_conversation,
            ),
            "sections": (declared.sections, target.sections is not Target.sections),
            "state_checks": (bool(declared.state_checks), target.state is not Target.state),
            "probe": (declared.probe, target.probe is not Target.probe),
        }
        wrong = [name for name, (says, has) in replaced.items() if says != has]
        if wrong:
            self.said(
                "declares what it implements",
                "failed",
                "declared and replaced methods differ for: " + ", ".join(wrong),
            )
        else:
            self.said("declares what it implements", "ok")
        if info is None:
            self.said("exports INFO", "failed", "the module has no AdapterInfo named INFO")
        elif not set(info.required) <= set(info.options):
            self.said("exports INFO", "failed", "INFO.required names something not in its options")
        else:
            self.said("exports INFO", "ok")

    async def open_sessions(self) -> tuple[Session, Session] | None:
        run = f"contract-{self.token}"
        try:
            first = await self.open(f"{run}-a")
            second = await self.open(f"{run}-b")
            sessions = [first, second]
            # Without reset, the runner opens a new session per try under the same run.
            if not self.target.capabilities.reset:
                sessions.append(await self.open(f"{run}-a"))
        except Exception as exc:  # someone's own adapter can fail in any way at all
            self.said("open gives unique sessions", "failed", explain("open", exc))
            return None
        problems = []
        if len({s.id for s in sessions}) < len(sessions):
            problems.append(f"session ids repeat: {[s.id for s in sessions]}")
        if len({s.trace_value for s in sessions}) < len(sessions):
            problems.append(
                f"trace values repeat, so tries would read each other's spans: "
                f"{[s.trace_value for s in sessions]}"
            )
        self.said(
            "open gives unique sessions",
            "failed" if problems else "ok",
            "; ".join(problems),
        )
        return first, second

    async def a_turn(self, session: Session, spans: SpanCount | None, settle: float) -> bool:
        name = "send returns a reply and ends"
        start = time.time()
        try:
            turn = await self.send(session)
        except Exception as exc:
            self.said(
                name,
                "failed",
                explain("send", exc) + ". It raises only when the session is gone: an app's "
                "failure is Turn.error",
            )
            return False
        problem = (
            f"the turn ended on an error: {turn.error}"
            if turn.error
            else "the turn ended, but the adapter read an empty reply"
            if not turn.reply.strip()
            else f"Turn.ended ({turn.ended}) is before Turn.started ({turn.started})"
            if turn.ended < turn.started
            else ""
        )
        self.said(name, "failed" if problem else "ok", problem or f"{time.time() - start:.1f} s")
        if not problem:
            await self.trace_value(session, spans, start, settle)
        return not problem

    async def trace_value(
        self, session: Session, spans: SpanCount | None, start: float, settle: float
    ) -> None:
        name = "the trace value finds spans"
        if spans is None:
            self.said(name, "skipped", "no traces from this target yet")
            return
        stop = time.time() + settle
        try:
            while True:
                found = await self.bounded(spans(session, start, time.time()))
                if found or time.time() >= stop:
                    break
                await asyncio.sleep(min(1.0, settle))
        except Exception as exc:
            self.said(name, "failed", explain("reading the traces", exc))
            return
        if found:
            self.said(name, "ok", f"{found} AI spans")
        else:
            self.said(
                name,
                "failed",
                f"no AI spans with {session.trace_attribute} = {session.trace_value} after "
                f"{settle:.0f} s: check the app sends traces, and with that value",
            )

    async def after(self, name: str, session: Session, what: str) -> None:
        """One more turn: *what* happened, and the session still works."""
        try:
            turn = await self.send(session)
        except Exception as exc:
            self.said(name, "failed", explain(f"a turn after {what}", exc))
            return
        if turn.error:
            self.said(name, "failed", f"a turn after {what} ended on an error: {turn.error}")
        else:
            self.said(name, "ok")

    async def reset(self, session: Session) -> None:
        try:
            await self.bounded(self.target.reset(session))
        except Exception as exc:
            self.said("reset", "failed", explain("reset", exc))
            return
        await self.after("reset", session, "reset")

    async def conversation_break(self, session: Session) -> None:
        try:
            await self.bounded(self.target.break_conversation(session))
        except Exception as exc:
            self.said("conversation_break", "failed", explain("conversation_break", exc))
            return
        await self.after("conversation_break", session, "a new conversation")

    async def files(self, session: Session) -> None:
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "note.txt"
            file.write_text("A note from the evalution contract kit.\n", encoding="utf-8")
            try:
                turn = await self.send(session, files=[file])
            except Exception as exc:
                self.said("files", "failed", explain("files", exc))
                return
        if turn.error == NO_FILES:
            self.said("files", "failed", "declared, but a turn with a file answers NO_FILES")
        elif turn.error:
            self.said("files", "failed", f"a turn with a file ended on an error: {turn.error}")
        else:
            self.said("files", "ok")

    async def stop(self, session: Session) -> None:
        try:
            turn = await self.send(session, LONG_MESSAGE, stop_after=0.5)
        except Exception as exc:
            self.said("stop", "failed", explain("stop", exc))
            return
        if turn.error == NO_STOP:
            self.said("stop", "failed", "declared, but a turn with stop_after answers NO_STOP")
        else:
            self.said("stop", "ok")

    async def state_checks(self, session: Session, *, before: bool = False) -> None:
        """Each state check read; *before* the session's first message, through ``probe``."""
        read, what = (self.target.probe, "probe") if before else (self.target.state, "state check")
        for key in sorted(self.target.capabilities.state_checks):
            name = f"{what} {key}"
            try:
                found = await self.bounded(read(session, {key: PROBE}))
            except Exception as exc:
                self.said(name, "failed", explain(name, exc))
                continue
            if found.observed:
                self.said(name, "ok", "read; what it found isn't judged")
            else:
                self.said(name, "failed", f"declared, but couldn't read it: {found.reason}")

    def sections(self) -> None:
        request = {"messages": [{"role": "user", "content": MESSAGE}], "tools": []}
        try:
            found = self.target.sections(request)
        except Exception as exc:
            self.said("sections", "failed", explain("sections", exc))
            return
        if isinstance(found, list) and found:
            self.said("sections", "ok")
        else:
            self.said("sections", "failed", "declared, but a plain request gave no sections")

    def rest(self) -> None:
        """What is declared and not tried, including what this kit is older than."""
        declared = self.target.capabilities
        for item in dataclasses.fields(declared):
            name = item.name
            if name in _DESCRIPTIVE or name in _EXERCISED or not getattr(declared, name):
                continue
            why = _NOT_EXERCISED.get(name, "this version of the kit doesn't try it")
            self.said(name, "skipped", f"declared; {why}")

    async def deadline(self, session: Session) -> None:
        """Last, and on its own session: it may leave work going on the app."""
        name = "the deadline holds"
        deadline = time.time() + self.short
        try:
            turn = await self.send(session, LONG_MESSAGE, deadline=deadline)
        except TimeoutError:
            self.said(
                name,
                "failed",
                f"send was still out {self.grace:.0f} s past a {self.short:.0f} s deadline",
            )
            return
        except Exception as exc:
            self.said(name, "failed", explain("send", exc))
            return
        late = time.time() - deadline
        if late > 1 and not turn.error:
            self.said(
                name, "failed", f"returned {late:.0f} s past its deadline, with no Turn.error"
            )
        else:
            self.said(name, "ok")

    async def close_all(self) -> None:
        """Each from a rebuilt ``Session``: a later server process closes what an earlier one left
        open, from these fields alone."""
        problems = []
        for session in self.sessions:
            try:
                rebuilt = Session(**json.loads(json.dumps(session.as_dict())))
                await self.bounded(self.target.close(rebuilt))
            except Exception as exc:
                problems.append(explain(f"close {session.id}", exc))
        self.said("close doesn't raise", "failed" if problems else "ok", "; ".join(problems))


async def run(
    target: Target,
    info: AdapterInfo | None,
    *,
    spans: SpanCount | None = None,
    on_step: OnStep | None = None,
    settle: float = 30.0,
    limit: float = 120.0,
    short: float = 5.0,
    grace: float = 30.0,
) -> Report:
    """The whole sequence over *target*.

    :param spans: How to read spans, when the target has a trace source.
    :param on_step: Called with each step as it ends.
    :param settle: How long spans may take to arrive.
    :param limit: How long any one call may take.
    :param short: The deadline of the turn that tests deadlines.
    :param grace: How far past a deadline ``send`` may be, before it is given up on.
    """
    steps = _Sequence(target, on_step, limit, short, grace)
    steps.shape(info)
    opened = await steps.open_sessions()
    declared = target.capabilities
    try:
        if opened is None:
            return steps.report
        first, second = opened
        if await steps.a_turn(first, spans, settle):
            if declared.state_checks:
                await steps.state_checks(first)
            if declared.sections:
                steps.sections()
            if declared.files:
                await steps.files(first)
            if declared.stop:
                await steps.stop(first)
            if declared.reset:
                await steps.reset(first)
            if declared.conversation_break:
                await steps.conversation_break(first)
            if declared.probe:
                await steps.state_checks(second, before=True)
            steps.rest()
            await steps.deadline(second)
        return steps.report
    finally:
        with contextlib.suppress(asyncio.CancelledError):
            await steps.close_all()


# Which versions of an app an adapter works with


def _numbers(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in version.split("."))


def version_fits(found: str, supports: str) -> bool:
    """Whether *found* (``1.4.2``, ``v1.4``) is within *supports* (``>=1.2,<2``). A version with no
    number in it doesn't fit.

    :raises ValueError: *supports* isn't comparisons joined by commas.
    """
    version = _VERSION.search(found)
    bounds = [_BOUND.match(item) for item in supports.split(",")]
    if not all(bounds):
        raise ValueError(f"supports: {supports!r} isn't like '>=1.2,<2'")
    if version is None:
        return False
    have = _numbers(version.group())
    compare = {
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        "!=": lambda a, b: a != b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }
    for bound in bounds:
        assert bound is not None
        op, wanted = bound.groups()
        want = _numbers(wanted)
        size = max(len(have), len(want))
        pad = (0,) * size
        if not compare[op]((have + pad)[:size], (want + pad)[:size]):
            return False
    return True


def version_part(label: str, found: str, supports: str | None) -> dict[str, Any]:
    """One part of an adapter's check (:attr:`AdapterInfo.check`) for the running app's version: a
    mismatch reads as a setup problem, with what to do about it."""
    if supports is None or version_fits(found, supports):
        return part(True, f"{label} {found}")
    return part(
        False,
        f"{label} is version {found}, and this adapter supports {supports}",
        fix="Run a version it supports, or update the adapter and its INFO.supports",
    )


# Writing a new one


_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_STARTER = '''"""__NAME__: an evalution adapter, from `evalution adapter new`. For an app with
`POST {url}/chat`, which takes {"session": ..., "message": ...} and answers {"reply": ...}, and
`GET {url}/health`. Change those to what your app does; each place is marked CHANGE.

Target it from evalution.yaml, and check it (this spends a few messages, and says how many first):

    targets:
      my-app:
        adapter: __NAME__
        url: http://127.0.0.1:3000

    evalution adapter test __NAME__ --target my-app

docs/adapters.md has the contract. In short: declare only what you implement, send() returns when
the work is done and not just when the first answer comes, and close() needs only Session's fields.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from evalution.adapters.base import (
    NO_FILES,
    NO_STOP,
    AdapterInfo,
    Capabilities,
    Isolation,
    OwnTraffic,
    Session,
    Target,
    Turn,
    failure_of,
    part,
)
from evalution.config import TargetConfig
from evalution.core import net


class AppTarget(Target):
    # CHANGE: say what is true of your app. Turn a capability on only together with its method:
    #   reset=True               async def reset(self, session): the session is clean for a new try
    #   conversation_break=True  async def break_conversation(self, session): same person, new chat
    #   state_checks=frozenset({"memory_contains"})
    #                            async def state(self, session, check) -> StateResult
    #   probe=True               async def probe(self, session, check) -> StateResult: the same,
    #                            read once without waiting, before a try's first message
    #   sections=True            def sections(self, request) -> list[Section]
    #   files=True, stop=True    honour send()'s files and stop_after
    #   background=True          the app answers first and works on, and send() waits for the work
    #   parallel=True            sessions don't share anything, so tries may play side by side
    # IDENTITY isolation is a new user per session; SESSION is a new session id, and no more.
    capabilities = Capabilities(
        isolation=Isolation.SESSION,
        isolation_note="Each try gets a new session id; what carries over is up to the app.",
    )

    def __init__(self, target: TargetConfig) -> None:
        self.name = target.name
        self.url = str(target.options["url"]).rstrip("/")
        self.trace_attribute = target.trace_attribute
        self.client = net.client()

    async def open(self, run: str) -> Session:
        # Unique every time, even for the same run, and never reusing someone's real session.
        # CHANGE: if your app makes its own sessions, ask it for one here.
        value = f"evalution-{run}-{secrets.token_hex(4)}"
        return Session(value, self.trace_attribute, value)

    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),  # type: ignore[assignment]
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn:
        started = time.time()
        if files:
            return Turn("", started, started, error=NO_FILES)
        if stop_after is not None:
            return Turn("", started, started, error=NO_STOP)
        try:
            # CHANGE: how your app takes a message, and where its reply is.
            answer = await self.client.post(
                f"{self.url}/chat",
                json={"session": session.trace_value, "message": message},
                timeout=max(deadline - started, 0.1),
            )
            answer.raise_for_status()
            reply = str(answer.json()["reply"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # An app that failed is the turn's error, never an exception.
            error = net.said(exc)
            return Turn("", started, time.time(), error=error, failure=failure_of(exc))
        return Turn(reply, started, time.time())

    async def close(self, session: Session) -> None:
        # Called also for sessions an earlier server process left open, so it can use only
        # Session's fields. CHANGE: end the session here if your app needs it. Delete nothing that
        # this adapter can't prove it made: leaving a session behind costs less than losing data.
        return None


def create(target: TargetConfig) -> AppTarget:
    return AppTarget(target)


async def _check(client: httpx.AsyncClient, options: dict[str, Any], lookup: Any) -> dict[str, Any]:
    """Whether the app is there, without playing anything: `evalution doctor` and the page ask."""
    # CHANGE: a route of your app that costs nothing.
    answer = await client.get(f"{str(options['url']).rstrip('/')}/health")
    if answer.status_code >= 400:
        return part(False, f"It answered {answer.status_code}")
    # If your app says its version, a wrong one can read as a setup problem. Set `supports` below,
    # import version_part from evalution.adapters.contract, and:
    #     return version_part("The app", answer.json()["version"], INFO.supports)
    return part(True, "It answers")


INFO = AdapterInfo(
    name="__NAME__",
    label="__NAME__ adapter",
    options=("url",),
    required=("url",),
    secrets=(),
    check=_check,
    # How its eval traffic shows in traces, so it stays out of digests of real use.
    own_traffic=OwnTraffic(sessions=("evalution-",)),
    # supports=">=1.2,<2",
)
'''


def new_adapter(name: str, folder: Path, taken: tuple[str, ...]) -> Path:
    """A commented starter at ``<folder>/<name>.py``, which works as it stands against an app of the
    shape it says. Never over an existing file.

    :param taken: The adapters' folder's modules: a new one under such a name would never load.
    :raises ValueError: A name that can't be one, or is taken.
    :raises FileExistsError: The file is there.
    """
    if not _NAME.match(name):
        raise ValueError(f"{name!r} isn't an adapter name: lowercase letters, digits and _")
    if name in taken:
        raise ValueError(f"{name!r} is taken: choose another name")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.py"
    with path.open("x", encoding="utf-8") as out:
        out.write(_STARTER.replace("__NAME__", name))
    return path
