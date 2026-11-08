"""The page map (ADR 0010, phase 2): where a target's page keeps what a person uses, as data.

A target's ``page:`` block says which page to open, how a test person signs in, how the page
shows the conversation the inner adapter opened, where the message box, Send and New chat are,
where the replies are, and when the page is still busy. A fixed script follows it on every try;
no model picks a click while a test runs. Written by hand, or learned by the test writer on
evalution's copy and approved once (:mod:`evalution.writer.paging`).

Elements are named the way a person finds them, so a change to the page's look doesn't move them:

* ``{role, name}``: a role with its accessible name, ``{role: button, name: "Send"}``.
* ``{test_id}``: the element's test id.
* ``{attr, equals}``: an attribute's value, only to say which replies are the assistant's
  (``mine``) or when the page is busy (``busy``).

A reply (``replies.item``, ``open_session.pick.item``) repeats, so it may be a role alone. CSS,
XPath and coordinates are refused: they break when the page's look changes.

Every URL starts with ``{copy_page}``, the copy's page address: a page run only ever opens
evalution's own copy (ADR 0009). :func:`fill_urls` fills it in where the copy's address is known,
and refuses a URL that would leave it. ``{session}`` (in ``open_session.url``) and
``{account.email}``, ``{account.password}`` (in sign-in values) are filled later, per try.

A target reached only through its page (``adapter: page``, no API adapter under it) opens its
chat at ``url``, or by ``actions.new_chat``, and needs no ``open_session``. It may say where the
page shows the conversation's id (``session_id: {from: url, pattern: "/chat/(?P<id>[^/?#]+)"}``),
so its spans are found by it; without one, they are found by the try's ``X-Evalution-Run`` and
``X-Evalution-Try`` headers alone. Its turn is done when every ``busy`` sign is gone and the
replies have stayed the same for ``quiet_ms`` (800 by default): busy first, quiet as the
tiebreaker.

A test person signs in one of four ways (``sign_in``): ``none``, a ``cookie``, a ``form`` with a
seeded account (``accounts: seed``), or ``headers`` sent with every request, for an app that
trusts a proxy in front of it to say who is signed in. A header's value is a template
(``{person.email}``, ``{account.email}``, ``{secret:ENV_NAME}``), never a literal secret. These
headers are only ever sent to the copy's origin, never to another host the page reaches; the page
target holds to that.

``proof`` says what a try keeps of what the page showed: its ``screenshots`` (one after each
message) and its ``video``, each ``always``, ``failed`` (kept, and deleted once the try is judged a
pass) or ``off`` (not taken at all). By default every screenshot is kept, and the video only when a
try fails. A test may say otherwise for itself (its ``page.proof``), part by part, and wins. The
older ``video`` (``failed``, ``always`` or ``never``) still reads, as ``proof.video`` (``never`` is
``off``); when both are given, ``proof`` wins.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from evalution.adapters.page import NAME as PAGE_ONLY
from evalution.config import ConfigError, TargetConfig
from evalution.core.case import PROOF_PARTS

#: Where the copy's page address goes in a URL.
COPY_PAGE = "{copy_page}"
#: The longest the page may take, after the inner adapter's send returns, to show the reply.
CATCH_UP_MOST = 120.0
#: The longest ``send_seconds`` a map may give the page's message request.
SEND_MOST = 600.0
#: The longest the replies must stay the same before a page-only turn is taken as done.
QUIET_MOST_MS = 60_000
#: The entries a map's ``evidence`` may speak for.
ENTRIES = frozenset(
    {
        "url",
        "ready",
        "sign_in",
        "accounts",
        "open_session",
        "session_id",
        "actions.new_chat",
        "actions.type",
        "actions.send",
        "actions.attach",
        "actions.stop",
        "replies.in",
        "replies.item",
        "replies.mine",
        "busy",
    }
)

Status = Literal["draft", "tried", "approved", "stale", "broken"]
#: When a try's screenshots or video are kept: always, only when it doesn't pass, or never (not
#: taken). The words of :data:`evalution.core.case.PROOF_KEEPS`.
Keep = Literal["always", "failed", "off"]
#: The older ``video`` setting's words, as :data:`Keep` says them.
_OLD_VIDEO = {"failed": "failed", "always": "always", "never": "off"}

_HOW = "name the element by its role and name ({role, name}) or its test id ({test_id})"
_CSS = {"css", "selector", "class"}
_XPATH = {"xpath"}
_COORDINATES = {"x", "y", "coordinates", "point", "position", "box"}
_ROLE = re.compile(r"^[a-z]+$")
_ATTR = re.compile(r"^[A-Za-z_:][-A-Za-z0-9_:.]*$")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_URL_PLACEHOLDERS = {"copy_page", "session"}
_VALUE_PLACEHOLDERS = {"account.email", "account.password"}
_HEADER_PLACEHOLDERS = {"account.email", "person.email"}
#: A stored secret is named like ``targets/<target>/<key>``; an env var like ``MY_KEY``.
_SECRET_PLACEHOLDER = re.compile(r"^secret:[A-Za-z_][A-Za-z0-9_./-]*$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_SECRET_NAME = re.compile(r"secret|token|key|password|authorization", re.IGNORECASE)


class PageMapBroken(RuntimeError):
    """The page didn't have what its page map names: every try would meet the same (ADR 0010)."""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class Locator(_Strict):
    """One element, named the way a person finds it: ``{role, name}``, ``{test_id}`` or, where
    the map allows it, ``{attr, equals}``."""

    role: str | None = None
    name: str | None = None
    test_id: str | None = None
    attr: str | None = None
    equals: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_selectors(cls, data: Any) -> Any:
        if isinstance(data, str):
            raise ValueError(f"{data!r} is a selector, which isn't allowed: {_HOW}")
        if isinstance(data, list | tuple):
            raise ValueError(f"coordinates aren't allowed: {_HOW}")
        if isinstance(data, dict):
            keys = set(data)
            if keys & _CSS:
                raise ValueError(f"CSS selectors aren't allowed: {_HOW}")
            if keys & _XPATH:
                raise ValueError(f"XPath isn't allowed: {_HOW}")
            if keys & _COORDINATES:
                raise ValueError(f"coordinates aren't allowed: {_HOW}")
        return data

    @model_validator(mode="after")
    def _one_shape(self) -> Locator:
        by_role = self.role is not None or self.name is not None
        by_attr = self.attr is not None or self.equals is not None
        if by_role + (self.test_id is not None) + by_attr != 1:
            raise ValueError("a locator is one of {role, name}, {test_id} or {attr, equals}")
        if by_role:
            if not self.role:
                raise ValueError(
                    f'a name needs its role, like {{role: button, name: "{self.name}"}}'
                )
            if not _ROLE.match(self.role):
                raise ValueError(f"{self.role!r} isn't a role: a role is one word, like button")
            if self.name is not None and not self.name.strip():
                raise ValueError("a name can't be empty")
        if self.test_id is not None and not self.test_id.strip():
            raise ValueError("a test id can't be empty")
        if by_attr:
            if self.attr is None or self.equals is None:
                raise ValueError("an attribute match needs both attr and equals")
            if not _ATTR.match(self.attr):
                raise ValueError(f"{self.attr!r} isn't an attribute name")
        return self


