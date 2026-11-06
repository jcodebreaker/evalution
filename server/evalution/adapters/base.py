"""The Target protocol (docs/architecture.md, "The Target protocol"): how evalution plays a case
on an app. The trajectory is never asked of the adapter; it is read from its traces.

An adapter is a module ``evalution.adapters.<name>`` with a ``create(target: TargetConfig) ->
Target`` function and an ``INFO`` (:class:`AdapterInfo`): everything the rest of evalution knows
about it, found through :func:`evalution.adapters.infos`. Adding that one module adds the adapter.

Contract notes for adapter authors. :class:`Target` holds what an adapter can leave out, each
with an honest default; :class:`Capabilities` says what it does instead, and a conformance test
holds the two together.

* ``open(run)`` returns an isolated session for the run. A run that plays cases side by side
  (the target's ``max_parallel`` above 1) opens one session per case slot, named
  ``<run>-<slot>``, for an adapter that declares ``parallel``. With ``reset`` declared the runner
  keeps one session per slot and calls ``reset`` before every try (the first too); without it, it
  opens a new session per try and closes it after the try.
* ``send`` returns when the turn is done, or at ``deadline`` (an absolute ``time.time()``)
  with ``Turn.error`` set. One that doesn't is stopped anyway (:mod:`evalution.runs.play`). It
  presses Stop ``stop_after`` seconds after sending when given. Done means done: when the app
  answers first and works on (a queued task, linked work), ``send`` waits for that work to end and
  returns the outcome, and the adapter declares ``background``. A later try never starts on top of
  an earlier one's work: a turn that timed out is cancelled, and a run on a ``background`` adapter
  that can't cancel ends there. ``Turn.ended_as`` says how the turn ended (a final answer, work
  waiting on something, or failed work), and ``Turn.error`` says why.
  Without ``files`` or ``stop`` declared, a turn asking for them errors with :data:`NO_FILES` or
  :data:`NO_STOP`. It never raises for an app-side failure of the turn (that is ``Turn.error``,
  with ``Turn.failure`` saying whether the app answered badly, could not be reached, refused the
  credentials or was busy: :func:`failure_of`, ``Turn.heard`` once the app has taken the message or
  may have, and ``Turn.lost`` when the connection broke); it raises :class:`TargetGone` when the
  session itself went away. Anything else it
  raises is taken as the adapter's own fault. Not being reachable, refused credentials and
  :class:`TargetGone` end the run: nothing can be judged on an app that is not there.
* ``state(session, {key: value})`` is called once per key of an ``expect_state`` step after
  the turn before it (``context_contains`` / ``context_lacks`` are checked by evalution from
  the traces, never sent here). The adapter waits for background work itself. The keys it answers
  are its ``state_checks``, which the library's fit verdicts read. ``StateResult.observed`` is
  false when it could not really read what it answers about: then nothing was learned, whatever
  ``ok`` says. An exception from a read means the same.
* ``probe(session, {key: value})`` reads the same check as the session stands, once and without
  waiting, before a try's first message: an adapter that declares ``probe`` lets a try that starts
  already holding what it should only come to hold (a reset that leaked) go unjudged rather than
  pass (:mod:`evalution.runs.leftover`).
* ``sections(request)`` names the parts of one model request, as read from an LLM span: the
  provider request body when the span's ``input.value`` holds one (a dict with ``messages``),
  else ``{"messages": [{"role", "content"}], "tools": [...]}`` built from
  ``llm.input_messages`` / ``llm.tools``. ``Section.text`` is what was sent;
  ``Section.tokens`` overrides the size estimate (an image block, say).
* ``break_conversation(session)`` starts a new conversation for the same person, keeping what the
  app keeps across conversations (long-term memory, files), for a case's
  ``{"break": "conversation"}`` turn. It may change ``session.trace_value``; the try reads the
  spans of every value it had.
* ``cancel(session)`` stops the work in flight, child work the adapter owns included. It is called
  when a run is stopped, when a turn is given up on or runs out of time, and for sessions an
  earlier server process left open. Failures are logged, never raised.
* ``close`` ends the session and keeps what it left in the app, to look at. An adapter that removes
  anything there declares ``deletes``, off unless the target asks. ``cancel``, ``close`` and any
  cleanup touch only what this run's sessions made: a session's id, and the identity it plays as,
  are unique to its run (or its try), so that holds by construction, and no adapter may search more
  widely. Both are also called for sessions an earlier server process left open, rebuilt from
  ``Session`` fields, so they must not rely on in-memory state.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Any, ClassVar

import httpx

from evalution.core.case import CLEAN_KINDS
from evalution.core.evidence import Failure, TurnEnd
from evalution.core.trace import Section

#: A turn's error on an adapter that doesn't declare ``files``, and ``stop``.
NO_FILES, NO_STOP = "this target takes no files", "this target cannot stop a turn"


class Isolation(Enum):
    """What a new session leaves behind of earlier ones."""

    NONE = "none"
    #: A new session id only: what carries over is up to the app.
    SESSION = "session"
    #: A new user, who has nothing of anyone else's.
    IDENTITY = "identity"


@dataclass(frozen=True)
class Capabilities:
    """What an adapter can do.

    :param isolation_note: What its isolation means here, in one plain sentence.
    :param parallel: Sessions may play at once, so a run may use the target's ``max_parallel``.
    :param state_checks: The ``expect_state`` keys it answers; none means no state checks.
    :param probe: It can read those keys before a try's first message (:meth:`Target.probe`).
    :param background: The app may answer before its work is done ("Queued!"), and ``send`` waits
        on past that answer for the work to end. So an adapter still waiting after an answer has
        missed nothing, and only the deadline ends the wait.
    :param cancel: It can stop the work in flight (:meth:`Target.cancel`).
    :param blocks: It can tell when the work is waiting on something (``TurnEnd.BLOCKED``), so a
        test may expect that.
    :param deletes: ``close`` removes what the session left in the app. Off, everything stays.
    """

    isolation: Isolation
    isolation_note: str
    reset: bool = False
    conversation_break: bool = False
    sections: bool = False
    files: bool = False
    stop: bool = False
    parallel: bool = False
    state_checks: frozenset[str] = frozenset()
    probe: bool = False
    background: bool = False
    cancel: bool = False
    blocks: bool = False
    deletes: bool = False
    #: What a try starts clean of, where it isn't what ``isolation`` implies.
    clean: frozenset[str] | None = None

    def starts_clean(self) -> frozenset[str]:
        """What a try can rely on finding nothing of from earlier ones."""
        if self.clean is not None:
            return self.clean
        if self.isolation is Isolation.IDENTITY:
            return frozenset(CLEAN_KINDS)
        return frozenset({"conversation"} if self.isolation is Isolation.SESSION else ())

    def names(self) -> list[str]:
        """The API's and the test writer's words for it, sorted."""
        named = {
            "reset": self.reset,
            "state": bool(self.state_checks),
            "sections": self.sections,
            "isolation": self.isolation is not Isolation.NONE,
            "parallel": self.parallel,
            "conversation_break": self.conversation_break,
            "cancel": self.cancel,
            "blocks": self.blocks,
            "background": self.background,
        }
        return sorted(name for name, has in named.items() if has)


class TargetGone(RuntimeError):
    """The session or the app went away mid-run (for example its host was removed)."""


class AppRefused(TargetGone):
    """The app turned the credentials down: nothing can be played until they're fixed."""


