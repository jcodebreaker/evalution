"""One real turn through an adapter, to show it works before anything rests on it: the app answers,
the adapter reads the answer and the turn ends. Spans that don't arrive under the session's own
value are a warning: a plain app with no traces is still usable, and tests needing them go unjudged.

The conformance tests can only show an adapter has the right shape. This one spends one message.
A setup that passed isn't checked again until the adapter's code or its configuration changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from evalution.adapters.base import Target, TargetGone, failure_of
from evalution.config import TargetConfig
from evalution.core import net
from evalution.core.case import Case, CaseTurn
from evalution.core.evidence import Failure, TraceStatus
from evalution.runs.page.map import PageMapBroken
from evalution.runs.play import ADAPTER_SECONDS, play_with_retry
from evalution.traces.base import TraceSource

MESSAGE = "Reply with only the word OK."
LIMIT_SECONDS = 120
_PASSED = "adapter-checks.json"
#: Who a run's first check plays as.
RUN_KEY = "adapter-check"
#: How a turn of a target reached through its page alone may end that is the page's to show, and
#: the tests' to judge: it showed no reply, or was still busy when the time ran out.
_THE_PAGES_FINDING = (Failure.APP, Failure.TIMED_OUT)


class AdapterBroken(RuntimeError):
    """The adapter doesn't read the app. Says how."""


@dataclass(frozen=True)
class AdapterCheck:
    problems: list[str] = field(default_factory=list)
    reply: str = ""
    seconds: float = 0.0
    tools: int = 0
    spans: int = 0
    traces: str = TraceStatus.NO_SOURCE.value
    #: The app wasn't there to answer, which says nothing of the adapter.
    app_down: bool = False
    #: The app turned the credentials down.
    refused: bool = False
    #: The failure was the adapter's. One may be a misreading of this turn alone.
    adapter_failed: bool = False
    #: The page didn't have what its page map names (ADR 0010).
    page_map: bool = False
    #: The app works, but tests that need its traces can't be judged.
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


_CASE = Case(
    case="adapter-check",
    name="The adapter works",
    turns=(CaseTurn(message=MESSAGE),),
    tools={},
    reference="",
    rubric=(),
    limits={"tries": 1, "seconds": LIMIT_SECONDS},
    version="",
    raw={},
)


async def check_adapter(
    target: Target, source: TraceSource | None, settle: float, stuck: float, key: str = RUN_KEY
) -> AdapterCheck:
    """Never raises for an app or adapter that doesn't work: that is what it reports. *key* is who
    it plays as, which an adapter may tidy up after by name."""
    try:
        session = await asyncio.wait_for(target.open(key), ADAPTER_SECONDS)
    except PageMapBroken as exc:
        return AdapterCheck([f"Couldn't open the page: {exc}"], page_map=True)
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        refused = failure_of(exc) is Failure.REFUSED
        said = "The app refused the credentials" if refused else "Couldn't open a session"
        problem = f"{said}: {net.said(exc)}"
        return AdapterCheck([problem], app_down=True, refused=refused)
    try:
        if target.capabilities.reset:
            await asyncio.wait_for(target.reset(session), ADAPTER_SECONDS)
        played = await play_with_retry(
            target, session, _CASE, Path(), filler=None, source=source, settle=settle, stuck=stuck
        )
    except (httpx.HTTPError, TargetGone, OSError, RuntimeError, ValueError) as exc:
        problem = f"The turn didn't run: {net.said(exc)}"
        return AdapterCheck([problem], app_down=True)
    finally:
        with contextlib.suppress(httpx.HTTPError, OSError, RuntimeError, ValueError):
            await target.close(session)
    assert played.evidence is not None
    evidence = played.evidence
    problems = []
    warnings = []
    if played.page_only and played.failure in _THE_PAGES_FINDING:
        # No adapter to check: what the page showed of the turn is for the tests to judge, and a
        # page that showed nothing fails them, not the run before them.
        warnings.append(f"The page didn't answer the check's message: {played.error}")
    elif played.error and played.failure is not None:
        problems.append(net.no_login(played.error))
    elif not played.reply_text.strip():
        problems.append("The turn ended, but the adapter read an empty reply")
    elif played.reply_text.strip() == MESSAGE:
        problems.append("The adapter read back the message it sent, not a reply")
    elif _raw_json(played.reply_text):
        problems.append("The adapter read the app's raw answer, not the reply inside it")
    if evidence.trace is TraceStatus.UNREAD:
        warnings.append(f"The traces couldn't be read: {evidence.trace_note}")
    elif evidence.trace is TraceStatus.EMPTY and source is not None:
        warnings.append(
            f"No traces arrived with {session.trace_attribute} = {session.trace_value}: "
            "check the app sends them, and with that value"
        )
    return AdapterCheck(
        problems=problems,
        reply=played.reply_text,
        seconds=played.seconds,
        tools=len(evidence.tools),
        spans=len(evidence.spans),
        traces=evidence.trace.value,
        app_down=bool(problems)
        and played.failure
        in (
            Failure.APP,
            Failure.UNREACHABLE,
            Failure.TIMED_OUT,
            Failure.BLOCKED,
            Failure.REFUSED,
            Failure.TRANSIENT,
        ),
        refused=played.failure is Failure.REFUSED,
        adapter_failed=played.failure is Failure.ADAPTER,
        page_map=played.failure is Failure.PAGE_MAP,
        warnings=warnings,
    )


