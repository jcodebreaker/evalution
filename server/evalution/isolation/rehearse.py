"""A plan for resetting an app's stores, tried once on a copy before any test relies on it.

The plan's stores are each a :class:`~evalution.isolation.drivers.Driver`. The rehearsal:

1. stops the app and keeps each planned store as it is now: the **starting state**, and its hash;
2. starts the app and sends a probe carrying a canary, watching the folders the app uses here;
3. stops it, and refuses the plan if the app wrote into a writable folder it shares with the
   person's own app (``shared``: putting that back would change the person's files); fails if
   the canary reached nothing planned, or if the app wrote outside the plan: a file here no
   planned store covers, or (inside containers, where nothing can be watched file by file) a
   place the canary turned up that no planned store holds;
4. puts every planned store back to the starting state and fails if its hash doesn't match;
5. starts the app again and fails if the canary is still anywhere it can be looked for;
6. always ends with the starting state put back and the app started, so a rehearsal leaves the
   app exactly as it found it, whatever happened.

With ``hot``, once the plan is proven, it is put back once more as a run would put it back with
the app up (or only the services a store needs stopped, :mod:`.hot`): the app must answer, take a
second probe, and after a second such put back hold the starting state with that probe's canary
gone. That proves the faster path (``hot`` is ``proven``); an app that breaks under it is recorded
as needing a restart (``failed``), and the plan stays proven for the whole stop and start.

Only what the plan names is ever replaced, and only by its own copy from step 1. The copies live
in a folder of evalution's own. A planned store whose hash the probe didn't move is reported as
unconfirmed, not as a failure. Tables the plan leaves out (traces) are never a leak.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from evalution.code import SKIP_DIRS
from evalution.isolation import observe
from evalution.isolation.drivers import Driver
from evalution.isolation.drivers.files import SIDECARS, FileStore

__all__ = [
    "SIDECARS", "FileStore", "Rehearsal", "Runner", "Step", "rehearse", "scan_files", "within",
]  # fmt: skip


class Runner(Protocol):
    """The app as a rehearsal works it: started, stopped with its stores kept, and probed."""

    async def start(self) -> None:
        """Start the app (again) and wait until it answers."""
        ...

    async def stop(self) -> None:
        """Stop it, its stores kept, so drivers can copy them whole."""
        ...

    async def probe(self, canary: str) -> object:
        """Send one message carrying *canary*."""
        ...

    def watch_roots(self) -> list[Path]:
        """Folders on this computer the running app writes in (may be none)."""
        ...

    async def search(self, canary: str) -> list[str]:
        """Every place *canary* is, as a path here or ``container:where`` inside a container:
        the watched folders and every container of the app, those it started included."""
        ...


class HotRunner(Runner, Protocol):
    """A runner that can also stop the app only as far as its stores need (:mod:`.hot`)."""

    async def hot_stop(self) -> None:
        """Stop only what the stores need stopped (maybe nothing)."""
        ...

    async def hot_start(self) -> None:
        """Start that again and make one health call; raise when the app doesn't answer."""
        ...


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Rehearsal:
    ok: bool
    steps: list[Step] = field(default_factory=list)
    #: Places outside the plan the canary reached, or the app wrote to.
    leaks: list[str] = field(default_factory=list)
    #: Planned stores the probe didn't change: kept in the plan, but not shown to be needed.
    unconfirmed: list[str] = field(default_factory=list)
    #: Seconds the whole rehearsal took.
    seconds: float = 0.0
    #: Put back with the app up: ``proven``, ``failed`` (it needs a restart), ``None`` untried.
    hot: str | None = None
    #: Places in a folder the copy shares with the person's own app that the probe wrote: the
    #: plan is refused, since putting them back would change the person's own files.
    shared: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            f"{'ok ' if s.ok else 'NO '} {s.name}{': ' + s.detail if s.detail else ''}"
            for s in self.steps
        ]
        if self.hot == "proven":
            lines.append("Put back with the app up: proven; runs don't restart it.")
        elif self.hot == "failed":
            lines.append("Put back with the app up: it broke; runs stop and start it instead.")
        if self.unconfirmed:
            lines.append(
                f"Unconfirmed (the probe didn't change them): {', '.join(self.unconfirmed)}"
            )
        failed = next((s for s in self.steps if not s.ok), None)
        if self.shared:
            lines.append(
                "Refused: a test wrote into a folder your app shares with the copy "
                f"({', '.join(self.shared)}). Putting it back would change your app's own files, "
                "so no plan is proven while it is shared: mount it read-only, or give the copy "
                "a folder of its own."
            )
        lines.append(
            "Proven: the plan resets everything the probe reached."
            if self.ok
            else "Not proven: "
            + (", ".join(self.leaks) or (failed.detail if failed else "") or "see above")
        )
        return "\n".join(lines)


