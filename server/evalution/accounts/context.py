"""The seam between signing in and everything else.

A request's auth (:mod:`evalution.api.auth`) settles who is asking and in which workspace, and
puts them on ``request.state.user`` (a user id, ``None`` locally or for a token) and
``request.state.workspace`` (a workspace id, :data:`LOCAL` locally). It also sets
:data:`acting_user` for the request, so work the request starts, a run included, carries it into
its tasks (asyncio copies the context into each task it creates): a model call made for that work
uses that person's own model key.
"""

from __future__ import annotations

from contextvars import ContextVar

from evalution.storage.database import LOCAL

__all__ = ["LOCAL", "acting_user"]

#: The user the current work is done for, if anyone signed in started it.
acting_user: ContextVar[str | None] = ContextVar("acting_user", default=None)
