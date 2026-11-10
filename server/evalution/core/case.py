"""What a test is, as data: a set, a case and its turns, and the words their files may use.

Reading and checking their text is :mod:`evalution.suite.cases`; keeping them,
:mod:`evalution.suite.store`."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: The model keys a set may pin for its Grader and filler writer (``model.models`` in the config).
#: ``judge`` is the optional one that grades by default, and is ``strong`` unless named.
GRADERS = ("judge", "strong", "light")
ORDERS = ("exact", "in_order", "any_order")
MATCHERS = ("eq", "contains", "re", "len", "any")
DEFAULT_PASS_MARKS = {"tools": 0.8, "reply": 0.7}
DEFAULT_TRIES = 3
#: The most tries a test plays in one run, asked for or not.
MOST_TRIES = 20
CASE_SUFFIX = ".case.json"
#: A target, set or case id: one safe path segment.
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
#: State checks evalution decides itself from the traces; any other key goes to the adapter.
CONTEXT_CHECKS = ("context_contains", "context_lacks")
#: What a test can need a try to start clean of: each its own promise, as a new conversation isn't
#: new memory.
CLEAN_KINDS = ("conversation", "memory", "files")
#: What a try played through the app's page keeps (``page.proof``), and when each is kept: for
#: every try, only for one that fails, or not taken at all (ADR 0010).
PROOF_PARTS = ("screenshots", "video")
PROOF_KEEPS = ("always", "failed", "off")
#: Where a UI check looks for an element (``page.shows``, ``page.hides``): under the turn's reply,
#: as the page map finds it, or anywhere on the page.
WITHIN = ("reply", "page")
#: The longest yes/no question a ``page.looks`` entry may ask of a screenshot.
LOOK_CHARS = 300
#: The ARIA roles Playwright finds an element by (its ``AriaRole``): the roles a UI check may name.
ARIA_ROLES = frozenset(
    {
        "alert", "alertdialog", "application", "article", "banner", "blockquote", "button",
        "caption", "cell", "checkbox", "code", "columnheader", "combobox", "complementary",
        "contentinfo", "definition", "deletion", "dialog", "directory", "document", "emphasis",
        "feed", "figure", "form", "generic", "grid", "gridcell", "group", "heading", "img",
        "insertion", "link", "list", "listbox", "listitem", "log", "main", "marquee", "math",
        "menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio", "meter", "navigation",
        "none", "note", "option", "paragraph", "presentation", "progressbar", "radio",
        "radiogroup", "region", "row", "rowgroup", "rowheader", "scrollbar", "search",
        "searchbox", "separator", "slider", "spinbutton", "status", "strong", "subscript",
        "superscript", "switch", "tab", "table", "tablist", "tabpanel", "term", "textbox", "time",
        "timer", "toolbar", "tooltip", "tree", "treegrid", "treeitem",
    }
)  # fmt: skip


@dataclass(frozen=True)
class EvalSet:
    """One ``set.json``.

    :param set: The set's id.
    :param grader: The Grader's model key.
    :param version: Short hash of its text.
    :param about: One line on where the set's cases come from, when the file says.
    """

    set: str
    name: str
    grader: str
    #: The tool-call and reply scores a try needs to pass (``pass_marks``; the old
    #: ``pass_scores`` key still reads).
    pass_marks: dict[str, float]
    version: str
    about: str | None = None
    #: The model key that writes filler messages (``filler``, default ``light``).
    filler: str = "light"


@dataclass(frozen=True)
class CaseTurn:
    """A message turn, a filler turn (``filler`` > 0: that many generated messages on
    ``about``), a state check (``state``: the ``expect_state`` object) or a conversation break
    (``new_conversation``: the same person starts a new conversation, keeping what the app
    remembers of them)."""

    message: str
    files: tuple[str, ...] = ()
    stop_after: float | None = None
    #: The try finds nothing from an earlier one: it gets a new token in its session
    #: (``Session.extra["fresh"]``) for the adapter to carry; the message is never changed.
    fresh: bool = False
    filler: int = 0
    about: str = ""
    state: dict[str, Any] | None = None
    new_conversation: bool = False
    #: The test wants the app to stop and wait here (``"blocked": true``), an approval, say.
    expect_blocked: bool = False
    #: Read the app's memory here, to measure whether it holds this (``{"memory_holds": "..."}``).
    #: A measurement: it never decides the try.
    memory_holds: str | None = None

    @property
    def is_message(self) -> bool:
        return (
            not self.filler
            and self.state is None
            and not self.new_conversation
            and self.memory_holds is None
        )


@dataclass(frozen=True)
class UiElement:
    """One element a UI check looks for on the page (``page.shows`` or ``page.hides``), named the
    way the page map names one: a role with its accessible name, a test id, visible text, or more
    than one of them together.

    :param within: ``reply``: under the turn's reply, where the page map finds replies; ``page``:
        anywhere on the page.
    :param least: How many must show for ``shows`` to hold (``count``; 1 when it says none).
    :param most: How many may show at most; ``None``: any number.
    :param turn: The message turn (from 1) it is looked for after; ``None``: every turn that has
        a reply.
    """

    role: str | None = None
    name: str | None = None
    test_id: str | None = None
    text: str | None = None
    within: str = "reply"
    least: int = 1
    most: int | None = None
    turn: int | None = None

    @property
    def key(self) -> str:
        """What a reading of it on the page is kept under: its locator and where it looks. The
        same element in ``shows`` and ``hides`` is read once, and a test that changes it is never
        judged on what was read of another."""
        found = {
            "role": self.role,
            "name": self.name,
            "test_id": self.test_id,
            "text": self.text,
            "within": self.within,
        }
        return json.dumps({k: v for k, v in found.items() if v is not None}, sort_keys=True)

    @property
    def said(self) -> str:
        """The element in plain words: ``button 'Thumbs up'``, ``element with test id 'rate'``."""
        parts = []
        if self.role is not None:
            parts.append(f"{self.role} {self.name!r}" if self.name is not None else self.role)
        if self.test_id is not None:
            parts.append(
                f"test id {self.test_id!r}" if parts else f"element with test id {self.test_id!r}"
            )
        if self.text is not None:
            parts.append(f"text {self.text!r}")
        return " with ".join(parts)


@dataclass(frozen=True)
class UiLook:
    """A yes/no question a vision model answers about a turn's screenshot (``page.looks``).

    :param turn: The message turn (from 1) whose screenshot it asks about; ``None``: the last.
    """

    question: str
    turn: int | None = None


@dataclass(frozen=True)
class Case:
    """One ``<case>.case.json``.

    :param case: The case's id, its file's stem.
    :param tools: The ``tools`` block as written, with ``order`` and ``extras`` filled in.
    :param limits: ``tries`` always; ``seconds`` when the case sets it.
    :param raw: The file as written.
    :param about: One sentence on the behaviour the case checks, when the file says.
    :param source: Where the case came from (``scenario matrix``, ``chat <date>``, ...).
    """

    case: str
    name: str
    turns: tuple[CaseTurn, ...]
    tools: dict[str, Any]
    reference: str
    rubric: tuple[str, ...]
    limits: dict[str, Any]
    version: str
    raw: dict[str, Any]
    about: str | None = None
    source: str | None = None
    #: Rubric lines that must pass (``reply.pass``); ``None``: every line.
    reply_pass: int | None = None
    #: A try that fails fails the test, however many others passed.
    must_hold: bool = False
    #: Words the last reply must have (``reply.contains``) and must not (``reply.lacks``): decided
    #: by code, whatever the case and spacing.
    reply_contains: tuple[str, ...] = ()
    reply_lacks: tuple[str, ...] = ()
    #: What the test needs a try to start clean of (``needs_clean``): ``conversation``, ``memory``,
    #: ``files``. An app that can't promise it refuses the test rather than weakening it.
    needs_clean: tuple[str, ...] = ()
    #: Ask the app to fold older turns from this many tokens (``fold_at_tokens``), so a short
    #: history reaches what a long one would. An app that can't is measured as never folding.
    fold_at_tokens: int | None = None
    #: Words the app's page must show (``page.contains``) and must not (``page.lacks``) of the last
    #: reply, on a try played through the page (ADR 0010): decided by code, from what it showed.
    shown_contains: tuple[str, ...] = ()
    shown_lacks: tuple[str, ...] = ()
    #: The page's checks decide the try (``page.decides``); otherwise they are shown as a guide.
    page_decides: bool = False
    #: What a try played through the page keeps, part by part (``page.proof``), over what the
    #: target's page map says: ``{"screenshots" | "video": "always" | "failed" | "off"}``.
    page_proof: dict[str, str] = field(default_factory=dict)
    #: Elements the page must show (``page.shows``) and must not (``page.hides``), read on the page
    #: by code; and yes/no questions a vision model answers about a turn's screenshot
    #: (``page.looks``). Guides unless ``page.decides``, as the page's other checks are.
    shows: tuple[UiElement, ...] = ()
    hides: tuple[UiElement, ...] = ()
    looks: tuple[UiLook, ...] = ()
    #: How the test says it plays (``plays``): ``api``, through the app's API alone, which a test
    #: with UI checks can't; ``None``: as its target plays.
    plays: str | None = None

    @property
    def ui_checks(self) -> bool:
        """Whether the test checks the page's elements or its look, which only a try played
        through the page can."""
        return bool(self.shows or self.hides or self.looks)

    @property
    def message_turns(self) -> int:
        """How many message turns the test has: what a UI check's ``turn`` counts."""
        return sum(1 for t in self.turns if t.is_message)
