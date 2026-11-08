"""The page target (ADR 0010, phase 3): a target played through its API adapter, with the app's own
page open on evalution's copy beside it, as its page map says.

The inner adapter still plays: its ``Turn.reply`` is what judging reads. The page is a second
look at the same turn. When the inner adapter can wait on a turn it didn't send (``watch``), the
page sends: it types the message and clicks Send, waits for the copy to answer the request that
carried it (an error status, or no answer by the deadline, is the turn's end), and the inner
adapter then waits for that turn. Else the inner adapter sends, and the page only watches. Either
way, once the turn is done the page gets ``catch_up_seconds`` for its busy signs to clear and the
reply to show in ``replies.in``: its words as the page renders them, in order, after the person's
message and after what the region already showed before the turn
(:func:`evalution.core.rendered.shown_span`), so neither the message's echo nor an earlier reply
with the same words is taken for it. ``Turn.shown`` is the page's text where it showed, the newest;
``None`` when the reply never showed, with the last of what did in
``Session.extra["page"]["last_shown"]``.

One browser per page target, so one per copy, launched on first use: Playwright's own Chromium when
it is installed, else the system Chrome (``EVALUTION_CHROME``, or the usual place on a Mac).
Nothing is ever downloaded. A new browser context per session, and again on reset, so cookies,
local storage and IndexedDB never carry from one try to the next. Headers are added only to
requests for the copy's origin: the map's sign-in headers and the try's ``X-Evalution-Run`` and
``X-Evalution-Try``. Any other origin the page reaches gets nothing added.

What the page did is recorded per try (:meth:`PageTarget.record`, a
:class:`~evalution.core.evidence.PageRecord`): per turn, one for every ``send`` in order, when its
reply showed or what showed instead, and a screenshot taken after it (sign-in boxes masked) into
the run's file store; the console's errors and warnings and uncaught errors, scrubbed of the
target's secrets; requests to the copy's origin that failed (400 and up, or no answer), by path
without the query.

And a video of the try, unless the map's ``video`` is ``never``: each context records its pages
(Playwright's ffmpeg, at the viewport's size), and a form sign-in happens on a page of its own,
closed before the try's page opens, so no video shows the form being filled. ``record`` closes the
try's page, which finishes its video, and moves it into the file store as
:data:`~evalution.core.evidence.VIDEO`; the next turn in the same session opens a new page on the
conversation. A video over :data:`VIDEO_BYTES` is dropped. Without ffmpeg the try plays on with no
video and says why; a try whose page was never recorded (a restart) says its video is missing. A
passed try's video is deleted once judged when the map keeps it only for failures (the runner's).

A form or cookie sign-in uses the slot's test account: the target's ``page_accounts``, a list of
``{email, password_env}`` (one per slot, the password read as any ``*_env`` is), as the copy's
setup has no accounts of its own.

``Session.extra["page"]`` holds JSON only: the key of the session's context. ``close`` and
``cancel`` work from it alone, after a restart too, when there is no browser: a key this process
doesn't hold is a no-op. Playwright is imported only here, and only when a browser is needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import importlib.util
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from evalution.adapters.base import (
    NO_FILES,
    NO_STOP,
    Capabilities,
    MemoryRead,
    Person,
    Session,
    StateResult,
    Target,
    TargetGone,
    Turn,
    failure_of_status,
    status_may_have_passed_on,
)
from evalution.adapters.http import RUN_HEADER, TRY_HEADER
from evalution.config import ConfigError, KeyRing, TargetConfig
from evalution.core.evidence import (
    VIDEO,
    Clipped,
    ConsoleLine,
    FailedRequest,
    Failure,
    PageRecord,
    PageTurn,
)
from evalution.core.rendered import page_words, shown_span
from evalution.core.trace import Section
from evalution.isolation.launch import scrub
from evalution.runs.page.map import Locator, PageMap, PageMapBroken, fill_urls, page_map_of
from evalution.storage.files import Files

_logger = logging.getLogger(__name__)

#: Where Chrome is on a Mac, used when ``EVALUTION_CHROME`` doesn't say.
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
#: Seconds the page gets to show an element its map names, before the map is called broken.
ELEMENT_SECONDS = 10.0
#: Seconds a page gets to load.
LOAD_SECONDS = 30.0
#: How much of what the replies' region showed is kept, when the reply didn't show.
SHOWN_KEPT = 2000
#: A turn's error when a case carries a fresh token: a page has nowhere to keep it unseen.
NO_FRESH = "a page can't carry a fresh token where the person can't see it"
NO_PLAYWRIGHT = (
    "page mode needs Playwright, which this install doesn't have: install evalution with its "
    "page extra (uv sync --extra page)"
)
NO_BROWSER = (
    "page mode needs a browser, and none was found: install Google Chrome, set EVALUTION_CHROME "
    "to a Chrome or Chromium, or run `playwright install chromium`"
)
#: The page's size in the browser, and its video's.
VIEWPORT = {"width": 1280, "height": 720}
#: The largest video a try keeps; a bigger one is dropped, and the try says so.
VIDEO_BYTES = 50_000_000
#: Seconds the browser gets to close a try's page and finish its video.
VIDEO_SECONDS = 30.0
#: Why a page try has no video.
NO_VIDEO = "video needs Playwright's ffmpeg: run `playwright install ffmpeg`"
CUT_OFF = "the try was cut off before its video was finished"
UNFINISHED = "the browser didn't finish the try's video"
NOT_KEPT = "the try's video couldn't be kept"
NO_PAGE = "nothing of the try was on the page to record"
#: The shortest value scrubbed from what the page wrote: shorter ones are words, not secrets.
_SCRUB_SHORTEST = 6
#: Methods a page sends a message with.
_SENDS = frozenset({"POST", "PUT", "PATCH"})
#: Methods that only read: all a page may do while its map is looked at or tried.
_READS = frozenset({"GET", "HEAD", "OPTIONS"})
#: What a message box is filled with when its entry is tried: never sent, cleared after.
HARMLESS = "evalution is checking where this box is"
#: Seconds an element may take to show while a map is tried or checked before a run.
LOCATE_SECONDS = 5.0
#: How much of an accessibility snapshot, or of a diff of two, is kept.
SNAPSHOT_KEPT = 12_000
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
#: Plain language names a test person may give, as a browser's locale.
_LANGUAGES = {
    "english": "en",
    "portuguese": "pt",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "dutch": "nl",
    "japanese": "ja",
    "chinese": "zh",
}


class PageUnavailable(RuntimeError):
    """Page mode can't start here: no Playwright, or no browser. Says what to install."""