def _named(found: Locator) -> Locator:
    """One element: a role with its name, or a test id."""
    if found.attr is not None:
        raise ValueError(
            f"an attribute match only says which replies are mine or when busy: {_HOW}"
        )
    if found.role is not None and found.name is None:
        raise ValueError(
            f'a {found.role} needs its name too, like {{role: {found.role}, name: "..."}}'
        )
    return found


def _repeated(found: Locator) -> Locator:
    """An element that repeats: a role alone will do."""
    if found.attr is not None:
        raise ValueError(
            f"an attribute match only says which replies are mine or when busy: {_HOW}"
        )
    return found


def _marked(found: Locator) -> Locator:
    """A sign on the page: a role with its name, a test id or an attribute's value."""
    if found.role is not None and found.name is None:
        raise ValueError(
            f'a {found.role} needs its name too, like {{role: {found.role}, name: "..."}}'
        )
    return found


Element = Annotated[Locator, AfterValidator(_named)]
Repeated = Annotated[Locator, AfterValidator(_repeated)]
Mark = Annotated[Locator, AfterValidator(_marked)]


def _climbs(rest: str) -> bool:
    """Whether the path after ``{copy_page}`` has a ``..`` (spelled out or %-encoded) in it."""
    path = re.split(r"[?#]", rest, maxsplit=1)[0]
    return any(unquote(part) == ".." for part in re.split(r"[/\\]", path))