def within(place: str, shared: Sequence[str]) -> bool:
    """Whether *place* (a path here, or ``container:where``) is inside one of *shared*: folders
    here, or ``volume:NAME``."""
    where = place if place.startswith("/") else place.partition(":")[2]
    return any(where == s or where.startswith(s.rstrip("/") + "/") for s in shared if s)


def scan_files(roots: Sequence[Path], canary: str) -> list[str]:
    """Every file under *roots* that holds *canary*."""
    found = []
    for root in roots:
        files = [root] if root.is_file() else root.rglob("*")
        for f in files:
            if any(part in SKIP_DIRS for part in f.parts) or not f.is_file():
                continue
            if observe.holds(str(f), canary):
                found.append(str(f))
    return sorted(set(found))


async def _hot_round(
    runner: HotRunner,
    kept: Sequence[tuple[Driver, Path]],
    before: dict[str, str],
    outside: Callable[[list[str]], list[str]],
    step: Callable[..., bool],
) -> str:
    """Put the stores back twice as a run would with the app up, a probe between: ``proven`` when
    the app answers each time, takes the probe, and ends in the starting state with that probe's
    canary nowhere; else ``failed``. Never fails the plan: a run then restarts the app."""
    canary = f"eval-rehearsal-{secrets.token_hex(6)}"
    name = "put back with the app up"

    async def put_back() -> None:
        await runner.hot_stop()
        for d, copy in kept:
            await asyncio.to_thread(d.put_back, copy)
        await runner.hot_start()

    try:
        await put_back()
        await runner.probe(canary)
        await put_back()
        now = {d.name: await asyncio.to_thread(d.digest) for d, _ in kept}
        differ = [n for n, h in now.items() if h != before.get(n)]
        left = [p for d, _ in kept for p in await asyncio.to_thread(d.holding, canary)]
        left += outside(await runner.search(canary))
        said = ", ".join([*differ, *left])
        return "proven" if step(name, not differ and not left, said) else "failed"
    except Exception as exc:  # the app broke under it: a run restarts it instead
        step(name, False, f"{type(exc).__name__}: {exc}")
        return "failed"


