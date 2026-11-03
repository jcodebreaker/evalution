"""What the code that reads a target's past needs of its runs, so it never depends on the runner."""

from __future__ import annotations

from typing import Any, Protocol

from evalution.llm import LLM, Caller
from evalution.targets import Targets


class History(Protocol):
    """The runs of this server, as those that only read them see them."""

    targets: Targets
    llm: LLM | None

    def caller(self, key: str) -> Caller | None: ...

    def all(
        self, target: str | None = None, set_name: str | None = None
    ) -> list[dict[str, Any]]: ...
