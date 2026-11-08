"""Learning a target's page map on evalution's own copy of the app (ADR 0010, after v1): what the
test writer's page tools do to the page, and the check a run makes of an approved map before it
plays.

Every function here works on a copy of the app the target's proven isolation plan starts
(:class:`~evalution.runs.isolated.Copies`, one slot), and takes it down again, its stores put
back, however it ends. Without a proven plan nothing here runs (:func:`refusal`): a page is never
opened on a person's own app.

* :func:`look` opens a page signed in as a proposed ``sign_in`` says, and returns its
  accessibility snapshot. Nothing is clicked or typed.
* :func:`try_entry` tries one entry of a proposed map: found or not, and what changed on the page.
  Only the message box is typed into (a fixed harmless text, cleared again); Send is never
  clicked, and any request but a read is refused while it looks.
* :func:`rehearse` plays one real message through the whole map, as a run would: it spends one
  turn of the app. Its screenshots are kept in the file store.
* :func:`check` is the run's re-try of an approved map before it plays: every element a turn needs
  is found, none is acted on, no message is sent.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from evalution.adapters import make
from evalution.adapters.base import Capabilities, Target
from evalution.config import Config
from evalution.core.evidence import VIDEO
from evalution.runs import isolated
from evalution.runs.page.map import PageMap, read_map
from evalution.runs.page.target import (
    Missing,
    PageFiles,
    PageTarget,
    on_page,
    page_lacks,
)
from evalution.storage.files import Files

#: What a rehearsal says: one plain message, as a person might send it.
MESSAGE = "Hello, this is a test."
#: Seconds the rehearsal's one turn may take, the page's catching up included.
REHEARSAL_SECONDS = 180.0
#: Where a target's rehearsal screenshots are kept in the file store: run, then case.
SHOTS_CASE = "rehearsal"
#: The file store's "case" the check before a run keeps its screenshot under.
CHECK_CASE = "check"
_RUN_ID = re.compile(r"[^A-Za-z0-9_-]")
#: The run key each of these opens its session under, on the copy.
LOOKING = "page-map-look"


def shots_run(target: str) -> str:
    """The file store's "run" a target's rehearsal screenshots are kept under."""
    return f"page-map-{_RUN_ID.sub('-', target)}"[:64]


def refusal(config: Config, target: str, capabilities: Capabilities) -> str | None:
    """Why the page of *target* can't be opened now, in plain words; ``None`` when it can. A page
    is only ever opened on evalution's own copy, which a proven isolation plan starts (ADR 0009),
    and only with Playwright and a browser here."""
    found = isolated.footing(config, target, capabilities)
    if not found.managed:
        why = found.problem or "it has no isolation plan yet"
        return (
            f"{target}'s page is only ever opened on evalution's own copy of the app, and {why}. "
            "Setting up testing on the Test environment page makes the copy (or `evalution "
            f"isolate --target {target}`)."
        )
    if (off := isolated.off_copy(config, target)) is not None:
        return f"{target}'s page can't be opened on evalution's own copy: {off}"
    lacks = page_lacks()
    return f"{lacks[0].said} Run: {lacks[0].command}" if lacks else None


def lacking(config: Config, target: str, capabilities: Capabilities) -> Missing | None:
    """What :func:`refusal` refuses for, when it's a piece the page can offer to fix: no copy
    (Set up testing makes one), or Playwright or a browser (a command installs it). ``None``
    when nothing is missing, or what stands in the way is something else."""
    found = isolated.footing(config, target, capabilities)
    if not found.managed:
        why = found.problem or "it has no proven isolation plan yet"
        said = f"{target}'s page is only ever opened on evalution's own copy of the app, and {why}."
        return Missing("copy", said)
    if isolated.off_copy(config, target) is not None:
        return None
    lacks = page_lacks()
    return lacks[0] if lacks else None


@dataclass
class OnCopy:
    """A copy of the app started for learning its page: the adapter pointed at it, without any
    page map of its own, and the copy's page address."""

    config: Config
    target: str
    inner: Target
    url: str
    copies: isolated.Copies = field(repr=False)

    def page(self, page: PageMap) -> PageTarget:
        """The target played through *page* on this copy, signing in as slot 0's test account."""
        made = on_page(self.config.targets[self.target], self.inner, self.url, 0, page=page)
        assert isinstance(made, PageTarget)
        return made


@contextlib.asynccontextmanager
async def on_copy(config: Config, target: str, capabilities: Capabilities) -> AsyncIterator[OnCopy]:
    """A copy of *target* started from its proven plan, put back and taken down when done.

    :raises RuntimeError: It can't be (:func:`refusal`, in words).
    """
    if (why := refusal(config, target, capabilities)) is not None:
        raise RuntimeError(why)
    found = isolated.footing(config, target, capabilities)
    copies = isolated.Copies(config, target, found)
    try:
        await copies.start(1)
        slot = copies.slots[0]
        assert slot.adapter is not None
        inner = slot.adapter.inner if isinstance(slot.adapter, PageTarget) else slot.adapter
        if inner is None:
            # Reached through its page alone: its stand-in, which the page target puts aside.
            inner = make(config.targets[target])
        yield OnCopy(config, target, inner, slot.copy.url, copies)
    finally:
        await copies.close()


