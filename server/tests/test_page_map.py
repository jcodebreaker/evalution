"""The page map as data (ADR 0010, phase 2): the plan's example reads and round-trips, elements
are named by role and name or test id and nothing else, every URL stays on evalution's copy,
and a target's ``page:`` block is read or refused in plain words."""

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from evalution.config import ConfigError, TargetConfig
from evalution.runs.page.map import PageMap, fill_urls, page_map_of

#: The plan's example, as written in docs/plans/page-testing.md, "The page map (v1)".
EXAMPLE = """
page:
  url: "{copy_page}/chat"
  ready: {role: textbox, name: "Message"}
  sign_in:
    form:
      url: "{copy_page}/login"
      fill:
        - {role: textbox, name: "Email", value: "{account.email}"}
        - {role: textbox, name: "Password", value: "{account.password}"}
      submit: {role: button, name: "Sign in"}
      done: {url_matches: "/chat"}
  accounts: seed
  open_session:
    url: "{copy_page}/chat/{session}"
  actions:
    new_chat: {click: {role: button, name: "New chat"}}
    type:     {fill: {role: textbox, name: "Message"}}
    send:     {click: {role: button, name: "Send"}}
  replies:
    in: {role: log, name: "Messages"}
    item: {role: article}
    mine: {attr: data-role, equals: assistant}
  busy:
    - {role: button, name: "Stop"}
    - {attr: aria-busy, equals: "true"}
  catch_up_seconds: 10
"""

COPY = "http://127.0.0.1:41234"


def _block() -> dict[str, Any]:
    return yaml.safe_load(EXAMPLE)["page"]


def _with(**changes: Any) -> dict[str, Any]:
    return {**_block(), **changes}


