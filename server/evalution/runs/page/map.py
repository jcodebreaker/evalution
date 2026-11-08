"""The page map (ADR 0010, phase 2): where a target's page keeps what a person uses, as data.

A target's ``page:`` block says which page to open, how a test person signs in, how the page
shows the conversation the inner adapter opened, where the message box, Send and New chat are,
where the replies are, and when the page is still busy. A fixed script follows it on every try;
no model picks a click while a test runs. Written by hand in v1.

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

A test person signs in one of four ways (``sign_in``): ``none``, a ``cookie``, a ``form`` with a
seeded account (``accounts: seed``), or ``headers`` sent with every request, for an app that
trusts a proxy in front of it to say who is signed in. A header's value is a template
(``{person.email}``, ``{account.email}``, ``{secret:ENV_NAME}``), never a literal secret. These
headers are only ever sent to the copy's origin, never to another host the page reaches; the page
target holds to that.
"""

from __future__ import annotations

import re
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

from evalution.config import ConfigError, TargetConfig

#: Where the copy's page address goes in a URL.
COPY_PAGE = "{copy_page}"
#: The longest the page may take, after the inner adapter's send returns, to show the reply.
CATCH_UP_MOST = 120.0
#: The entries a map's ``evidence`` may speak for.
ENTRIES = frozenset(
    {
        "url",
        "ready",
        "sign_in",
        "accounts",
        "open_session",
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
_SECRET_PLACEHOLDER = re.compile(r"^secret:[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_SECRET_NAME = re.compile(r"secret|token|key|password|authorization", re.IGNORECASE)


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


class Actions(_Strict):
    """What a person does. ``attach`` and ``stop`` aren't used in v1."""

    type: Fill
    send: Click
    new_chat: Click | None = None
    attach: Click | None = None
    stop: Click | None = None


class Replies(_Strict):
    """Where the replies are: the list (``in``), each one in it (``item``), and which are the
    assistant's (``mine``). A page whose messages have no roles leaves ``item`` and ``mine`` out."""

    in_: Element = Field(alias="in")
    item: Repeated | None = None
    mine: Mark | None = None


class PageMap(_Strict):
    """A target's page, as a fixed script follows it (see the module's docstring).

    :param accounts: ``seed``: one account per slot, seeded in the copy's setup, for a form or
        cookie to sign in with. Not needed with ``headers``.
    :param busy: The page is still working while any of these is there.
    :param catch_up_seconds: After the inner adapter's send returns, how long the page gets to
        stop being busy and show the reply.
    :param evidence: Per entry (one of :data:`ENTRIES`), why it is what it is.
    """

    url: PageUrl
    ready: Element
    sign_in: SignIn = Field(default_factory=SignIn)
    accounts: Literal["seed"] | None = None
    open_session: OpenSession
    actions: Actions
    replies: Replies
    busy: tuple[Mark, ...] = ()
    catch_up_seconds: float = Field(default=10.0, gt=0, le=CATCH_UP_MOST)
    status: Status = "draft"
    evidence: dict[str, Evidence] = Field(default_factory=dict)

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
    if opening.url is not None:
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


def page_map_of(target: TargetConfig) -> PageMap | None:
    """The target's page map, from its ``page:`` block; ``None`` when it has none.

    :raises ConfigError: The block isn't a page map; the message says where and why.
    """
    block = target.options.get("page")
    if block is None:
        return None
    where = f"targets.{target.name}.page"
    if not isinstance(block, dict):
        raise ConfigError(f"{where}: must be a mapping")
    try:
        return PageMap.model_validate(block)
    except ValidationError as e:
        reasons = "; ".join(_plain(err, where) for err in e.errors())
        raise ConfigError(reasons) from None
