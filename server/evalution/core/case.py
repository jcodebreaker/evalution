"""What a test is, as data: a set, a case and its turns, and the words their files may use.

Loading and checking those files is :mod:`evalution.suite.cases`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: The model keys a set may pin for its Grader and filler writer (``model.models`` in the config).
#: ``judge`` is the optional one that grades by default, and is ``strong`` unless named.
GRADERS = ("judge", "strong", "light")
ORDERS = ("exact", "in_order", "any_order")
MATCHERS = ("eq", "contains", "re", "len", "any")
DEFAULT_PASS_MARKS = {"tools": 0.8, "reply": 0.7}
DEFAULT_TRIES = 3
CASE_SUFFIX = ".case.json"
#: A target, set or case id: one safe path segment.
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
#: State checks evalution decides itself from the traces; any other key goes to the adapter.
CONTEXT_CHECKS = ("context_contains", "context_lacks")
#: What a test can need a try to start clean of: each its own promise, as a new conversation isn't
#: new memory.
CLEAN_KINDS = ("conversation", "memory", "files")


@dataclass(frozen=True)
class EvalSet:
    """One ``set.json``.

    :param set: The folder name.
    :param grader: The Grader's model key.
    :param version: Short hash of the file.
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

    @property
    def is_message(self) -> bool:
        return not self.filler and self.state is None and not self.new_conversation


@dataclass(frozen=True)
class Case:
    """One ``<case>.case.json``.

    :param case: The file stem.
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