def page_missing() -> str | None:
    """What this install lacks for page mode, in words; ``None`` when Playwright is there. Whether
    a browser is there is known only once one is launched."""
    if importlib.util.find_spec("playwright") is None:
        return NO_PLAYWRIGHT
    return None


#: What sets up everything page mode needs on this computer; ``./install.sh`` runs it.
PAGE_SETUP = (
    "uv sync --project server --extra page && "
    "uv run --project server playwright install chromium ffmpeg"
)


def playwright_cache() -> Path:
    """Where Playwright keeps the browsers and ffmpeg it installs: ``PLAYWRIGHT_BROWSERS_PATH``,
    else its own place for this kind of computer."""
    if found := os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return Path(found)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    if sys.platform == "win32":
        return home / "AppData" / "Local" / "ms-playwright"
    return home / ".cache" / "ms-playwright"


def page_parts() -> dict[str, bool]:
    """What page mode has here, without launching anything: Playwright, a browser (Playwright's
    Chromium or the system Chrome) and Playwright's ffmpeg, which a try's video needs."""
    cache = playwright_cache()

    def installed(*prefixes: str) -> bool:
        return cache.is_dir() and any(
            item.is_dir() and item.name.startswith(prefixes) for item in cache.iterdir()
        )

    return {
        "playwright": page_missing() is None,
        "browser": installed("chromium-", "chromium_headless_shell-")
        or system_chrome() is not None,
        "ffmpeg": installed("ffmpeg-"),
    }


def system_chrome() -> str | None:
    """The system Chrome to use when Playwright has no Chromium of its own, if it is there."""
    path = os.environ.get("EVALUTION_CHROME") or MAC_CHROME
    return path if Path(path).is_file() else None


def page_capabilities(inner: Capabilities, page: PageMap) -> Capabilities:
    """What a target played through *page* can do: what its adapter can, without files or Stop
    (v1). A new conversation is its adapter's, and the page then opens it, so it needs no New chat
    on the page. It can't be watched. One try at a time: tries played side by side through
    relay-lite's page stalled (2026-11-08, cause not yet known), the same tries one at a time
    and through its API side by side didn't, so a page run plays one slot until that is
    understood."""
    return replace(inner, files=False, stop=False, watch=False, parallel=False)


def on_page(
    target: TargetConfig, adapter: Target, copy_url: str, slot: int = 0, page: PageMap | None = None
) -> Target:
    """*adapter*, played through the target's page on the copy at *copy_url* when it has a page
    map (or *page*, a map being learned, in its place), signing in as slot *slot*'s test account;
    else *adapter* as it is.

    :raises ConfigError: The target's page map, or its ``page_accounts``, doesn't read.
    """
    page = page or page_map_of(target)
    if page is None:
        return adapter
    accounts = target.options.get("page_accounts") or []
    where = f"targets.{target.name}.page_accounts"
    if not isinstance(accounts, list) or not all(
        isinstance(a, dict)
        and isinstance(a.get("email"), str)
        and isinstance(a.get("password_env"), str)
        for a in accounts
    ):
        raise ConfigError(f"{where}: a list of {{email, password_env}}, one per slot")
    account = None
    if slot < len(accounts):
        found = accounts[slot]
        account = {"email": found["email"], "password": target.keys.get(found["password_env"])}
    told = [target.keys.get(name) for name in _env_names(target.options)]
    return PageTarget(
        adapter,
        page,
        copy_url,
        keys=target.keys,
        account=account,
        secrets=[value for value in told if value],
    )


def _env_names(block: Any) -> list[str]:
    """The names a target's ``*_env`` options read, however deep: its secrets, to scrub."""
    found: list[str] = []
    if isinstance(block, dict):
        for key, value in block.items():
            if str(key).endswith("_env"):
                if isinstance(value, str):
                    found.append(value)
                elif isinstance(value, dict):
                    found += [v for v in value.values() if isinstance(v, str)]
            elif key == "page_accounts" and isinstance(value, list):
                found += [str(a.get("password_env")) for a in value if isinstance(a, dict)]
            else:
                found += _env_names(value)
    elif isinstance(block, list):
        for value in block:
            found += _env_names(value)
    return found


@dataclass(frozen=True)
class PageFiles:
    """Where a try's screenshots and video go: the run's file store, under its run, case and
    try."""

    files: Files
    run: str
    case: str
    n: int


def _kept_video(made: Path, keep: PageFiles) -> None:
    """Move the finished video *made* into the try's files, as :data:`VIDEO`."""
    where = keep.files.path(keep.run, keep.case, keep.n, VIDEO)
    part = where.with_name(f".{VIDEO}.part")
    shutil.move(made, part)
    part.chmod(0o600)
    part.replace(where)