class AppError(RuntimeError):
    """The app answered with an error, so the adapter could not open or reset a session."""


@dataclass
class Session:
    id: str
    trace_attribute: str
    trace_value: str
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Turn:
    reply: str
    started: float
    ended: float
    trace_ids: list[str] = field(default_factory=list)
    error: str | None = None
    #: With ``error``: ``APP`` when left out.
    failure: Failure | None = None
    ended_as: TurnEnd = TurnEnd.ANSWERED
    #: With ``error``: the app took the message, or may have (:func:`may_have_heard`), and what went
    #: wrong came after. Sending it again would ask the same thing twice.
    heard: bool = False
    #: With ``error``: the connection broke while waiting, so nothing the app answered says it
    #: failed. Set by :func:`lost_connection`.
    lost: bool = False


@dataclass
class StateResult:
    ok: bool
    actual: str | None
    reason: str
    observed: bool = True


@dataclass
class MemoryRead:
    """An app's memory as text. ``written`` is what isn't yet what a new conversation gets, when
    the app has such a thing; ``None`` when what it has written is what it would use."""

    committed: str
    written: str | None = None


#: A turn that ended on one of these never reached the app.
UNREACHABLE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError)


def failure_of_status(status: int) -> Failure:
    """What an HTTP error status says of the app: it answered with an error, unless it turned the
    credentials down or it, or a gateway in front of it, is busy or down for now."""
    if status in (401, 403):
        return Failure.REFUSED
    return Failure.TRANSIENT if status in (429, 502, 503, 504) else Failure.APP


def status_may_have_passed_on(status: int) -> bool:
    """Whether an error status comes from a gateway that may have passed the message on first."""
    return status in (502, 504)


def may_have_heard(exc: BaseException) -> bool:
    """Whether ``send`` meeting *exc* leaves it open that the app took the message."""
    return isinstance(exc, httpx.HTTPStatusError) and status_may_have_passed_on(
        exc.response.status_code
    )


