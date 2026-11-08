"""The page target (ADR 0010, phase 3): a target played through its API adapter, with the app's own
page open on evalution's copy beside it, as its page map says.

The inner adapter still plays: its ``Turn.reply`` is what judging reads. The page is a second
look at the same turn. When the inner adapter can wait on a turn it didn't send (``watch``), the
page sends: it types the message and clicks Send, waits for the copy to answer the request that
carried it (an error status, or no answer by the deadline, is the turn's end), and the inner
adapter then waits for that turn. Else the inner adapter sends, and the page only watches. Either
way, once the turn is done the page gets ``catch_up_seconds`` for its busy signs to clear and the
reply to show in ``replies.in`` once more than before the turn, both normalised
(:func:`normalised`), so an earlier reply with the same words isn't taken for it. ``Turn.shown`` is
the newest; ``None`` when the reply never showed, with the last of what did in
``Session.extra["page"]["last_shown"]``.

One browser per page target, so one per copy, launched on first use: Playwright's own Chromium when
it is installed, else the system Chrome (``EVALUTION_CHROME``, or the usual place on a Mac).
Nothing is ever downloaded. A new browser context per session, and again on reset, so cookies,
local storage and IndexedDB never carry from one try to the next. Headers are added only to
requests for the copy's origin: the map's sign-in headers and the try's ``X-Evalution-Run`` and
``X-Evalution-Try``. Any other origin the page reaches gets nothing added.

``Session.extra["page"]`` holds JSON only: the key of the session's context. ``close`` and
``cancel`` work from it alone, after a restart too, when there is no browser: a key this process
doesn't hold is a no-op. Playwright is imported only here, and only when a browser is needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
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
from evalution.config import KeyRing, TargetConfig
from evalution.core.evidence import Failure
from evalution.core.trace import Section
from evalution.runs.page.map import Locator, PageMap, PageMapBroken, fill_urls, page_map_of

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
#: Methods a page sends a message with.
_SENDS = frozenset({"POST", "PUT", "PATCH"})
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_MARKS = re.compile(r"[*_`#>]")
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


def system_chrome() -> str | None:
    """The system Chrome to use when Playwright has no Chromium of its own, if it is there."""
    path = os.environ.get("EVALUTION_CHROME") or MAC_CHROME
    return path if Path(path).is_file() else None


def normalised(text: str) -> str:
    """*text* as a reply and a page are compared: Markdown's marks gone, whitespace collapsed,
    lowercase."""
    return " ".join(_MARKS.sub("", text).split()).lower()


def page_capabilities(inner: Capabilities, page: PageMap) -> Capabilities:
    """What a target played through *page* can do: what its adapter can, without files or Stop
    (v1), and a new conversation only when the page can follow it there. It can't be watched."""
    follows = not inner.watch or page.actions.new_chat is not None
    return replace(
        inner,
        files=False,
        stop=False,
        watch=False,
        conversation_break=inner.conversation_break and follows,
    )


def on_page(target: TargetConfig, adapter: Target, copy_url: str) -> Target:
    """*adapter*, played through the target's page on the copy at *copy_url* when it has a page
    map; else *adapter* as it is.

    :raises ConfigError: The target's page map doesn't read.
    """
    page = page_map_of(target)
    if page is None:
        return adapter
    return PageTarget(adapter, page, copy_url, keys=target.keys)


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
    :param element_seconds: How long an element the map names may take to show.
    """

    def __init__(
        self,
        inner: Target,
        page: PageMap,
        copy_page: str,
        *,
        keys: KeyRing | None = None,
        element_seconds: float = ELEMENT_SECONDS,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.person = inner.person
        self.page = fill_urls(page, copy_page)
        self.capabilities = page_capabilities(inner.capabilities, page)
        self.keys = keys or KeyRing()
        self.element_seconds = element_seconds
        self._copy = _origin(copy_page)
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        #: Each session's context and its page, by the key ``Session.extra["page"]`` holds.
        self._open: dict[str, tuple[Any, Any]] = {}

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
        account = session.extra.get("account") or {}

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
            return str(value)

        return _PLACEHOLDER.sub(one, text)

    def _headers(self, session: Session) -> dict[str, str]:
        """What every request to the copy's origin carries for *session*."""
        sign_in = self.page.sign_in.headers or {}
        added = {name: self._filled(value, session) for name, value in sign_in.items()}
        added[RUN_HEADER] = str(session.extra.get("run", ""))
        added[TRY_HEADER] = str(session.extra.get("try", ""))
        return added

    async def _new_context(self, session: Session) -> Any:
        """A fresh context for *session*, signed in, with a page open on nothing yet."""
        await self._drop(session)
        added = self._headers(session)
        browser = await self._launched()
        context = await browser.new_context(**self._context_options())
        key = f"{session.id}:{secrets.token_hex(4)}"
        page = await context.new_page()
        self._open[key] = (context, page)
        session.extra["page"] = {"context": key}

        async def add(route: Any) -> None:
            await route.continue_(headers={**route.request.headers, **added})

        await context.route(self._on_copy, add)
        await self._sign_in(context, page, session)
        return page

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

    def _page_of(self, session: Session) -> Any:
        """:raises TargetGone: This process holds no page for *session*."""
        key = (session.extra.get("page") or {}).get("context")
        found = self._open.get(key) if isinstance(key, str) else None
        if found is None:
            raise TargetGone("the session's page isn't open")
        return found[1]

    async def _drop(self, session: Session) -> None:
        """Close *session*'s context, if this process holds it."""
        key = (session.extra.get("page") or {}).get("context")
        found = self._open.pop(key, None) if isinstance(key, str) else None
        if found is not None:
            await found[0].close()

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
        if files:
            return Turn("", started, started, error=NO_FILES)
        if stop_after:
            return Turn("", started, started, error=NO_STOP)
        if session.extra.get("fresh"):
            return Turn("", started, started, error=NO_FRESH, failure=Failure.NOT_SUPPORTED)
        page = self._page_of(session)
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
            await self._catch_up(page, session, turn, normalised(before), deadline)
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
        self, page: Any, session: Session, turn: Turn, before: str, deadline: float
    ) -> None:
        """Wait for the page to stop being busy and show *turn*'s reply once more than it did
        *before* the turn (normalised), and say on *turn* what it showed: the newest. ``Turn.reply``
        is never changed; a region the page lacks is the map's error."""
        api = _api()
        want = normalised(turn.reply)
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
                    shown = normalised(text)
                    more = bool(want) and shown.count(want) > before.count(want)
                    at = shown.rfind(want)
                    if idle and more:
                        turn.shown = shown[at : at + len(want)]
                        session.extra["page"].pop("last_shown", None)
                        return
                if time.time() >= until:
                    break
                await asyncio.sleep(0.1)
        except api.Error:
            _logger.warning("evals: the page of %s couldn't be read", session.id, exc_info=True)
        turn.shown = None
        if not seen:
            region_said = _said(self.page.replies.in_)
            turn.error, turn.failure = f"the page has no {region_said}", Failure.PAGE_MAP
            return
        session.extra["page"]["last_shown"] = text[-SHOWN_KEPT:]

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
        await self._show(self._page_of(session), session)

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
        for context, _ in list(self._open.values()):
            with contextlib.suppress(Exception):
                await context.close()
        self._open.clear()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None
        await self.inner.dispose()

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


def _quoted(value: str) -> str:
    """*value* as a quoted attribute value."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