@dataclass
class _Record:
    """What a session's page did since it was last recorded, as it happened (absolute times)."""

    #: ``(level, text, at)``, scrubbed.
    console: list[tuple[str, str, float]] = field(default_factory=list)
    #: ``(method, path, status, at)``.
    failed: list[tuple[str, str, int | None, float]] = field(default_factory=list)
    #: One per ``send``: ``{done, shown_at, instead, shot}``.
    turns: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    keep: PageFiles | None = None


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return "", "", None
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), port or {"http": 80, "https": 443}.get(scheme)


def _said(found: Locator) -> str:
    """An element of the map, as a person would name it."""
    if found.test_id is not None:
        return f"element with test id {found.test_id!r}"
    if found.attr is not None:
        return f"element with {found.attr}={found.equals!r}"
    return f"{found.role} {found.name!r}" if found.name else str(found.role)


def _api() -> Any:
    """Playwright's async API, imported only now.

    :raises PageUnavailable: It isn't installed.
    """
    try:
        from playwright import async_api
    except ImportError as exc:
        raise PageUnavailable(NO_PLAYWRIGHT) from exc
    return async_api


class PageTarget(Target):
    """*inner*, played with the app's page open on evalution's copy (see the module's docstring).

    :param page: The target's page map; its ``{copy_page}`` is filled with *copy_page*.
    :param keys: Where ``{secret:NAME}`` in a sign-in header is read, as ``*_env`` options are.
    :param account: The slot's test account, ``{email, password}``, for ``{account.*}``.
    :param secrets: Values to scrub from what the page writes to its console.
    :param element_seconds: How long an element the map names may take to show.
    """

    def __init__(
        self,
        inner: Target,
        page: PageMap,
        copy_page: str,
        *,
        keys: KeyRing | None = None,
        account: dict[str, str | None] | None = None,
        secrets: list[str] | None = None,
        element_seconds: float = ELEMENT_SECONDS,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.person = inner.person
        self.page = fill_urls(page, copy_page)
        self.capabilities = page_capabilities(inner.capabilities, page)
        self.keys = keys or KeyRing()
        self.account = account or {}
        self.element_seconds = element_seconds
        self._secrets = {s for s in secrets or () if len(s) >= _SCRUB_SHORTEST}
        self._copy = _origin(copy_page)
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        #: Each session's context and its page, by the key ``Session.extra["page"]`` holds. The
        #: page is ``None`` once a try's was closed to finish its video, until the next turn.
        self._open: dict[str, tuple[Any, Any]] = {}
        #: What each of those pages did, by the same key.
        self._records: dict[str, _Record] = {}
        #: Where each context records its videos, by the same key; removed with the context.
        self._videos: dict[str, Path] = {}
        #: Whether Playwright's ffmpeg is here; ``None`` until a context tried to record.
        self._ffmpeg: bool | None = None
        #: The writes refused from a page that may only look (:meth:`_new_context`), by key.
        self._blocked: dict[str, list[str]] = {}

    def persona(self, person: Person) -> None:
        """The inner adapter plays as *person*, and the page's language and time zone follow."""
        self.person = person
        self.inner.persona(person)

    # The browser

    async def _launched(self) -> Any:
        """The browser, launched on first use.

        :raises PageUnavailable: No Playwright, or no browser to launch.
        """
        async with self._lock:
            if self._browser is not None:
                return self._browser
            api = _api()
            playwright = await api.async_playwright().start()
            try:
                own = Path(playwright.chromium.executable_path)
                if await asyncio.to_thread(own.is_file):
                    self._browser = await playwright.chromium.launch(headless=True)
                elif (chrome := system_chrome()) is not None:
                    self._browser = await playwright.chromium.launch(
                        headless=True, executable_path=chrome
                    )
                else:
                    raise PageUnavailable(NO_BROWSER)
            except BaseException:
                await playwright.stop()
                raise
            self._playwright = playwright
            return self._browser

    def _context_options(self) -> dict[str, Any]:
        """The browser context's locale and time zone, from the test person where they read."""
        person = self.person
        options: dict[str, Any] = {}
        if person is None:
            return options
        language = person.language.strip()
        locale = language if _TAG.match(language) else _LANGUAGES.get(language.lower())
        if locale:
            options["locale"] = locale
        if person.timezone:
            with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
                ZoneInfo(person.timezone)
                options["timezone_id"] = person.timezone
        return options

    def _on_copy(self, url: str) -> bool:
        return _origin(url) == self._copy

    def _filled(self, text: str, session: Session) -> str:
        """*text* with the session's placeholders filled.

        :raises ValueError: One it needs has no value here.
        """
        account = {**self.account, **(session.extra.get("account") or {})}

        def one(found: re.Match[str]) -> str:
            name = found.group(1)
            if name == "person.email":
                value = session.extra.get("email")
                where = "the target's adapter gives its sessions no email"
            elif name.startswith("account."):
                value = account.get(name.removeprefix("account."))
                where = "this copy has no seeded account for the session"
            elif name.startswith("secret:"):
                value = self.keys.get(name.removeprefix("secret:"))
                where = f"{name.removeprefix('secret:')} isn't set"
            else:
                return found.group(0)
            if not value:
                raise ValueError(f"the page map uses {{{name}}}, and {where}")
            if name == "account.password" or name.startswith("secret:"):
                self._secret(str(value))
            return str(value)

        return _PLACEHOLDER.sub(one, text)

    def _headers(self, session: Session) -> dict[str, str]:
        """What every request to the copy's origin carries for *session*."""
        sign_in = self.page.sign_in.headers or {}
        added = {name: self._filled(value, session) for name, value in sign_in.items()}
        added[RUN_HEADER] = str(session.extra.get("run", ""))
        added[TRY_HEADER] = str(session.extra.get("try", ""))
        return added

    async def _new_context(self, session: Session, *, looking: bool = False) -> Any:
        """A fresh context for *session*, signed in, with a page open on nothing yet. *looking*:
        once signed in, the page may only read, and any other request it makes is refused and
        kept (:meth:`blocked`): a map looked at or tried never sends anything."""
        await self._drop(session)
        added = self._headers(session)
        browser = await self._launched()
        key = f"{session.id}:{secrets.token_hex(4)}"
        context, page = await self._context(browser, key)
        record = _Record()
        self._open[key] = (context, page)
        self._records[key] = record
        self._listen(page, record)
        session.extra["page"] = {"context": key}

        async def add(route: Any) -> None:
            await route.continue_(headers={**route.request.headers, **added})

        await context.route(self._on_copy, add)
        await self._sign_in(context, page, session)
        if self.page.sign_in.form is not None:
            # The form was filled on a page of its own: the try's page, and its video, start
            # signed in, sharing the context's cookies and storage.
            signed_in = page
            page = await context.new_page()
            self._open[key] = (context, page)
            self._listen(page, record)
            await signed_in.close()
        if looking:
            blocked = self._blocked.setdefault(key, [])

            async def only_reads(route: Any) -> None:
                request = route.request
                if request.method in _READS:
                    await route.fallback()
                    return
                blocked.append(f"{request.method} {urlsplit(request.url).path}")
                await route.abort()

            # Routes added later run first: this one sees every request before the headers do.
            await context.route("**/*", only_reads)
        return page

    async def _context(self, browser: Any, key: str) -> tuple[Any, Any]:
        """A new context and its first page, recording video when the map keeps any and
        Playwright's ffmpeg is here (else without, and :attr:`_ffmpeg` says so)."""
        options = {**self._context_options(), "viewport": VIEWPORT}
        if self.page.video != "never" and self._ffmpeg is not False:
            folder = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="evalution-video-"))
            context = None
            try:
                context = await browser.new_context(
                    **options, record_video_dir=str(folder), record_video_size=VIEWPORT
                )
                page = await context.new_page()
            except Exception as exc:
                if context is not None:
                    with contextlib.suppress(Exception):
                        await context.close()
                await asyncio.to_thread(shutil.rmtree, folder, True)
                if not isinstance(exc, _api().Error) or "ffmpeg" not in str(exc):
                    raise
                _logger.warning("evals: page tries have no video here: %s", NO_VIDEO)
                self._ffmpeg = False
            else:
                self._ffmpeg = True
                self._videos[key] = folder
                return context, page
        context = await browser.new_context(**options)
        return context, await context.new_page()

    def _secret(self, value: str) -> None:
        if len(value) >= _SCRUB_SHORTEST:
            self._secrets.add(value)

    def _scrubbed(self, text: str) -> str:
        return scrub(text, sorted(self._secrets, key=len, reverse=True))

    def _listen(self, page: Any, record: _Record) -> None:
        """Keep in *record* what *page* does from now: its console, its uncaught errors, and the
        requests to the copy that fail."""

        def console(message: Any) -> None:
            level = {"error": "error", "warning": "warning"}.get(message.type)
            if level:
                record.console.append((level, self._scrubbed(message.text), time.time()))

        def uncaught(error: Any) -> None:
            said = f"{getattr(error, 'name', '') or 'Error'}: {getattr(error, 'message', error)}"
            record.console.append(("error", self._scrubbed(said), time.time()))

        def answered(response: Any) -> None:
            if response.status >= 400 and self._on_copy(response.url):
                path = urlsplit(response.url).path
                record.failed.append((response.request.method, path, response.status, time.time()))

        def unanswered(request: Any) -> None:
            # A request the page itself let go of (a navigation, Stop) isn't the copy failing.
            if self._on_copy(request.url) and "ERR_ABORTED" not in str(request.failure or ""):
                path = urlsplit(request.url).path
                record.failed.append((request.method, path, None, time.time()))

        page.on("console", console)
        page.on("pageerror", uncaught)
        page.on("response", answered)
        page.on("requestfailed", unanswered)

    def _record_of(self, session: Session) -> _Record | None:
        key = (session.extra.get("page") or {}).get("context")
        return self._records.get(key) if isinstance(key, str) else None

    def begin(self, session: Session, keep: PageFiles | None) -> None:
        """A try starts in *session*: its screenshots go to *keep* (none when ``None``), and its
        turns are counted from the first. What the page did while it opened stays in the record."""
        record = self._record_of(session)
        if record is not None:
            record.keep = keep
            record.turns.clear()
            record.files.clear()

    async def record(self, session: Session, t0: float, *, cut_short: bool = False) -> PageRecord:
        """What the page did of the try that started at *t0*, times from it, and forget it. A try
        *cut_short* (out of time, stopped), or with a turn still going, is ``interrupted``. When
        the map keeps video and the try has somewhere to keep files, its page is closed to finish
        its video, which is kept as :data:`VIDEO` (see the module's docstring)."""
        found = self._record_of(session)
        policy = None if self.page.video == "never" else self.page.video
        if found is None:
            return PageRecord(interrupted=True, video_missing=CUT_OFF if policy else None)
        files, missing = list(found.files), None
        if policy is not None and found.keep is not None:
            missing = await self._keep_video(session, found.keep)
            if missing is None:
                files.append(VIDEO)
        turns = tuple(
            PageTurn(
                shown_at=None if step["shown_at"] is None else round(step["shown_at"] - t0, 3),
                instead=Clipped(text=step["instead"]),
                shot=step["shot"],
            )
            for step in found.turns
        )
        made = PageRecord(
            turns=turns,
            console=tuple(
                ConsoleLine(level=level, text=Clipped(text=text), at=round(at - t0, 3))
                for level, text, at in found.console
            ),
            failed_requests=tuple(
                FailedRequest(method=method, path=path, status=status, at=round(at - t0, 3))
                for method, path, status, at in found.failed
            ),
            files=tuple(files),
            video_missing=missing,
            video_policy=policy if VIDEO in files else None,
            interrupted=cut_short or any(not step["done"] for step in found.turns),
        )
        found.console.clear()
        found.failed.clear()
        found.turns.clear()
        found.files.clear()
        return made

    async def _keep_video(self, session: Session, keep: PageFiles) -> str | None:
        """Close the try's page, which finishes its video, and keep that as the try's
        :data:`VIDEO`. Why there is none; ``None`` when it was kept."""
        key = session.extra["page"]["context"]
        context, page = self._open.get(key, (None, None))
        if page is None:
            return NO_PAGE
        video = page.video
        if video is None:
            return NO_VIDEO
        # The next turn in this session, if any, opens a page of its own (:meth:`_live`).
        self._open[key] = (context, None)
        api = _api()
        made = self._videos[key] / f"{secrets.token_hex(4)}.webm"
        try:
            await asyncio.wait_for(page.close(), VIDEO_SECONDS)
            await asyncio.wait_for(video.save_as(made), VIDEO_SECONDS)
            size = (await asyncio.to_thread(made.stat)).st_size
        except (api.Error, TimeoutError, OSError):
            _logger.warning("evals: the video of %s wasn't finished", session.id, exc_info=True)
            return UNFINISHED
        if not size:
            return UNFINISHED
        if size > VIDEO_BYTES:
            await asyncio.to_thread(made.unlink, True)
            most = VIDEO_BYTES // 1_000_000
            return f"the video was {size / 1_000_000:.1f} MB, more than the {most} MB a try keeps"
        try:
            await asyncio.to_thread(_kept_video, made, keep)
        except (OSError, ValueError):
            _logger.warning("evals: the video of %s not kept", session.id, exc_info=True)
            return NOT_KEPT
        return None

    async def _shoot(self, page: Any, record: _Record, index: int) -> str | None:
        """A screenshot of the page after turn *index*, with sign-in boxes masked, kept as the
        try's file; its name, or ``None`` when there is nowhere to keep it or it failed."""
        keep = record.keep
        if keep is None:
            return None
        form = self.page.sign_in.form
        mask = [self._locator(page, box) for box in form.fill] if form is not None else []
        try:
            png = await page.screenshot(mask=mask, timeout=10_000)
        except _api().Error:
            _logger.warning("evals: no screenshot of turn %s", index, exc_info=True)
            return None
        name = f"turn-{index}.png"
        try:
            await asyncio.to_thread(keep.files.put, keep.run, keep.case, keep.n, name, png)
        except (OSError, ValueError):
            _logger.warning("evals: screenshot %s not kept", name, exc_info=True)
            return None
        record.files.append(name)
        return name

    async def _sign_in(self, context: Any, page: Any, session: Session) -> None:
        sign_in = self.page.sign_in
        if sign_in.cookie is not None:
            value = self._filled(sign_in.cookie.value, session)
            url = self.page.url
            await context.add_cookies([{"name": sign_in.cookie.name, "value": value, "url": url}])
        elif sign_in.form is not None:
            form = sign_in.form
            await self._goto(page, form.url)
            for field in form.fill:
                box = await self._find(page, field)
                await self._act(box.fill(self._filled(field.value, session)), field)
            submit = await self._find(page, form.submit)
            await self._act(submit.click(), form.submit)
            done = form.done.url_matches
            try:
                await page.wait_for_url(lambda url: done in urlsplit(url).path, timeout=self._ms())
            except _api().TimeoutError as exc:
                raise PageMapBroken(f"signing in never reached {done}") from exc

    def _ms(self, deadline: float | None = None) -> float:
        seconds = self.element_seconds
        if deadline is not None:
            seconds = max(min(seconds, deadline - time.time()), 0.1)
        return seconds * 1000

    def _locator(self, scope: Any, found: Locator) -> Any:
        if found.test_id is not None:
            return scope.get_by_test_id(found.test_id)
        if found.attr is not None:
            return scope.locator(f"[{found.attr}={_quoted(str(found.equals))}]")
        if found.name is None:
            return scope.get_by_role(found.role)
        return scope.get_by_role(found.role, name=found.name, exact=True)

    async def _find(self, scope: Any, found: Locator, deadline: float | None = None) -> Any:
        """The first element *found* names, once it shows.

        :raises PageMapBroken: It didn't show in time.
        """
        element = self._locator(scope, found).first
        try:
            await element.wait_for(state="visible", timeout=self._ms(deadline))
        except _api().TimeoutError as exc:
            raise PageMapBroken(f"the page has no {_said(found)}") from exc
        return element

    async def _act(self, action: Any, found: Locator) -> None:
        """A click or a fill on an element that showed.

        :raises PageMapBroken: It can't be done to that element.
        """
        api = _api()
        try:
            await action
        except api.Error as exc:
            if "has been closed" in str(exc):
                raise TargetGone(f"the page closed: {exc}") from exc
            raise PageMapBroken(f"the page's {_said(found)} didn't take it") from exc

    async def _goto(self, page: Any, url: str) -> None:
        """:raises ConnectionError: The page didn't load."""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=LOAD_SECONDS * 1000)
        except _api().Error as exc:
            raise ConnectionError(f"the page at {url} didn't load: {exc}") from exc

    async def _show(self, page: Any, session: Session) -> None:
        """Open *session*'s conversation on the page, and wait until it is ready."""
        opening = self.page.open_session
        if opening.url is not None:
            await self._goto(page, opening.url.replace("{session}", quote(session.id, safe="")))
        else:
            assert opening.pick is not None
            await self._goto(page, self.page.url)
            listed = await self._find(page, opening.pick.in_)
            item = self._locator(listed, opening.pick.item).filter(has_text=session.id)
            try:
                await item.first.wait_for(state="visible", timeout=self._ms())
            except _api().TimeoutError as exc:
                raise PageMapBroken(
                    f"the page's {_said(opening.pick.in_)} doesn't list the conversation"
                ) from exc
            await self._act(item.first.click(), opening.pick.item)
        await self._find(page, self.page.ready)

    async def _live(self, session: Session) -> Any:
        """*session*'s page; a new one in its context, showing its conversation, when the last
        try's was closed to finish its video.

        :raises TargetGone: This process holds no context for *session*.
        """
        key = (session.extra.get("page") or {}).get("context")
        found = self._open.get(key) if isinstance(key, str) else None
        if found is None:
            raise TargetGone("the session's page isn't open")
        context, page = found
        if page is None:
            page = await context.new_page()
            self._open[key] = (context, page)
            if (record := self._records.get(key)) is not None:
                self._listen(page, record)
            await self._show(page, session)
        return page

    async def _drop(self, session: Session) -> None:
        """Close *session*'s context, if this process holds it, and its videos with it."""
        key = (session.extra.get("page") or {}).get("context")
        if not isinstance(key, str):
            return
        found = self._open.pop(key, None)
        self._records.pop(key, None)
        self._blocked.pop(key, None)
        folder = self._videos.pop(key, None)
        try:
            if found is not None:
                await found[0].close()
        finally:
            if folder is not None:
                await asyncio.to_thread(shutil.rmtree, folder, True)

    # Learning the map: looking and trying, never sending

    def blocked(self, session: Session) -> list[str]:
        """The requests *session*'s page tried to send while it could only look, refused."""
        key = (session.extra.get("page") or {}).get("context")
        return list(self._blocked.get(key, [])) if isinstance(key, str) else []

    async def _snapshot(self, page: Any) -> str:
        """The page's accessibility tree as Playwright writes it (roles and names), cut."""
        try:
            found = await page.locator("body").aria_snapshot(timeout=LOCATE_SECONDS * 1000)
        except _api().Error as exc:
            return f"(no snapshot: {str(exc).splitlines()[0][:200]})"
        return str(found)[:SNAPSHOT_KEPT]

    async def look(self, session: Session, url: str | None = None) -> dict[str, Any]:
        """Open *url* (the map's own, by default) signed in as the map says, and say what the
        page shows: where it landed and its accessibility snapshot. Nothing is clicked or typed,
        and any request but a read is refused (:meth:`blocked`).

        :raises PageMapBroken: Signing in didn't get through.
        :raises ConnectionError: The page didn't load.
        """
        page = await self._new_context(session, looking=True)
        try:
            await self._goto(page, url or self.page.url)
            with contextlib.suppress(_api().Error):
                await page.wait_for_load_state("networkidle", timeout=LOCATE_SECONDS * 1000)
            return {
                "at": urlsplit(page.url).path or "/",
                "snapshot": await self._snapshot(page),
                "blocked": self.blocked(session),
            }
        finally:
            await self._drop(session)

    async def try_entry(self, session: Session, entry: str) -> dict[str, Any]:
        """Try one entry of the map on the page: whether what it names is there, and what changed
        on the page when it was used. Only ``actions.type`` acts: it fills the box with
        :data:`HARMLESS` and clears it again. Send, New chat and Stop are found, never clicked,
        and any request but a read is refused (:meth:`blocked`): trying a map never sends a
        message.

        :raises ValueError: *entry* isn't one the map has.
        """
        if entry not in TRIED:
            raise ValueError(f"entry: one of {', '.join(sorted(TRIED))}")
        page = await self._new_context(session, looking=True)
        said: dict[str, Any] = {"entry": entry, "found": False}
        before = after = ""
        try:
            if entry == "sign_in":
                await self._goto(page, self.page.url)
                said["found"] = True
                said["at"] = urlsplit(page.url).path or "/"
                after = await self._snapshot(page)
            elif entry in ("url", "ready"):
                await self._goto(page, self.page.url)
                await self._find(page, self.page.ready)
                said["found"] = True
            elif entry == "open_session":
                await self._show(page, session)
                said["found"] = True
                said["at"] = urlsplit(page.url).path or "/"
            else:
                await self._show(page, session)
                before = await self._snapshot(page)
                said.update(await self._tried(page, entry))
                after = await self._snapshot(page)
        except (PageMapBroken, ConnectionError) as exc:
            said["problem"] = str(exc)
        finally:
            said["blocked"] = self.blocked(session)
            await self._drop(session)
        if before or after:
            changed = difflib.unified_diff(
                before.splitlines(), after.splitlines(), "before", "after", lineterm="", n=1
            )
            said["changed"] = "\n".join(changed)[:SNAPSHOT_KEPT] or "(nothing changed)"
        return said

    async def _tried(self, page: Any, entry: str) -> dict[str, Any]:
        """One entry tried on the conversation's page: found or not, and what of it showed."""
        actions, replies = self.page.actions, self.page.replies
        clicks = {
            "actions.send": actions.send,
            "actions.new_chat": actions.new_chat,
            "actions.stop": actions.stop,
            "actions.attach": actions.attach,
        }
        if entry == "actions.type":
            box = await self._find(page, actions.type.fill)
            await self._act(box.fill(HARMLESS), actions.type.fill)
            filled = await box.input_value(timeout=LOCATE_SECONDS * 1000)
            await self._act(box.fill(""), actions.type.fill)
            return {"found": True, "took_text": filled == HARMLESS}
        if entry in clicks:
            click = clicks[entry]
            if click is None:
                return {"found": False, "problem": f"the map has no {entry}"}
            element = await self._find(page, click.click)
            return {"found": True, "enabled": await element.is_enabled(), "clicked": False}
        if entry == "replies.in":
            region = await self._find(page, replies.in_)
            text = str(await region.inner_text(timeout=LOCATE_SECONDS * 1000))
            return {"found": True, "shows": text[-500:]}
        if entry in ("replies.item", "replies.mine"):
            region = await self._find(page, replies.in_)
            mark = replies.item if entry == "replies.item" else replies.mine
            if mark is None:
                return {"found": False, "problem": f"the map has no {entry}"}
            count = await self._locator(region, mark).count()
            return {"found": count > 0, "count": count}
        # Busy: each sign shows only while a reply is on its way, so none is expected now.
        showing = [
            _said(mark)
            for mark in self.page.busy
            if await self._locator(page, mark).first.is_visible()
        ]
        return {"found": True, "showing_now": showing}

    async def check(self, run: str) -> list[str]:
        """Find, on the copy's page, each element the map names that a turn needs (its ready
        sign, the message box, Send, the replies, and New chat when it has one) without acting on
        any, and with any request but a read refused: no message is sent. What wasn't there, in
        words; nothing when all of it was.

        :raises ConnectionError: The page didn't load.
        :raises PageUnavailable: No Playwright or no browser here.
        """
        session = await self.inner.open(run)
        missing: list[str] = []
        try:
            page = await self._new_context(session, looking=True)
            try:
                await self._show(page, session)
            except PageMapBroken as exc:
                return [str(exc).removeprefix("the page has no ")]
            actions = self.page.actions
            wanted = [actions.type.fill, actions.send.click, self.page.replies.in_]
            if actions.new_chat is not None:
                wanted.append(actions.new_chat.click)
            for found in wanted:
                try:
                    await self._find(page, found)
                except PageMapBroken:
                    missing.append(_said(found))
        finally:
            with contextlib.suppress(Exception):
                await self._drop(session)
            with contextlib.suppress(Exception):
                await self.inner.close(session)
        return missing

    # The contract

    async def open(self, run: str) -> Session:
        """The inner adapter's session, with its conversation open on the page.

        :raises PageMapBroken: The page lacks what the map names.
        :raises ConnectionError: The page didn't load.
        :raises PageUnavailable: No Playwright or no browser here.
        """
        session = await self.inner.open(run)
        try:
            page = await self._new_context(session)
            await self._show(page, session)
        except BaseException:
            with contextlib.suppress(Exception):
                await self._drop(session)
            with contextlib.suppress(Exception):
                await self.inner.close(session)
            raise
        return session

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
        # One step of the record per send, so its turns line up with the try's.
        record = self._record_of(session)
        step = {"done": False, "shown_at": None, "instead": "", "shot": None}
        if record is not None:
            record.turns.append(step)
        if files:
            turn = Turn("", started, started, error=NO_FILES)
        elif stop_after:
            turn = Turn("", started, started, error=NO_STOP)
        elif session.extra.get("fresh"):
            turn = Turn("", started, started, error=NO_FRESH, failure=Failure.NOT_SUPPORTED)
        else:
            page = await self._live(session)
            turn = await self._played(page, session, message, started, deadline, step)
            if record is not None:
                step["shot"] = await self._shoot(page, record, len(record.turns))
        step["done"] = True
        return turn

    async def _played(
        self,
        page: Any,
        session: Session,
        message: str,
        started: float,
        deadline: float,
        step: dict[str, Any],
    ) -> Turn:
        """The turn, with the page sending or watching, then caught up with."""
        # What the replies' region held before: an earlier reply with the same words isn't this.
        before = await self._region_text(page)
        if self.inner.capabilities.watch:
            # The page sends, as a person would; the inner adapter waits for that turn.
            sent = await self._page_sends(page, message, started, deadline)
            if isinstance(sent, Turn):
                return sent
            turn = await self.inner.watch(session, sent, deadline=deadline)
        else:
            # The inner adapter sends; the page has the conversation open and watches.
            turn = await self.inner.send(session, message, deadline=deadline)
        if turn.error is None:
            step["shown_at"] = await self._catch_up(page, session, turn, before, message, deadline)
            if turn.shown is None:
                step["instead"] = session.extra["page"].get("last_shown", "")
        return turn

    async def _region_text(self, page: Any) -> str:
        """What the replies' region shows now; empty when it isn't there (yet)."""
        region = self._locator(page, self.page.replies.in_).first
        with contextlib.suppress(_api().Error):
            if await region.count():
                return str(await region.inner_text(timeout=1000))
        return ""

    def _carries(self, message: str) -> Callable[[Any], bool]:
        """Whether a response answers the page's request that sent *message*: a POST, PUT or PATCH
        to the copy's origin whose body holds it, or has no body to read (a stream)."""
        spelled = {
            message,
            json.dumps(message)[1:-1],
            json.dumps(message, ensure_ascii=False)[1:-1],
            quote_plus(message),
        }

        def carries(response: Any) -> bool:
            request = response.request
            if request.method not in _SENDS or not self._on_copy(request.url):
                return False
            try:
                body = request.post_data
            except (UnicodeDecodeError, ValueError):
                body = None
            return body is None or any(form in body for form in spelled)

        return carries

    async def _page_sends(
        self, page: Any, message: str, started: float, deadline: float
    ) -> float | Turn:
        """Type *message* and click Send, then wait for the copy to answer the request that
        carried it. When it was sent (just before the click), or the turn, when it went no
        further: a map that's off, an error status, or no answer by *deadline*."""
        api = _api()
        actions = self.page.actions
        try:
            box = await self._find(page, actions.type.fill, deadline)
            await self._act(box.fill(message, timeout=self._ms(deadline)), actions.type.fill)
            button = await self._find(page, actions.send.click, deadline)
            wait = max(deadline - time.time(), 0.1) * 1000
            since = time.time()
            async with page.expect_response(self._carries(message), timeout=wait) as answered:
                await self._act(button.click(timeout=self._ms(deadline)), actions.send.click)
            response = await answered.value
        except PageMapBroken as exc:
            return Turn("", started, time.time(), error=str(exc), failure=Failure.PAGE_MAP)
        except api.TimeoutError:
            said = "the app didn't answer the page's message in time"
            return Turn("", started, time.time(), error=said, failure=Failure.TIMED_OUT, heard=True)
        if response.status >= 400:
            return Turn(
                "",
                started,
                time.time(),
                error=f"the app answered the page's message with {response.status}",
                failure=failure_of_status(response.status),
                heard=status_may_have_passed_on(response.status),
            )
        return since

    async def _catch_up(
        self, page: Any, session: Session, turn: Turn, before: str, message: str, deadline: float
    ) -> float | None:
        """Wait for the page to stop being busy and show *turn*'s reply after the person's
        *message* and past what the region showed *before* the turn (:func:`shown_span`), and say
        on *turn* what it showed: the newest. When it showed; ``None`` when it didn't.
        ``Turn.reply`` is never changed; a region the page lacks is the map's error."""
        api = _api()
        earlier = page_words(before)
        region = self._locator(page, self.page.replies.in_).first
        busy = [self._locator(page, mark).first for mark in self.page.busy]
        left = min(self.page.catch_up_seconds, deadline - time.time())
        until = time.time() + max(left, 0.0)
        seen, text = False, ""
        try:
            while True:
                if await region.count():
                    seen = True
                    text = await region.inner_text(timeout=1000)
                    idle = not [b for b in busy if await b.is_visible()]
                    now = page_words(text)
                    kept = next(
                        (i for i, (a, b) in enumerate(zip(earlier, now, strict=False)) if a != b),
                        min(len(earlier), len(now)),
                    )
                    span = shown_span(turn.reply, text, message=message, after=kept)
                    if idle and span is not None:
                        turn.shown = text[span[0] : span[1]]
                        session.extra["page"].pop("last_shown", None)
                        return time.time()
                if time.time() >= until:
                    break
                await asyncio.sleep(0.1)
        except api.Error:
            _logger.warning("evals: the page of %s couldn't be read", session.id, exc_info=True)
        turn.shown = None
        if not seen:
            region_said = _said(self.page.replies.in_)
            turn.error, turn.failure = f"the page has no {region_said}", Failure.PAGE_MAP
            return None
        session.extra["page"]["last_shown"] = text[-SHOWN_KEPT:]
        return None

    async def reset(self, session: Session) -> None:
        """The inner adapter's reset, then a fresh context with the session's page open."""
        await self.inner.reset(session)
        page = await self._new_context(session)
        await self._show(page, session)

    async def break_conversation(self, session: Session) -> None:
        """The inner adapter's new conversation, then its page open."""
        if not self.capabilities.conversation_break:
            raise NotImplementedError(f"{self.name} can't start a new conversation on its page")
        await self.inner.break_conversation(session)
        await self._show(await self._live(session), session)

    async def cancel(self, session: Session) -> None:
        """The inner adapter's: the work in flight is the app's, not the page's."""
        await self.inner.cancel(session)

    async def close(self, session: Session) -> None:
        """Close the session's context when this process holds it, then the inner session. What
        goes wrong with the page is logged, never raised."""
        try:
            await self._drop(session)
        except Exception:
            _logger.warning("evals: the page of %s not closed", session.id, exc_info=True)
        await self.inner.close(session)

    async def dispose(self) -> None:
        await self.dispose_browser()
        await self.inner.dispose()

    async def dispose_browser(self) -> None:
        """Close the browser and every context in it; the inner adapter stays as it is."""
        for context, _ in list(self._open.values()):
            with contextlib.suppress(Exception):
                await context.close()
        self._open.clear()
        self._records.clear()
        for folder in list(self._videos.values()):
            await asyncio.to_thread(shutil.rmtree, folder, True)
        self._videos.clear()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    # The rest is the inner adapter's

    async def read_memory(self, session: Session) -> MemoryRead | None:
        return await self.inner.read_memory(session)

    async def fold_at(self, session: Session, tokens: int) -> None:
        await self.inner.fold_at(session, tokens)

    async def state(self, session: Session, check: dict[str, Any]) -> StateResult:
        return await self.inner.state(session, check)

    async def probe(self, session: Session, check: dict[str, Any]) -> StateResult:
        return await self.inner.probe(session, check)

    def sections(self, request: dict[str, Any]) -> list[Section]:
        return self.inner.sections(request)

    def answers(self) -> list[dict[str, Any]]:
        return self.inner.answers()


#: The entries :meth:`PageTarget.try_entry` tries.
TRIED = frozenset(
    {
        "url",
        "ready",
        "sign_in",
        "open_session",
        "actions.type",
        "actions.send",
        "actions.new_chat",
        "actions.stop",
        "actions.attach",
        "replies.in",
        "replies.item",
        "replies.mine",
        "busy",
    }
)


def _quoted(value: str) -> str:
    """*value* as a quoted attribute value."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