def lost_connection(exc: BaseException) -> bool:
    """Whether *exc* is a connection breaking, and no answer of the app's."""
    return isinstance(exc, httpx.TransportError)


def failure_of(exc: BaseException) -> Failure:
    """What an exception met in ``send`` says of the app: not there, out of time, or what its status
    says. Anything but a transport error is the adapter not reading what it got."""
    if isinstance(exc, UNREACHABLE_ERRORS):
        return Failure.UNREACHABLE
    if isinstance(exc, httpx.TimeoutException):
        return Failure.TIMED_OUT
    if isinstance(exc, httpx.HTTPStatusError):
        return failure_of_status(exc.response.status_code)
    return Failure.APP if isinstance(exc, httpx.HTTPError) else Failure.ADAPTER


class Target(ABC):
    """An app played through its adapter. ``capabilities`` says which of the methods with a default
    it replaces."""

    name: str
    capabilities: ClassVar[Capabilities]

    @abstractmethod
    async def open(self, run: str) -> Session: ...

    @abstractmethod
    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),  # type: ignore[assignment]
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn: ...

    @abstractmethod
    async def close(self, session: Session) -> None: ...

    async def read_memory(self, session: Session) -> MemoryRead | None:
        """What the app keeps about this person, as text, to measure; ``None`` if it can't say."""
        return None

    async def fold_at(self, session: Session, tokens: int) -> None:  # noqa: B027
        """Have the app fold older turns from *tokens*. Optional: most can't, and then a test that
        asks is measured as never folding."""

    async def reset(self, session: Session) -> None:
        raise NotImplementedError(f"{self.name} can't reset a session")

    async def break_conversation(self, session: Session) -> None:
        raise NotImplementedError(f"{self.name} can't start a new conversation")

    async def cancel(self, session: Session) -> None:
        raise NotImplementedError(f"{self.name} can't stop work in flight")

    async def state(self, session: Session, check: dict[str, Any]) -> StateResult:
        return StateResult(False, None, "this target has no state checks", observed=False)

    async def probe(self, session: Session, check: dict[str, Any]) -> StateResult:
        """:meth:`state` as the session stands, read once without waiting for background work: what
        a try starts from."""
        raise NotImplementedError(f"{self.name} can't read its state before a try")

    def sections(self, request: dict[str, Any]) -> list[Section]:
        return []

    async def dispose(self) -> None:
        """Let go of what it holds, for one made for a single message."""
        return None

    def answers(self) -> list[dict[str, Any]]:
        """What the app last answered, as sent (``{route, status, body}``, oldest first), to work
        out settings that misread it. Raw: it may hold anything, so scrub it before it goes on."""
        return []


# What the rest of evalution knows about an adapter


#: Python package files, by name.
PY_DEPS = ("pyproject.toml", "requirements.txt", "requirements.in", "setup.py", "setup.cfg")
#: A port a compose file publishes.
COMPOSE_PORT = re.compile(r"[\"'\s-](?:\d+\.\d+\.\d+\.\d+:)?(\d{2,5}):\d{2,5}[\"'\s]")
#: Ports that are not the app: Phoenix, OTLP and the usual databases.
NOT_APP_PORTS = frozenset({6006, 4317, 4318, 5432, 6379, 3306, 27017})