def _refused(block: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as e:
        PageMap.model_validate(block)
    return str(e.value)


def test_the_plans_example_reads() -> None:
    page = PageMap.model_validate(_block())
    assert page.url == "{copy_page}/chat"
    assert page.ready.role == "textbox" and page.ready.name == "Message"
    assert page.sign_in.kind == "form"
    assert page.sign_in.form is not None
    assert [f.value for f in page.sign_in.form.fill] == ["{account.email}", "{account.password}"]
    assert page.open_session.url == "{copy_page}/chat/{session}"
    assert page.actions.send.click.name == "Send"
    assert page.actions.new_chat is not None and page.actions.new_chat.click.name == "New chat"
    assert page.actions.attach is None and page.actions.stop is None
    assert page.replies.in_.role == "log" and page.replies.item.name is None
    assert page.replies.mine.attr == "data-role"
    assert len(page.busy) == 2
    assert page.catch_up_seconds == 10
    assert page.status == "draft"
    assert page.evidence == {}


def test_a_map_round_trips() -> None:
    page = PageMap.model_validate(
        _with(status="tried", evidence={"actions.send": {"file": "web/Chat.tsx", "line": 42}})
    )
    again = PageMap.model_validate(page.model_dump(by_alias=True))
    assert again == page
    assert page.model_dump(by_alias=True)["replies"]["in"] == {
        "role": "log",
        "name": "Messages",
        "test_id": None,
        "attr": None,
        "equals": None,
    }


def test_sign_in_none_and_by_cookie_read() -> None:
    assert PageMap.model_validate(_with(sign_in="none")).sign_in.kind == "none"
    blockless = {k: v for k, v in _block().items() if k != "sign_in"}
    assert PageMap.model_validate(blockless).sign_in.kind == "none"
    cookie = PageMap.model_validate(_with(sign_in={"cookie": {"name": "sid", "value": "test"}}))
    assert cookie.sign_in.kind == "cookie"


#: A page like relay-lite's, as phase 0 found it: a trusted proxy's headers say who is signed
#: in, and its messages have no roles.
HEADERS = """
url: "{copy_page}/"
ready: {role: textbox, name: "Message"}
sign_in:
  headers:
    X-Forwarded-Email: "{person.email}"
    X-Atrium-Proxy-Secret: "{secret:proxy_secret_env}"
open_session:
  url: "{copy_page}/c/{session}"
actions:
  type: {fill: {role: textbox, name: "Message"}}
  send: {click: {role: button, name: "Send"}}
replies:
  in: {test_id: messages}
busy:
  - {role: button, name: "Stop"}
"""


def test_headers_with_placeholders_sign_in_and_need_no_accounts() -> None:
    page = PageMap.model_validate(yaml.safe_load(HEADERS))
    assert page.sign_in.kind == "headers"
    assert page.sign_in.headers == {
        "X-Forwarded-Email": "{person.email}",
        "X-Atrium-Proxy-Secret": "{secret:proxy_secret_env}",
    }
    assert page.accounts is None
    assert PageMap.model_validate(page.model_dump(by_alias=True)) == page
    assert fill_urls(page, COPY).sign_in.headers == page.sign_in.headers


def test_relay_lite_as_it_really_is_reads() -> None:
    """Its stored secret is named by path, and its conversation is an unnamed ``main``."""
    raw = yaml.safe_load(HEADERS)
    raw["sign_in"]["headers"]["X-Atrium-Proxy-Secret"] = (
        "{secret:targets/relay-lite/proxy_secret}"
    )
    raw["replies"] = {"in": {"role": "main"}}
    page = PageMap.model_validate(raw)
    assert page.replies.in_.role == "main" and page.replies.in_.name is None


@pytest.mark.parametrize(
    ("headers", "said"),
    [
        ({"X-Atrium-Proxy-Secret": "hunter2"}, "a secret is never written in the map"),
        ({"Authorization": "Bearer abc123"}, "a secret is never written in the map"),
        ({"X-Api-Key": "abc123"}, "a secret is never written in the map"),
        ({"X-Forwarded-Email": "{person.password}"}, "isn't something evalution fills in"),
        ({"X-Token": "{secret:not an env}"}, "isn't something evalution fills in"),
        ({"Bad Header": "{person.email}"}, "isn't a header name"),
        ({}, "at least one header"),
    ],
)
def test_headers_with_a_literal_secret_or_unknown_placeholder_are_refused(
    headers: dict[str, str], said: str
) -> None:
    assert said in _refused(_with(sign_in={"headers": headers}))


def test_a_plain_header_that_isnt_secret_may_be_literal() -> None:
    page = PageMap.model_validate(_with(sign_in={"headers": {"X-Forwarded-Groups": "testers"}}))
    assert page.sign_in.headers == {"X-Forwarded-Groups": "testers"}


def test_sign_in_is_one_way() -> None:
    form = _block()["sign_in"]["form"]
    both = {"form": form, "headers": {"X-Forwarded-Email": "{person.email}"}}
    assert "sign in one way" in _refused(_with(sign_in=both))


def test_replies_without_item_or_mine_read() -> None:
    page = PageMap.model_validate(_with(replies={"in": {"role": "log", "name": "Messages"}}))
    assert page.replies.item is None and page.replies.mine is None
    assert "replies.in" in _refused(_with(replies={"item": {"role": "article"}}))


def test_a_test_id_is_a_locator() -> None:
    page = PageMap.model_validate(_with(ready={"test_id": "composer"}))
    assert page.ready.test_id == "composer"


@pytest.mark.parametrize(
    ("ready", "said"),
    [
        ("button.send", "is a selector, which isn't allowed"),
        ({"css": "#composer textarea"}, "CSS selectors aren't allowed"),
        ({"selector": "div > textarea"}, "CSS selectors aren't allowed"),
        ({"xpath": "//textarea[@name='m']"}, "XPath isn't allowed"),
        ("//textarea", "is a selector, which isn't allowed"),
        ({"x": 120, "y": 640}, "coordinates aren't allowed"),
        ([120, 640], "coordinates aren't allowed"),
    ],
)
def test_css_xpath_and_coordinates_are_refused(ready: Any, said: str) -> None:
    message = _refused(_with(ready=ready))
    assert said in message
    assert "role and name" in message


def test_a_locator_with_an_unknown_key_is_refused() -> None:
    assert "Extra inputs" in _refused(_with(ready={"role": "textbox", "label": "Message"}))


def test_a_locator_is_one_shape() -> None:
    assert "one of" in _refused(_with(ready={"role": "textbox", "name": "M", "test_id": "m"}))
    assert "one of" in _refused(_with(ready={}))
    assert "isn't a role" in _refused(_with(ready={"role": "button.primary", "name": "Send"}))


def test_one_element_needs_its_name_but_a_reply_may_be_a_role_alone() -> None:
    assert "needs its name too" in _refused(_with(ready={"role": "textbox"}))
    assert "needs its name too" in _refused(_with(busy=[{"role": "button"}]))
    replies = {**_block()["replies"], "item": {"role": "listitem"}}
    assert PageMap.model_validate(_with(replies=replies)).replies.item.role == "listitem"


def test_an_attribute_match_only_says_mine_or_busy() -> None:
    assert "only says which replies" in _refused(_with(ready={"attr": "data-role", "equals": "x"}))


def test_an_unknown_key_in_the_map_is_refused() -> None:
    assert "Extra inputs" in _refused(_with(click_send={"role": "button", "name": "Send"}))
    actions = {**_block()["actions"], "submit": {"click": {"role": "button", "name": "Go"}}}
    assert "Extra inputs" in _refused(_with(actions=actions))


def test_a_bad_status_is_refused() -> None:
    assert "status" in _refused(_with(status="done"))


def test_catch_up_is_bounded() -> None:
    assert "catch_up_seconds" in _refused(_with(catch_up_seconds=0))
    assert "catch_up_seconds" in _refused(_with(catch_up_seconds=3600))


def test_evidence_is_for_an_entry_the_map_has() -> None:
    assert "no entry for" in _refused(_with(evidence={"actions.click": {"file": "a.tsx"}}))
    assert "line" in _refused(_with(evidence={"ready": {"file": "a.tsx", "line": 0}}))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:5173/chat",
        "https://example.com/{copy_page}/chat",
        "/chat",
    ],
)
def test_a_url_not_starting_with_the_copys_page_is_refused(url: str) -> None:
    assert "must start with {copy_page}" in _refused(_with(url=url))
    form = {**_block()["sign_in"]["form"], "url": url}
    assert "must start with {copy_page}" in _refused(_with(sign_in={"form": form}))
    assert "must start with {copy_page}" in _refused(_with(open_session={"url": url}))