def _placeholders(text: str, allowed: set[str], where: str) -> None:
    for name in _PLACEHOLDER.findall(text):
        if name in allowed:
            continue
        if name.startswith("account."):
            raise ValueError(f"{text!r}: {where} can't carry the account's {name[8:]}")
        raise ValueError(f"{text!r}: {{{name}}} isn't something evalution fills in {where}")


def _page_url(template: str) -> str:
    """A URL on the copy's page: ``{copy_page}``, then a path, a query or nothing."""
    if not template.startswith(COPY_PAGE):
        raise ValueError(
            f"{template!r} must start with {COPY_PAGE}: a page run only opens evalution's own copy"
        )
    rest = template[len(COPY_PAGE) :]
    if rest and rest[0] not in "/?#":
        raise ValueError(f"{template!r}: after {COPY_PAGE} comes a path, like {COPY_PAGE}/chat")
    if "\\" in rest:
        raise ValueError(f"{template!r}: a URL's path is split by /, not \\")
    if _climbs(rest):
        raise ValueError(f"{template!r} climbs out of the copy's page with ..")
    _placeholders(template, _URL_PLACEHOLDERS, "a URL")
    return template


def _value(text: str) -> str:
    _placeholders(text, _VALUE_PLACEHOLDERS, "a value")
    return text


PageUrl = Annotated[str, AfterValidator(_page_url)]
Value = Annotated[str, AfterValidator(_value)]


class Evidence(_Strict):
    """Why an entry is what it is: where in the code it comes from, and when it was last found
    on the copy's page (seconds since the epoch)."""

    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    tried: float | None = None


class Click(_Strict):
    click: Element


class Fill(_Strict):
    """Types the turn's message into this box."""

    fill: Element


class FormField(Locator):
    """A box on the sign-in form and what goes in it."""

    value: Value


class Done(_Strict):
    """Signed in once the page's address matches this (a path, like ``/chat``)."""

    url_matches: str = Field(min_length=1)


class Form(_Strict):
    url: PageUrl
    fill: tuple[Annotated[FormField, AfterValidator(_named)], ...] = ()
    submit: Element
    done: Done


class Cookie(_Strict):
    """A cookie set on the copy's page before it opens."""

    name: str = Field(min_length=1)
    value: Value


def _headers(headers: dict[str, str]) -> dict[str, str]:
    """Header names as HTTP has them, and values that are templates, never a literal secret."""
    if not headers:
        raise ValueError("headers needs at least one header")
    for name, value in headers.items():
        if not _HEADER_NAME.match(name):
            raise ValueError(f"{name!r} isn't a header name")
        found = _PLACEHOLDER.findall(value)
        for placeholder in found:
            if placeholder not in _HEADER_PLACEHOLDERS and not _SECRET_PLACEHOLDER.match(
                placeholder
            ):
                raise ValueError(
                    f"{name}: {{{placeholder}}} isn't something evalution fills in a header"
                )
        if not found and _SECRET_NAME.search(name):
            raise ValueError(
                f"{name}: a secret is never written in the map; name it, like {{secret:ENV_NAME}}"
            )
    return headers


class SignIn(_Strict):
    """How the test person signs in: ``none``, a ``cookie``, a ``form`` or ``headers`` (see the
    module's docstring)."""

    form: Form | None = None
    cookie: Cookie | None = None
    headers: Annotated[dict[str, str], AfterValidator(_headers)] | None = None

    @model_validator(mode="before")
    @classmethod
    def _none(cls, data: Any) -> Any:
        return {} if data is None or data == "none" else data

    @model_validator(mode="after")
    def _one_way(self) -> SignIn:
        ways = [self.form, self.cookie, self.headers]
        if sum(way is not None for way in ways) > 1:
            raise ValueError("sign in one way: a form, a cookie or headers")
        return self

    @property
    def kind(self) -> Literal["none", "cookie", "form", "headers"]:
        if self.form is not None:
            return "form"
        if self.cookie is not None:
            return "cookie"
        return "headers" if self.headers is not None else "none"


class Pick(_Strict):
    """The conversation, picked from a list by its title."""

    in_: Element = Field(alias="in")
    item: Repeated


