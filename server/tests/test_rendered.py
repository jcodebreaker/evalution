"""A reply's Markdown against what a page rendered of it (ADR 0010). The page texts are what
relay-lite's page showed for these replies, on evalution's copy, 2026-11-08."""

from __future__ import annotations

import pytest

from evalution.core.rendered import reply_words, shown_part, shown_span, shows

TABLE_ASKED = (
    "Make a Markdown table with two columns, Fruit and Colour, and three rows: apple red, "
    "banana yellow, grape purple. Nothing else."
)
CASES = [
    pytest.param(
        "| Fruit  | Colour |\n|--------|--------|\n| Apple  | Red    |\n| Banana | Yellow |\n"
        "| Grape  | Purple |",
        f"Work in…\nHome\n{TABLE_ASKED}\nNov 8, 3:43 PM\n\t\nFruit\n\t\nColour\n\n1\t\nApple\n\t\n"
        "Red\n\n2\t\nBanana\n\t\nYellow\n\n3\t\nGrape\n\t\nPurple\n3 rows\nActivity",
        TABLE_ASKED,
        id="a table, its rows numbered by the page",
    ),
    pytest.param(
        "[Python website](https://www.python.org)",
        "Home\nReply with only a Markdown link to https://www.python.org with the text Python "
        "website.\nNov 8, 3:43 PM\n\nPython website\n\nActivity",
        "Reply with only a Markdown link to https://www.python.org with the text Python website.",
        id="a link, its address hidden",
    ),
    pytest.param(
        '```python\nfor _ in range(3):\n    print("hello")\n```',
        "Home\nReply with only a Python code block that prints hello three times with a for loop."
        '\nNov 8, 3:43 PM\nfor _ in range(3):\n    print("hello")\n\nActivity',
        "Reply with only a Python code block that prints hello three times with a for loop.",
        id="a code block, its fences gone",
    ),
    pytest.param(
        "1. The sea keeps no secrets from the moon.\n2. Waves write and erase the shore.",
        "Home\nWrite a numbered list about the sea.\nNov 8, 3:44 PM\nThe sea keeps no secrets "
        "from the moon.\nWaves write and erase the shore.\nActivity",
        "Write a numbered list about the sea.",
        id="a numbered list, its numbers drawn by the page",
    ),
]


@pytest.mark.parametrize(("reply", "page", "message"), CASES)
def test_what_relay_lite_rendered_shows_the_reply(reply: str, page: str, message: str) -> None:
    span = shown_span(reply, page, message=message)
    assert span is not None
    assert page[span[0] : span[1]].strip()


def test_the_persons_own_words_never_stand_in_for_the_reply() -> None:
    message = "Say exactly: apple red banana yellow"
    page = f"Home\n{message}\nNov 8\nSomething went wrong. Try again."
    assert not shows("apple red banana yellow", page, message=message)


def test_a_reply_said_before_this_turn_doesnt_count_again() -> None:
    page = "Ok.\nSay ok again\nThinking"
    before = len(reply_words("Ok.")) + 3  # the words already there before this turn
    assert not shows("Ok.", page, after=before)
    assert shows("Ok.", page + "\nOk.", after=before)


def test_words_scattered_far_apart_arent_the_reply_showing() -> None:
    page = "apple " + "filler " * 40 + "banana"
    assert not shows("apple banana", page)
    assert shows("apple banana", "apple 1 banana")


def test_a_reply_with_no_words_shows_nowhere() -> None:
    assert not shows("```\n```", "anything")


def test_the_newest_showing_is_the_one_found() -> None:
    page = "Your dog is called Biscuit.\nWhat's my dog called?\nYour dog is called Biscuit."
    start, _ = shown_span("Your dog is called Biscuit.", page, message="What's my dog called?")
    assert start == page.rindex("Your dog")


def test_how_far_a_page_showed_a_reply_names_where_it_stopped() -> None:
    """The mockup's table: the page showed the text above it, and none of its rows."""
    reply = (
        "Here's a comparison:\n\n| Trait | Cats | Dogs |\n|---|---|---|\n"
        "| Walks | No | Yes |\n| Purring | Yes | No |"
    )
    message = "Compare cats and dogs in a table"
    page = f"{message}\nHere's a comparison:\nMessage…\nSend"
    assert shown_part(reply, page, message=message) == (
        "Here's a comparison",
        "Trait Cats Dogs Walks No Yes …",
    )
    # Nothing of it showed; and all of it did, which is no part missing.
    assert shown_part("Your dog is Biscuit.", "Loading…") == ("", "Your dog is Biscuit")
    assert shown_part("Your dog is Biscuit.", "Your dog is Biscuit.") is None
    assert shown_part("", "anything") is None
