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

#: A turn's error when a target reached through its page alone settled and showed no reply: the
#: page target says it, and the ``shown`` check reads it as the page's failure.
NOTHING_SHOWN = "the page showed no reply"

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
    return page_words(_rendered(markdown))


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


def reply_after(page: str, *, message: str = "", after: int = 0) -> str:
    """What *page* shows after the person's *message*, as the page renders it: a page's reply when
    nothing on the page says which part is the reply (ADR 0010, a target reached through its page
    alone). Read from the first showing of the whole message at or after word *after* (where what
    the page shows for this turn starts): the person's message comes before the reply, which may
    repeat its words. When the page doesn't echo the message, from word *after* itself. Empty when
    nothing follows."""
    found = _spans(page)
    echoes = [(lo, hi) for lo, hi in _showings(found, message) if lo >= after]
    start = echoes[0][1] + 1 if echoes else after
    if start >= len(found):
        return ""
    return page[found[start][1] :].strip()


def _rendered(markdown: str) -> str:
    """*markdown* as a page renders it, for :func:`reply_words`: its text, not just its words."""
    text = _FENCE.sub(" ", markdown)
    text = _LINK.sub(r"\1", text)
    text = _AUTOLINK.sub(r"\1", text)
    text = _TABLE_RULE.sub(" ", text)
    return _LIST_MARK.sub(" ", text)


#: How many of the reply's words :func:`shown_part` names of what's missing.
_MISSING_WORDS = 6


def shown_part(reply: str, page: str, *, message: str = "") -> tuple[str, str] | None:
    """How far *page* showed *reply*: the start of the reply whose words *page* shows in order
    after the person's *message* (:func:`reply_after`), and the reply's words from the first it
    doesn't, a few of them, as the page renders them. ``None`` when every word shows, or the reply
    has none: then it's not a part that's missing, and this says nothing."""
    text = _rendered(reply)
    words = _spans(text)
    if not words:
        return None
    seen = page_words(reply_after(page, message=message))
    at, held = 0, 0
    for word, _, _ in words:
        try:
            at = seen.index(word, at) + 1
        except ValueError:
            break
        held += 1
    if held == len(words):
        return None
    shown = _flat(text[words[0][1] : words[held - 1][2]]) if held else ""
    rest = words[held : held + _MISSING_WORDS]
    missing = _flat(text[rest[0][1] : rest[-1][2]])
    if held + _MISSING_WORDS < len(words):
        missing += " …"
    return shown, missing


def _flat(text: str) -> str:
    """*text* on one line, a table's bars left out."""
    return " ".join(text.replace("|", " ").split())


def shows(reply: str, page: str, *, message: str = "", after: int = 0) -> bool:
    """Whether *page* shows *reply* (:func:`shown_span`)."""
    return shown_span(reply, page, message=message, after=after) is not None