async def rehearse(
    drivers: Sequence[Driver],
    runner: Runner,
    workdir: Path,
    *,
    ignore: Sequence[str] = (),
    hot: bool = False,
    shared: Sequence[str] = (),
) -> Rehearsal:
    """Try the plan once on a running app (see the module's docstring).

    :param drivers: The plan's stores, on the app the runner works.
    :param workdir: An empty folder of evalution's own, for the starting state.
    :param ignore: Places never counted as a leak, ``db.schema.table`` (the plan's traces).
    :param hot: Prove the put back with the app up too; *runner* is then a :class:`HotRunner`.
        Sends a second probe.
    :param shared: Writable folders (or ``volume:NAME``) the copy shares with the person's own
        app. The probe writing into one refuses the plan, whatever covers it: putting it back
        would change the person's own files.
    """
    started_at = asyncio.get_running_loop().time()
    out = Rehearsal(ok=False)
    canary = f"eval-rehearsal-{secrets.token_hex(6)}"
    kept: list[tuple[Driver, Path]] = []
    before: dict[str, str] = {}
    running = True  # a rehearsal is handed a started app

    def step(name: str, ok: bool, detail: str = "") -> bool:
        out.steps.append(Step(name, ok, detail))
        return ok

    def outside(places: list[str]) -> list[str]:
        """*places* no planned store covers and the plan doesn't leave alone."""
        return [
            p
            for p in places
            if not any(d.covers(p) for d in drivers)
            and not any(p.partition(":")[2] == i or p == i for i in ignore)
        ]

    async def holding() -> list[str]:
        found: list[str] = []
        for d in drivers:
            found += await asyncio.to_thread(d.holding, canary)
        return found

    async def digests() -> dict[str, str]:
        return {d.name: await asyncio.to_thread(d.digest) for d in drivers}

    try:
        await runner.stop()
        running = False
        before = await digests()
        for i, d in enumerate(drivers):
            await asyncio.to_thread(d.keep, workdir / str(i))
            kept.append((d, workdir / str(i)))
        step("starting state kept", True, f"{len(kept)} stores")

        await runner.start()
        running = True
        roots = runner.watch_roots()
        roots += [
            s.path.parent
            for s in drivers
            if isinstance(s, FileStore) and not any(s.path.is_relative_to(r) for r in roots)
        ]
        snap = await asyncio.to_thread(observe.snapshot, roots)
        await runner.probe(canary)
        found = await runner.search(canary)  # while it runs: a container is searched from inside
        await runner.stop()
        running = False
        after = await asyncio.to_thread(observe.snapshot, roots)
        created, changed, _ = observe.diff(snap, after)
        moved = await digests()
        out.unconfirmed = [d.name for d in drivers if moved[d.name] == before[d.name]]
        if shared:
            into = [p for p in sorted({*created, *changed, *found}) if within(p, shared)]
            if not step("the app wrote nothing into a folder shared with your app", not into,
                        ", ".join(into)):  # fmt: skip
                out.leaks, out.shared = list(into), into
                return out
        reached = await holding()
        if not step("the probe reached a planned store", bool(reached), ", ".join(reached)):
            out.leaks = outside(found)
            return out
        stray = outside(sorted({*created, *changed, *found}))
        if not step("the app wrote only to planned stores", not stray, ", ".join(stray)):
            out.leaks = stray
            return out

        for d, copy in kept:
            await asyncio.to_thread(d.put_back, copy)
        back = await digests()
        differ = [d.name for d in drivers if back[d.name] != before[d.name]]
        if not step("put back to the starting state", not differ, ", ".join(differ)):
            return out

        await runner.start()
        running = True
        left = [*await holding(), *outside(await runner.search(canary))]
        if not step("the canary is gone after the reset", not left, ", ".join(left)):
            out.leaks = left
            return out
        out.ok = True
        if hot:
            out.hot = await _hot_round(runner, kept, before, outside, step)  # type: ignore[arg-type]
        return out
    except Exception as exc:  # a driver or the app failing ends the rehearsal, not the cleanup
        step("stopped by an error", False, f"{type(exc).__name__}: {exc}")
        out.ok = False
        return out
    finally:
        try:
            if running:
                await runner.stop()
            for d, copy in kept:
                await asyncio.to_thread(d.put_back, copy)
            if kept:
                now = await digests()
                same = all(now[d.name] == before[d.name] for d, _ in kept)
                step("left as it was found", same)
                out.ok = out.ok and same
            for d, copy in kept:
                drop = getattr(d, "drop_golden", None)
                if drop is not None:
                    await asyncio.to_thread(drop, copy)
        except Exception as exc:  # said in the report, then the app is started
            step("left as it was found", False, f"{type(exc).__name__}: {exc}")
            out.ok = False
        finally:
            await runner.start()
            out.seconds = round(asyncio.get_running_loop().time() - started_at, 1)