class OpenSession(_Strict):
    """How the page shows the conversation the inner adapter opened: its own ``url``
    (with ``{session}``), or ``pick`` it from a list."""

    url: PageUrl | None = None
    pick: Pick | None = None

    @model_validator(mode="after")
    def _one_way(self) -> OpenSession:
        if (self.url is None) == (self.pick is None):
            raise ValueError("open a conversation by its url or by pick, one of the two")
        return self


def _id_pattern(pattern: str) -> str:
    """A regular expression with an ``id`` group, read against the page's address."""
    try:
        found = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{pattern!r} isn't a regular expression: {exc}") from None
    if "id" not in found.groupindex:
        raise ValueError(f"{pattern!r} needs a group named id, like /chat/(?P<id>[^/?#]+)")
    return pattern


class SessionId(_Strict):
    """Where a page-only target's page shows its conversation's id: in its address, as the
    ``id`` group of ``pattern`` finds it there (its path, query and fragment, not its host)."""

    from_: Literal["url"] = Field(alias="from")
    pattern: Annotated[str, AfterValidator(_id_pattern)] = Field(min_length=1)


class Actions(_Strict):
    """What a person does. ``attach`` and ``stop`` aren't used in v1."""

    type: Fill
    send: Click
    new_chat: Click | None = None
    attach: Click | None = None
    stop: Click | None = None


class Replies(_Strict):
    """Where the replies are: the list (``in``), each one in it (``item``), and which are the
    assistant's (``mine``). A page whose messages have no roles leaves ``item`` and ``mine`` out,
    and one whose conversation is an unnamed landmark (``{role: main}``) may name the role alone:
    the first such element is the one."""

    in_: Repeated = Field(alias="in")
    item: Repeated | None = None
    mine: Mark | None = None


class Proof(_Strict):
    """What a try played through the page keeps of it: its screenshots, one after each message,
    and its video. Each is ``always``, ``failed`` (deleted once the try passes) or ``off``."""

    screenshots: Keep = "always"
    video: Keep = "failed"

    def over(self, given: Mapping[str, str]) -> Proof:
        """This proof with *given*'s parts (a test's own ``page.proof``) in place of its own."""
        found = {part: given[part] for part in PROOF_PARTS if part in given}
        return Proof.model_validate({**self.model_dump(), **found}) if found else self


class PageMap(_Strict):
    """A target's page, as a fixed script follows it (see the module's docstring).

    :param accounts: ``seed``: one account per slot, seeded in the copy's setup, for a form or
        cookie to sign in with. Not needed with ``headers``.
    :param busy: The page is still working while any of these is there.
    :param open_session: How the page shows the conversation the inner adapter opened. A target
        reached through its page alone has no inner adapter, and leaves it out.
    :param session_id: Where a page-only target's page shows its conversation's id.
    :param catch_up_seconds: After the inner adapter's send returns, how long the page gets to
        stop being busy and show the reply. On a page-only target, how long after Send the page
        gets to show any sign of answering (busy, or something new in the replies) before the
        turn is taken as one it showed nothing for.
    :param quiet_ms: On a page-only target, how long the replies must stay the same, nothing
        busy, before the turn is done.
    :param send_seconds: When the page sends, how long the app gets to answer the request that
        carried the message (its status; a stream's first bytes) before the message counts as
        not taken. An app whose message request returns only with the whole reply needs it
        longer than its slowest reply.
    :param evidence: Per entry (one of :data:`ENTRIES`), why it is what it is.
    :param proof: What a try keeps: its screenshots and its video (:class:`Proof`). The older
        ``video`` reads as ``proof.video``; when both are given, ``proof`` wins.
    """

    url: PageUrl
    ready: Element
    sign_in: SignIn = Field(default_factory=SignIn)
    accounts: Literal["seed"] | None = None
    open_session: OpenSession | None = None
    session_id: SessionId | None = None
    actions: Actions
    replies: Replies
    busy: tuple[Mark, ...] = ()
    catch_up_seconds: float = Field(default=10.0, gt=0, le=CATCH_UP_MOST)
    quiet_ms: int = Field(default=800, ge=0, le=QUIET_MOST_MS)
    send_seconds: float = Field(default=30.0, gt=0, le=SEND_MOST)
    status: Status = "draft"
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    proof: Proof = Field(default_factory=Proof)

    @model_validator(mode="before")
    @classmethod
    def _old_video(cls, data: Any) -> Any:
        """The older ``video`` as ``proof.video``; a ``proof`` that says its video wins."""
        if not isinstance(data, dict) or "video" not in data:
            return data
        data = dict(data)
        video = data.pop("video")
        if not isinstance(video, str) or video not in _OLD_VIDEO:
            raise ValueError(f"video: {video!r} isn't one of failed, always or never")
        proof = data.get("proof")
        if proof is None:
            data["proof"] = {"video": _OLD_VIDEO[video]}
        elif isinstance(proof, dict) and "video" not in proof:
            data["proof"] = {**proof, "video": _OLD_VIDEO[video]}
        return data

    @model_validator(mode="after")
    def _known_entries(self) -> PageMap:
        unknown = sorted(set(self.evidence) - ENTRIES)
        if unknown:
            raise ValueError(f"evidence for {', '.join(unknown)}, which the map has no entry for")
        return self