def scaffold(url: str, sign_in: Any) -> PageMap:
    """A map with only a page and a way to sign in, for looking: what it names besides is never
    used.

    :raises ConfigError: *url* or *sign_in* doesn't read.
    """
    return read_map(
        {
            "url": url,
            "sign_in": sign_in,
            "ready": {"role": "main", "name": "-"},
            "open_session": {"url": url},
            "actions": {
                "type": {"fill": {"role": "textbox", "name": "-"}},
                "send": {"click": {"role": "button", "name": "-"}},
            },
            "replies": {"in": {"role": "main"}},
        }
    )


async def _played(page: PageTarget, work: Any) -> Any:
    """*work* on a session of the inner adapter (page-only, one of its own), closed after, and the
    page target let go."""
    session = await page.looking_session(LOOKING)
    try:
        return await work(session)
    finally:
        with contextlib.suppress(Exception):
            await page.close(session)
        with contextlib.suppress(Exception):
            await page.dispose_browser()


async def look(
    config: Config, target: str, capabilities: Capabilities, url: str, sign_in: Any
) -> dict[str, Any]:
    """The copy's page at *url* (``{copy_page}``…), signed in as *sign_in* says: where it landed,
    its accessibility snapshot, and any request it tried that isn't a read (refused). Nothing is
    clicked or typed."""
    shown = scaffold(url, sign_in)
    async with on_copy(config, target, capabilities) as copy:
        page = copy.page(shown)
        return await _played(page, lambda session: page.look(session))


async def try_entry(
    config: Config, target: str, capabilities: Capabilities, page_map: PageMap, entry: str
) -> dict[str, Any]:
    """One entry of *page_map* tried on the copy's page (:meth:`PageTarget.try_entry`): never a
    click on Send, never a request that isn't a read."""
    async with on_copy(config, target, capabilities) as copy:
        page = copy.page(page_map)
        return await _played(page, lambda session: page.try_entry(session, entry))


@dataclass
class Rehearsed:
    """What a rehearsal of a map showed: whether the reply showed on the page, and the proof."""

    passed: bool
    reply: str = ""
    shown: str | None = None
    #: What the replies' region showed instead, when the reply didn't show.
    instead: str = ""
    error: str | None = None
    #: The screenshots kept, and where in the file store.
    shots: list[str] = field(default_factory=list)
    at: dict[str, Any] | None = None
    seconds: float = 0.0
    console: list[str] = field(default_factory=list)

    def said(self) -> str:
        """What it showed, in plain words."""
        if self.passed:
            shown = (self.shown or "")[:300]
            return f"Sent “{MESSAGE}” through the page; the reply showed: “{shown}”."
        if self.error:
            return f"Sent “{MESSAGE}” through the page, and it went wrong: {self.error}"
        instead = f" The replies showed instead: “{self.instead[-300:]}”." if self.instead else ""
        return (
            f"Sent “{MESSAGE}” through the page; the app answered “{self.reply[:200]}”, but the "
            f"page never showed it.{instead}"
        )


async def rehearse(
    config: Config,
    target: str,
    capabilities: Capabilities,
    page_map: PageMap,
    files: Files | None,
) -> Rehearsed:
    """Play one real message (:data:`MESSAGE`) through *page_map* on the copy, as a run would: it
    spends one turn of the app. Passed when the turn ended and its reply showed on the page."""
    async with on_copy(config, target, capabilities) as copy:
        page = copy.page(page_map)
        started = time.time()
        keep = (
            PageFiles(files, shots_run(target), SHOTS_CASE, int(started))
            if files is not None
            else None
        )
        session = await page.open("page-map-rehearsal")
        try:
            page.begin(session, keep)
            turn = await page.send(session, MESSAGE, deadline=started + REHEARSAL_SECONDS)
            record = await page.record(session, started)
        finally:
            with contextlib.suppress(Exception):
                await page.close(session)
            with contextlib.suppress(Exception):
                await page.dispose_browser()
    instead = record.turns[0].instead.text if record.turns else ""
    passed = turn.error is None and turn.shown is not None
    kept = list(record.files)
    if passed and keep is not None and record.video_policy == "failed" and VIDEO in kept:
        # As a run does: a video is kept only of a try that didn't pass.
        with contextlib.suppress(OSError, ValueError):
            await asyncio.to_thread(keep.files.delete, keep.run, keep.case, keep.n, VIDEO)
        kept.remove(VIDEO)
    return Rehearsed(
        passed=passed,
        reply=turn.reply,
        shown=turn.shown,
        instead=instead,
        error=turn.error,
        shots=kept,
        at={"run": keep.run, "case": keep.case, "n": keep.n} if keep and kept else None,
        seconds=round(time.time() - started, 1),
        console=[line.text.text for line in record.console if line.level == "error"][:5],
    )


@dataclass
class Checked:
    """What the check before a run found: what a turn needs that the page doesn't have, in words,
    and a screenshot of the page as it was when something was missing (*shot*, kept in the file
    store at *at*, ``{run, case, n}``)."""

    missing: list[str]
    shot: str | None = None
    at: dict[str, Any] | None = None


async def check(page: PageTarget, target: str = "", files: Files | None = None) -> Checked:
    """The run's re-try of an approved map on its copy's page, before it plays: what a turn needs
    that the page doesn't have, in words, with a screenshot of the page kept in *files* (under
    *target*'s page map screenshots) when something was missing. No message is sent."""
    keep = (
        PageFiles(files, shots_run(target), CHECK_CASE, int(time.time()))
        if files is not None and target
        else None
    )
    missing, shot = await page.checked("page-map-check", keep)
    at = {"run": keep.run, "case": keep.case, "n": keep.n} if keep and shot else None
    return Checked(missing, shot, at)