def _raw_json(text: str) -> bool:
    """Whether *text* is a JSON object or list: an answer not yet read, asked for one word."""
    try:
        return isinstance(json.loads(text), (dict, list))
    except ValueError:
        return False


async def check_isolation(target: Target) -> str | None:
    """Whether a new session can see what an earlier one said: says a made-up word in one and asks
    for it in another. Spends two messages. The problem in words, or ``None`` when it didn't show,
    which is no proof: an app might know it and not say.

    :raises OSError, RuntimeError, ValueError: The app didn't answer.
    """
    word = f"zephyr{secrets.token_hex(3)}"
    limit = time.time() + LIMIT_SECONDS
    first = await asyncio.wait_for(target.open("isolation-check-a"), ADAPTER_SECONDS)
    try:
        await target.send(first, f"Please remember this word for later: {word}.", deadline=limit)
    finally:
        await target.close(first)
    second = await asyncio.wait_for(target.open("isolation-check-b"), ADAPTER_SECONDS)
    try:
        said = await target.send(
            second,
            "Did anyone give you a word to remember? Repeat it if so, otherwise say no.",
            deadline=time.time() + LIMIT_SECONDS,
        )
    finally:
        await target.close(second)
    if word in said.reply.casefold():
        return (
            "A new session knew a word said only in an earlier one, so tries of a test can see "
            "each other. Tests that need a clean start need their own identity on this app."
        )
    return None


def setup_key(target: Target, config: TargetConfig) -> str:
    """Changes when the adapter's code or the target's settings do."""
    module = sys.modules.get(type(target).__module__)
    path = Path(module.__file__) if module and module.__file__ else None
    code = path.read_bytes() if path and path.exists() else b""
    settings = json.dumps(config.options, sort_keys=True, default=str)
    made = f"{config.adapter}\n{settings}\n".encode() + code
    return hashlib.sha256(made).hexdigest()


def passed_setups(data_dir: Path) -> dict[str, str]:
    try:
        found = json.loads((data_dir / _PASSED).read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def forget_setup(data_dir: Path, name: str) -> None:
    """The setup that passed no longer works: the next run checks it again. A passing check can't
    see a credential that changed, since a setup's key holds the secret's name and not its value."""
    found = passed_setups(data_dir)
    if found.pop(name, None) is not None:
        (data_dir / _PASSED).write_text(json.dumps(found, indent=2) + "\n")


def remember_setup(data_dir: Path, name: str, key: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    found = {**passed_setups(data_dir), name: key}
    (data_dir / _PASSED).write_text(json.dumps(found, indent=2) + "\n")