def read_env(file: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """The values of *keys* in the dotenv *file*, and nothing else from it."""
    try:
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        key = key.strip()
        if sep and key in keys:
            value = value.split(" #", 1)[0].strip().strip("\"'")
            if value:
                out[key] = value
    return out


class Folder:
    """A project folder, as :attr:`AdapterInfo.detect` reads it. Its files are read once, on first
    use, so an adapter that knows the folder by a fingerprint costs no scan."""

    def __init__(self, path: Path, read: Callable[[], dict[str, str]]) -> None:
        self.path = path
        self._read = read

    @cached_property
    def files(self) -> dict[str, str]:
        """``relative path -> text`` of what tells how the app runs. Never a secrets file."""
        return self._read()

    def env(self, keys: tuple[str, ...]) -> dict[str, str]:
        """The values of *keys* in the project's ``.env``, the only way to read that file."""
        return read_env(self.path / ".env", keys)

    def packages(self) -> str:
        """The text of its package files, lowercased."""
        names = (*PY_DEPS, "package.json")
        found = (t for r, t in self.files.items() if r.rsplit("/", 1)[-1] in names)
        return " ".join(found).lower()


@dataclass
class Detection:
    """What an adapter read from a folder that is its kind of app.

    :param base: The app's address.
    :param options: For :attr:`AdapterInfo.propose`.
    :param steps: ``(text, state)`` to show, in order; a state is ``done`` or ``problem``.
    :param secrets: Values found in the project: kept in memory, never shown.
    :param phoenix: The app's own Phoenix, when the adapter found it.
    :param reads_tracing: Look for the tracing setup in the code, as for any app.
    """

    base: str
    options: dict[str, Any]
    steps: list[tuple[str, str]]
    secrets: dict[str, str] = field(default_factory=dict)
    phoenix: dict[str, str] | None = None
    reads_tracing: bool = True


@dataclass(frozen=True)
class OwnTraffic:
    """How an adapter's eval traffic is told from real use in the traces.

    :param sessions: Prefixes of the session ids it opens.
    :param attributes: Span attributes that mark an eval try. Apps send these, so renaming one
        counts evals as real use.
    :param users: Patterns of the ``user.id`` it plays as.
    """

    sessions: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    users: tuple[re.Pattern[str], ...] = ()


def part(ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    """One part of a target's check; ``refused=True`` when the app turned its secret down."""
    return {"ok": ok, "detail": detail, **extra}


def text_options(options: dict[str, Any]) -> None:
    """:raises ValueError: An option that is not a non-empty string."""
    for key, value in options.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"options.{key}: must be a string")


def flat_block(options: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    """Options side by side in the target's block, each secret named by its ``<key>_env``."""
    return {**options, **{f"{key}_env": ref for key, ref in secrets.items()}}


def flat_options(block: dict[str, Any]) -> dict[str, Any]:
    """What :func:`flat_block` wrote, from the whole block."""
    return {k: v for k, v in block.items() if k not in ("adapter", "phoenix", "code")}


def url_options(base: str, found: dict[str, Any], answers: dict[str, str]) -> dict[str, Any]:
    """The address as ``url``, next to what was found."""
    return {"url": base, **found}


def _no_detection(folder: Folder) -> Detection | None:
    return None


def _no_trace_setup(url: str, builtin: bool) -> dict[str, Any] | None:
    return None


@dataclass(frozen=True)
class AdapterInfo:
    """Everything the rest of evalution knows about an adapter, exported by its module as ``INFO``.

    :param name: The module's name, and a target's ``adapter``.
    :param label: How the understanding page calls it.
    :param options: What it takes from the page.
    :param required: Which of those it needs.
    :param secrets: The secrets it takes, besides the traces' own.
    :param check: ``(client, target block, lookup)``: tries the app without playing anything, and
        says how as a :func:`part`. ``lookup`` turns a secret's name into its value.
    :param detect: Whether a project folder is this kind of app, and what that tells.
    :param trace_setup: ``(url, builtin)``: how to point this app at evalution's own traces
        (``traced``, ``text``, ``lines``, ``command``), when it isn't what its code shows.
    :param own_traffic: How its eval traffic shows in traces.
    :param rank: Detectors with a higher rank go first: a fingerprint before a mention in a package
        file.
    :param refused: The step shown when :attr:`check` says the app refused.
    :param trace_project: The project its spans land in, when the app can't be told which.
    :param framework: ``(package word, name)`` for the stack on the understanding page.
    :param validate: Raises ``ValueError``, naming the field, for options that don't fit.
    :param block: ``(options, secret -> its name)``: the target's block, without ``adapter``.
    :param page_options: Its options from a target's whole block.
    :param propose: ``(base, Job options, answers)``: its options for a target discovery proposes.
    :param supports: The app's versions it was written for, like ``>=1.2,<2``. Its :attr:`check`
        compares the running app's: :func:`evalution.adapters.contract.version_part`.
    """

    name: str
    label: str
    options: tuple[str, ...]
    required: tuple[str, ...]
    secrets: tuple[str, ...]
    check: Callable[
        [httpx.AsyncClient, dict[str, Any], Callable[[Any], str | None]],
        Awaitable[dict[str, Any]],
    ]
    detect: Callable[[Folder], Detection | None] = _no_detection
    trace_setup: Callable[[str, bool], dict[str, Any] | None] = _no_trace_setup
    own_traffic: OwnTraffic = OwnTraffic()
    rank: int = 0
    refused: str = "The app refused the request."
    trace_project: str | None = None
    framework: tuple[str, str] | None = None
    validate: Callable[[dict[str, Any]], None] = text_options
    block: Callable[[dict[str, Any], dict[str, str]], dict[str, Any]] = flat_block
    page_options: Callable[[dict[str, Any]], dict[str, Any]] = flat_options
    propose: Callable[[str, dict[str, Any], dict[str, str]], dict[str, Any]] = url_options
    supports: str | None = None
