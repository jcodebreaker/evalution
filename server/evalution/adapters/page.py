"""``adapter: page``: a target reached only through its own page, with no API adapter under it
(ADR 0010, amended). Not an adapter, and not a kind of app: this module has no ``INFO``, so
:func:`evalution.adapters.infos` leaves it out, and with it discovery, the connect form, the
conformance kit and every list of adapters.

What :func:`create` gives is a stand-in. It says what a page-only target can do
(:func:`page_only_capabilities`), so whatever asks a target that outside a run (the test writer, the
fit verdicts, the API) is told the truth, and it refuses to play: a page-only target plays only in
a run, on evalution's own copy of the app, where the ``runs`` layer puts the page target in its
place (:mod:`evalution.runs.page.target`, with no inner adapter). Its ``close`` and ``cancel`` are
no-ops, for sessions a restart left behind: the browser that held them went with the process.

.. code-block:: yaml

    targets:
      chat:
        adapter: page
        page: {url: "{copy_page}/chat", ...}   # the page map: how the page is played
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evalution.adapters.base import Capabilities, Isolation, Session, Target, Turn
from evalution.config import TargetConfig

#: A target's ``adapter`` when it is reached through its page alone.
NAME = "page"
#: How such a target is named where an adapter's label would be.
LABEL = "Its page only"
#: What a page-only target's isolation means before a run lays its copy's plan over it.
NOTE = "Played through its page only, on evalution's own copy of the app."
#: Which of a test person's fields the page honours: the browser's language and time zone.
PERSON = frozenset({"language", "timezone"})


class PlaysOnlyOnItsPage(RuntimeError):
    """A page-only target was asked to play outside a run on evalution's copy."""


def page_only_capabilities(new_chat: bool) -> Capabilities:
    """What a target played through its page alone can do. No state checks and no probe (there is
    no API to read the app's state through), no reset (each try opens a new browser context and a
    new chat; what the app keeps on its server is put back by the copy's restore, before the try,
    not by the target), a new conversation only when the page map has New chat, no files, no
    Stop, no watch, no sections, and one session at a time."""
    return Capabilities(
        Isolation.NONE,
        NOTE,
        conversation_break=new_chat,
        person=PERSON,
    )


def has_new_chat(options: dict[str, Any]) -> bool:
    """Whether a target's ``page:`` block maps New chat, read as it is written."""
    page = options.get("page")
    actions = page.get("actions") if isinstance(page, dict) else None
    return isinstance(actions, dict) and actions.get("new_chat") is not None


class PageOnly(Target):
    """The stand-in (see the module's docstring)."""

    def __init__(self, target: TargetConfig) -> None:
        self.name = target.name
        self.capabilities = page_only_capabilities(has_new_chat(target.options))  # type: ignore[misc]

    def _refused(self) -> PlaysOnlyOnItsPage:
        return PlaysOnlyOnItsPage(
            f"{self.name} plays only through its page, and only on evalution's own copy of the "
            "app: a run starts the copy and plays it there"
        )

    async def open(self, run: str) -> Session:
        raise self._refused()

    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),  # type: ignore[assignment]
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn:
        raise self._refused()

    async def close(self, session: Session) -> None:
        """Nothing to close outside a run: its page went with the process that held it."""


def create(target: TargetConfig) -> Target:
    return PageOnly(target)
