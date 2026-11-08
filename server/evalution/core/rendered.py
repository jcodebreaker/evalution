"""How a reply written in Markdown reads once a page has rendered it (ADR 0010).

A page doesn't show a reply's Markdown: a table loses its bars and rule (and may gain row numbers),
a link shows its text and hides its address, a code block loses its fences, a list its numbers. So
"did the page show this reply" is asked of words, not characters: the reply's words, read as the
page renders them, all on the page in their order, close together, and after the person's own
message, so the message's echo of them never stands in for the reply.

Both the page target (when it waits for a reply to show) and the ``shown`` check (when it judges
what showed) read replies here, so they can't disagree.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^[ \t]*(?:```|~~~).*$", re.MULTILINE)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_LIST_MARK = re.compile(r"^[ \t]*(?:\d+[.)]|[-*+])[ \t]+", re.MULTILINE)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}[-:| \t]*$", re.MULTILINE)
_WORD = re.compile(r"\w+")
#: How far the reply's words may spread on the page: a rendered table numbers its rows, a list
#: may keep a label between items. Words spread wider than this aren't the reply showing.
_SPREAD, _SLACK = 2, 10


def page_words(text: str) -> list[str]:
    """The words of rendered text, lowercase, in order."""
    return [word.lower() for word in _WORD.findall(text)]


def reply_words(markdown: str) -> list[str]:
    """The words a page shows of *markdown*: fences, link addresses, list markers and a table's
    rule left out."""
    text = _FENCE.sub(" ", markdown)
    text = _LINK.sub(r"\1", text)
    text = _AUTOLINK.sub(r"\1", text)
    text = _TABLE_RULE.sub(" ", text)
    text = _LIST_MARK.sub(" ", text)
    return page_words(text)


def _spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group().lower(), m.start(), m.end()) for m in _WORD.finditer(text)]


def _showings(found: list[tuple[str, int, int]], message: str) -> list[tuple[int, int]]:
    """Every place *found* shows the person's *message* whole, as word indices, first to last."""
    said = page_words(message)
    words = [word for word, _, _ in found]
    return [
        (start, start + len(said) - 1)
        for start in range(len(words) - len(said) + 1)
        if said and words[start : start + len(said)] == said
    ]


def shown_span(
    reply: str, page: str, *, message: str = "", after: int = 0
) -> tuple[int, int] | None:
    """Where *page* shows *reply*: the character span of its newest showing, its words in order
    and close together, starting at or after word *after*, and not inside a showing of the
    person's *message* (which may echo the reply's words). ``None`` when it doesn't show."""
    want = reply_words(reply)
    if not want:
        return None
    found = _spans(page)
    echoes = _showings(found, message)
    widest = _SPREAD * len(want) + _SLACK
    # Newest first: each place the reply's last word shows, from the end of the page back, read
    # backwards from there for the rest of its words, closest first.
    for end in range(len(found) - 1, after - 1, -1):
        if found[end][0] != want[-1]:
            continue
        j, start = len(want) - 2, end
        for i in range(end - 1, max(after, end - widest) - 1, -1):
            if j < 0:
                break
            if found[i][0] == want[j]:
                start, j = i, j - 1
        if j >= 0 or any(lo <= start and end <= hi for lo, hi in echoes):
            continue
        return found[start][1], found[end][2]
    return None


def shows(reply: str, page: str, *, message: str = "", after: int = 0) -> bool:
    """Whether *page* shows *reply* (:func:`shown_span`)."""
    return shown_span(reply, page, message=message, after=after) is not None