@pytest.mark.parametrize(
    ("url", "said"),
    [
        ("{copy_page}@evil.com/chat", "comes a path"),
        ("{copy_page}.evil.com/chat", "comes a path"),
        ("{copy_page}:9999/chat", "comes a path"),
        ("{copy_page}\\@evil.com", "comes a path"),
        ("{copy_page}/a\\..\\admin", "not \\"),
        ("{copy_page}/../admin", "climbs out"),
        ("{copy_page}/%2e%2e/admin", "climbs out"),
        ("{copy_page}/chat?as={account.email}", "can't carry the account's email"),
        ("{copy_page}/chat/{conversation}", "isn't something evalution fills in"),
    ],
)
def test_a_url_that_could_leave_the_copy_is_refused(url: str, said: str) -> None:
    assert said in _refused(_with(url=url))


def test_an_unknown_placeholder_in_a_sign_in_value_is_refused() -> None:
    form = _block()["sign_in"]["form"]
    form["fill"][0]["value"] = "{person.email}"
    assert "isn't something evalution fills in" in _refused(_with(sign_in={"form": form}))


def test_open_session_is_one_way() -> None:
    pick = {"in": {"role": "list", "name": "Chats"}, "item": {"role": "listitem"}}
    page = PageMap.model_validate(_with(open_session={"pick": pick}))
    assert page.open_session.pick is not None and page.open_session.url is None
    both = {"url": "{copy_page}/c/{session}", "pick": pick}
    assert "one of the two" in _refused(_with(open_session=both))


def test_fill_urls_puts_the_copy_in_every_url() -> None:
    page = fill_urls(PageMap.model_validate(_block()), COPY + "/")
    assert page.url == f"{COPY}/chat"
    assert page.sign_in.form is not None and page.sign_in.form.url == f"{COPY}/login"
    assert page.open_session.url == f"{COPY}/chat/{{session}}"
    assert page.sign_in.form.fill[0].value == "{account.email}"


def test_fill_urls_keeps_a_copy_under_a_path() -> None:
    page = fill_urls(PageMap.model_validate(_block()), "http://127.0.0.1:41234/app")
    assert page.url == "http://127.0.0.1:41234/app/chat"


@pytest.mark.parametrize(
    "template",
    [
        "{copy_page}@evil.com/chat",
        "{copy_page}.evil.com/chat",
        "{copy_page}:9999/chat",
        "{copy_page}/../admin",
        "{copy_page}/%2E%2E/admin",
        "{copy_page}\\@evil.com",
        "https://evil.com/{copy_page}",
    ],
)
def test_fill_urls_refuses_a_url_whose_origin_or_path_would_leave_the_copy(template: str) -> None:
    """A map read from the config never gets here; one made in code must still not leave."""
    page = PageMap.model_validate(_block()).model_copy(update={"url": template})
    with pytest.raises(ValueError, match=r"leaves the copy|must start with"):
        fill_urls(page, "http://127.0.0.1:41234/app")


@pytest.mark.parametrize(
    "copy",
    [
        "file:///tmp/app",
        "javascript:alert(1)",
        "127.0.0.1:41234",
        "http://user@127.0.0.1:41234",
        "http://127.0.0.1:41234/?next=x",
    ],
)
def test_fill_urls_refuses_a_copy_address_that_isnt_one(copy: str) -> None:
    with pytest.raises(ValueError, match="isn't the copy's page address"):
        fill_urls(PageMap.model_validate(_block()), copy)


def _target(options: dict[str, Any]) -> TargetConfig:
    return TargetConfig("bot", "http", None, None, {"url": "http://localhost:8000", **options})


def test_page_map_of_reads_the_targets_page_block() -> None:
    page = page_map_of(_target({"page": _block()}))
    assert page == PageMap.model_validate(_block())


def test_page_map_of_a_target_without_a_page_is_none() -> None:
    assert page_map_of(_target({})) is None


def test_page_map_of_refuses_a_bad_block_in_plain_words() -> None:
    with pytest.raises(ConfigError) as e:
        page_map_of(_target({"page": _with(url="http://localhost:5173/chat")}))
    assert str(e.value).startswith("targets.bot.page.url: ")
    assert "must start with {copy_page}" in str(e.value)

    with pytest.raises(ConfigError) as e:
        page_map_of(_target({"page": _with(ready={"css": "#m"}, nope=1)}))
    said = str(e.value)
    assert "targets.bot.page.ready: CSS selectors aren't allowed" in said
    assert "targets.bot.page.nope: not a key the page map has" in said
    assert "Value error" not in said

    with pytest.raises(ConfigError, match=r"targets\.bot\.page\.actions: missing"):
        page_map_of(_target({"page": {k: v for k, v in _block().items() if k != "actions"}}))

    with pytest.raises(ConfigError, match=r"targets\.bot\.page: must be a mapping"):
        page_map_of(_target({"page": "{copy_page}/chat"}))