def _origin(url: str) -> tuple[str, str, str]:
    """``(scheme, host and port, path)`` of the copy's page address.

    :raises ValueError: Not an http(s) address of its own.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{url!r} isn't the copy's page address: it needs http(s) and a host")
    if "@" in parts.netloc or parts.query or parts.fragment:
        raise ValueError(f"{url!r} isn't the copy's page address: no user, query or fragment")
    return parts.scheme, parts.netloc.lower(), parts.path.rstrip("/")


def _filled(template: str, copy_page: str) -> str:
    """*template* with the copy's page address in it, refused if it would leave the copy."""
    scheme, host, path = _origin(copy_page)
    base = copy_page.rstrip("/")
    if not template.startswith(COPY_PAGE):
        raise ValueError(f"{template!r} must start with {COPY_PAGE}")
    rest = template[len(COPY_PAGE) :]
    url = base + rest.replace(COPY_PAGE, base)
    parts = urlsplit(url)
    left = (
        parts.scheme != scheme
        or parts.netloc.lower() != host
        or not (parts.path == path or parts.path.startswith(f"{path}/"))
        or "\\" in rest
        or _climbs(rest)
    )
    if left:
        raise ValueError(f"{template!r} filled is {url!r}, which leaves the copy at {copy_page!r}")
    return url


def fill_urls(page: PageMap, copy_page: str) -> PageMap:
    """*page* with ``{copy_page}`` filled in every URL; other placeholders are left as they are.
    The filled map is for this run only: it no longer reads as a map.

    :raises ValueError: *copy_page* isn't a page address, or a filled URL would leave it.
    """
    sign_in = page.sign_in
    if sign_in.form is not None:
        form = sign_in.form.model_copy(update={"url": _filled(sign_in.form.url, copy_page)})
        sign_in = sign_in.model_copy(update={"form": form})
    opening = page.open_session
    if opening is not None and opening.url is not None:
        opening = opening.model_copy(update={"url": _filled(opening.url, copy_page)})
    return page.model_copy(
        update={
            "url": _filled(page.url, copy_page),
            "sign_in": sign_in,
            "open_session": opening,
        }
    )


def _plain(error: Any, where: str) -> str:
    """One validation error, in the config's words: where, then what's wrong."""
    where = ".".join([where, *(str(part) for part in error["loc"])])
    kind, message = error["type"], error["msg"]
    if kind == "extra_forbidden":
        message = "not a key the page map has"
    elif kind == "missing":
        message = "missing"
    elif kind == "value_error":
        message = message.removeprefix("Value error, ")
    else:
        message = message[:1].lower() + message[1:]
    return f"{where}: {message}"


def read_map(block: Any, where: str = "page") -> PageMap:
    """*block* as a page map.

    :raises ConfigError: It isn't one; the message says where and why, in the config's words.
    """
    if not isinstance(block, dict):
        raise ConfigError(f"{where}: must be a mapping")
    try:
        return PageMap.model_validate(block)
    except ValidationError as e:
        reasons = "; ".join(_plain(err, where) for err in e.errors())
        raise ConfigError(reasons) from None


def page_only(target: TargetConfig) -> bool:
    """Whether *target* is reached through its page alone (``adapter: page``), with no API
    adapter under it."""
    return target.adapter == PAGE_ONLY


