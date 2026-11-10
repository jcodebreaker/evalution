"""How far a copy of the app stops while its stores are put back (ADR 0009, and phase 4.1 of
``docs/plans/isolation.md``).

Each driver says what it needs (:data:`~evalution.isolation.drivers.base.STOPS`): a Postgres
database is put back with the app up, a volume with the services that mount it stopped, a file
with the whole copy stopped. A copy stops as far as the widest of its drivers needs, and wholly
when the app keeps state in its own memory, which only a restart clears.

Anything less than a whole stop is used only once a rehearsal proved it (``plan.hot`` is
``proven``); a plan that wasn't, or failed it, keeps the whole stop and start, today's path. A
copy that doesn't answer after a put back with it up raises :class:`NoAnswer`: the caller falls
back to the whole stop and start.
"""

from __future__ import annotations

from collections.abc import Sequence

from evalution.isolation.drivers.base import widest
from evalution.isolation.launch import ComposeCopy, LaunchError, ProcessCopy
from evalution.isolation.plan import Plan, holds_state

#: Seconds a copy put back with its app up may take to answer its health call.
ANSWER_SECONDS = 60


class NoAnswer(LaunchError):
    """The copy didn't answer after a put back that left it (partly) up."""


def wanted(plan: Plan, drivers: Sequence[object], copy: object) -> str:
    """How far *copy* would stop for *drivers*, before any rehearsal says it holds: only a compose
    copy can stop less than wholly."""
    if not isinstance(copy, ComposeCopy):
        return "all"
    return widest(drivers, holds_state=holds_state(plan))


def level(plan: Plan, drivers: Sequence[object], copy: object) -> str:
    """How far *copy* stops while *drivers* put its stores back in a run: :func:`wanted`, once the
    plan's rehearsal proved it; else the whole copy."""
    if plan.hot != "proven":
        return "all"
    return wanted(plan, drivers, copy)


async def services_for(copy: object, drivers: Sequence[object]) -> list[str]:
    """The services of *copy* that mount a volume one of *drivers* puts back."""
    volumes = [str(getattr(d, "volume", "")) for d in drivers if getattr(d, "volume", None)]
    if not isinstance(copy, ComposeCopy) or not volumes:
        return []
    return await copy.mounting(volumes)


async def stop(copy: ComposeCopy | ProcessCopy, how: str, *, keep: Sequence[str] = (),
               services: Sequence[str] = ()) -> None:  # fmt: skip
    """Stop *copy* as far as *how* says. ``all``: every service but the containers in *keep* (the
    databases a driver works in), or a process copy whole. ``services``: *services* only."""
    if how == "none":
        return
    if not isinstance(copy, ComposeCopy):
        await copy.stop()
    elif how == "services":
        await copy.stop_services(services)
    else:
        await copy.pause(keep)


async def start(copy: ComposeCopy | ProcessCopy, how: str) -> None:
    """Start what :func:`stop` stopped and wait until the copy answers.

    :raises NoAnswer: Put back with the copy (partly) up, it doesn't answer: fall back.
    :raises LaunchError: The whole copy didn't come back.
    """
    if not isinstance(copy, ComposeCopy):
        await copy.start()
        return
    if how == "all":
        await copy.resume()
        return
    try:
        if how == "services":
            await copy.resume()
        if not await copy.answers(ANSWER_SECONDS):
            raise NoAnswer(f"{copy.project} didn't answer after a put back with it up")
    except NoAnswer:
        raise
    except LaunchError as exc:
        raise NoAnswer(str(exc)) from exc