def page_map_of(target: TargetConfig) -> PageMap | None:
    """The target's page map, from its ``page:`` block; ``None`` when it has none.

    :raises ConfigError: The block isn't a page map; the message says where and why.
    """
    block = target.options.get("page")
    where = f"targets.{target.name}.page"
    if block is None:
        if target.adapter == PAGE_ONLY:
            raise ConfigError(f"{where}: missing: adapter: page plays through the page map alone")
        return None
    found = read_map(block, where)
    if target.adapter != PAGE_ONLY and found.open_session is None:
        raise ConfigError(
            f"{where}.open_session: missing: how the page shows the conversation its adapter "
            "opened (adapter: page plays through the page alone, and needs none)"
        )
    return found


def as_block(page: PageMap) -> dict[str, Any]:
    """*page* as a ``page:`` block reads it back: the config's names, nothing left unset."""
    return page.model_dump(mode="json", by_alias=True, exclude_none=True)


def entry_of(page: PageMap, entry: str) -> Any:
    """What the map says for *entry* (one of :data:`ENTRIES`), as data; ``None`` when it says
    nothing."""
    found: Any = as_block(page)
    for part in entry.split("."):
        found = found.get(part) if isinstance(found, dict) else None
    return found


def _where(found: Locator) -> str:
    """One element of the map in plain words."""
    if found.test_id is not None:
        return f"test id “{found.test_id}”"
    if found.attr is not None:
        return f"{found.attr} is “{found.equals}”"
    return f"{found.role} named “{found.name}”" if found.name is not None else f"any {found.role}"


_SIGN_IN = {
    "none": "nothing",
    "cookie": "a cookie",
    "form": "the sign-in form, as a seeded account",
    "headers": "headers sent only to the copy",
}


def _id_where(page: PageMap) -> str:
    if page.session_id is None:
        return "not on the page: spans are found by the try's own headers"
    return f"the page's address, by {page.session_id.pattern}"


_SHOTS = {
    "always": "one after each message, kept for every try",
    "failed": "one after each message, kept when a try doesn't pass",
    "off": "not taken",
}
_VIDEO = {
    "failed": "kept when a try doesn't pass",
    "always": "kept for every try",
    "off": "not recorded",
}
#: What a run keeps, as the Run dialog says it, part by part.
_KEPT_SHOTS = {
    "always": "screenshots of every message",
    "failed": "screenshots when a try fails",
    "off": "no screenshots",
}
_KEPT_VIDEO = {
    "always": "video of every try",
    "failed": "video when a try fails",
    "off": "no video",
}


def proof_words(proof: Proof) -> str:
    """What a try keeps, in a few words: "screenshots of every message, video when a try
    fails"."""
    if proof.screenshots == "off" and proof.video == "off":
        return "no screenshots or video"
    return f"{_KEPT_SHOTS[proof.screenshots]}, {_KEPT_VIDEO[proof.video]}"


def described(page: PageMap, *, only: bool = False) -> list[dict[str, str]]:
    """The map in plain words, entry by entry, as ``{what, where}``: how the page is played, as a
    person reads it. *only*: the target is reached through its page alone."""
    rows = [
        ("Opens", page.url.replace(COPY_PAGE, "", 1) or "/"),
        ("Signs in with", _SIGN_IN[page.sign_in.kind]),
        ("Ready when it shows", _where(page.ready)),
        ("Message box", _where(page.actions.type.fill)),
        ("Send", _where(page.actions.send.click)),
    ]
    if page.actions.new_chat is not None:
        rows.append(("New chat", _where(page.actions.new_chat.click)))
    rows.append(("Replies", _where(page.replies.in_)))
    if page.busy:
        rows.append(("Busy while", " or ".join(_where(found) for found in page.busy)))
    if only:
        rows.append(("Session id", _id_where(page)))
        quiet = f"the replies stay the same for {page.quiet_ms / 1000:g} s"
        rows.append(("Done when", f"nothing is busy and {quiet}" if page.busy else quiet))
    rows.append(("Waits for the app to take a message", f"up to {page.send_seconds:g} s"))
    rows.append(("Waits for the reply", f"up to {page.catch_up_seconds:g} s"))
    rows.append(("Screenshots", _SHOTS[page.proof.screenshots]))
    rows.append(("Video", _VIDEO[page.proof.video]))
    return [{"what": what, "where": where} for what, where in rows]
