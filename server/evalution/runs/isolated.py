"""Tries played on evalution's own copies of the app, each put back and checked first (ADR 0009).

A target with a proven isolation plan, whose evidence hasn't changed since, plays on copies of the
app: one per slot, ``eval-<target>-<slot>``, started once per run (:class:`Copies`). Each copy's
stores, as the plan names them, are kept once as the **starting state**, with their hashes. Before
every try the copy is stopped as far as its stores need, every store is put back, the copy is
started again, and every store's hash is compared with the starting state's. One that differs, or
can't be put back, leaves the try unjudged: "reset didn't hold: <store>". After the run, however it
ends, every store is put back, the databases' kept copies are dropped and every copy is taken down.

No canary is sent with the tests: the hash check is each try's proof for the stores the plan
names, and the rehearsal that proved the plan showed nothing reaches anywhere else.

A plan whose evidence changed since it was proven (:func:`~evalution.isolation.plan.stale`), or
one marked ``stale``, is rehearsed again on slot 0's copy before the run, and saved as proven or
failed (:func:`rehearse_again`). A target with no proven plan plays on the app as it is, every
result labelled ``none`` (or ``identity``, when its adapter plays each try as a new person), or is
refused when ``isolation.require_proven`` says so (:func:`refusal`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response

from evalution.adapters import infos
from evalution.adapters.base import Capabilities, Isolation, Target, TargetGone
from evalution.config import Config
from evalution.core.case import CLEAN_KINDS
from evalution.isolation.drivers import Driver, PostgresStore
from evalution.isolation.drivers.base import DockerError, Refused
from evalution.isolation.isolate import (
    ComposeCopy,
    Copy,
    adapter_on,
    code_folder,
    copy_of,
    drivers_for,
    plan_spec,
    rehearse_plan,
    roots_of,
    store_id,
    take_down,
    work_folder,
)
from evalution.isolation.launch import LaunchError, free_port
from evalution.isolation.plan import Plan, PlanStore, hashes, stale
from evalution.results.record import isolation, run_isolation
from evalution.suite.cases import CaseError
from evalution.traces import otlp, setup
from evalution.traces.builtin import TARGET_PREFIX, SpanStore

_logger = logging.getLogger(__name__)

#: What fixes a target with no proven plan.
FIX = "evalution isolate --target {target}"
#: The start of a try's ``problem`` when its copy wasn't put back as the plan says.
DIDNT_HOLD = "reset didn't hold"
#: Stores a plan may have that no driver resets: the app's memory is then not promised clean.
_NOT_RESET = ("identity", "none")
#: Kinds of store that hold files a person sent.
_FILE_KINDS = ("volume", "dir")


class NotIsolated(CaseError):
    """The target has no proven isolation plan, and ``isolation.require_proven`` asks for one.
    A :class:`~evalution.suite.cases.CaseError`, so every caller of an estimate shows it as it
    shows a test that can't run."""


@dataclass
class Footing:
    """What a run of a target stands on: its proven plan, or nothing evalution checks.

    :param how: ``proven``, ``identity`` or ``none``, as results say it.
    :param plan: The target's plan, whatever its status; ``None`` when it has none.
    :param version: The plan's number in its history.
    :param changed: Files the plan's evidence cites that changed since it was proven.
    :param problem: Why the plan there is can't be used, in words.
    """

    how: str
    plan: Plan | None = None
    version: int | None = None
    changed: list[str] = field(default_factory=list)
    problem: str | None = None

    @property
    def managed(self) -> bool:
        """The run plays on copies put back by the plan."""
        return self.how == "proven" and self.plan is not None

    @property
    def to_rehearse(self) -> bool:
        """The plan was proven, and its evidence changed since: rehearsed again before a run."""
        return (
            self.plan is not None
            and not self.managed
            and self.plan.status in ("proven", "stale")
            and (bool(self.changed) or self.plan.status == "stale")
        )

    def run_label(self) -> dict[str, Any]:
        return run_isolation(self.how, self.version if self.managed else None)

    def try_label(self) -> dict[str, Any]:
        """A try's label when it isn't played on a copy."""
        return isolation(self.how, problem=self.problem)


def _unmanaged(capabilities: Capabilities) -> str:
    return "identity" if capabilities.isolation is Isolation.IDENTITY else "none"


def footing(config: Config, target: str, capabilities: Capabilities) -> Footing:
    """What a run of *target* stands on now; reads files only, sends nothing."""
    plain = _unmanaged(capabilities)
    store = PlanStore(config.data_dir)
    try:
        plan = store.load(target)
    except ValueError as exc:
        return Footing(plain, problem=f"its isolation plan can't be read: {exc}")
    if plan is None:
        return Footing(plain)
    version = store.version(target)
    conf = config.targets.get(target)
    folder = conf.code.path if conf is not None and conf.code is not None else None
    changed = stale(plan, folder) if folder is not None else []
    if plan.status == "proven" and not changed:
        return Footing("proven", plan, version)
    found = Footing(plain, plan, version, changed)
    if found.to_rehearse:
        found.problem = "its isolation plan changed under it and wasn't rehearsed again"
    else:
        found.problem = f"its isolation plan is {plan.status}, not proven"
    return found


def refusal(config: Config, target: str, found: Footing) -> str | None:
    """Why a run of *target* is refused on *found*, with what fixes it; ``None`` when it isn't.
    A plan to rehearse again isn't refused here: the rehearsal decides, as the run starts."""
    if not config.isolation.require_proven or found.managed or found.to_rehearse:
        return None
    why = found.problem or "it has no isolation plan"
    return (
        f"{target} can't run: {why}, and isolation.require_proven is on. "
        f"Run `{FIX.format(target=target)}` to find and prove one."
    )


def covered(plan: Plan) -> frozenset[str]:
    """What a try on a copy this proven *plan* puts back starts clean of. Its rehearsal sent a
    message to be remembered, and found it nowhere once the stores were put back: so the
    conversation, and the memory too unless a store the plan can't reset (hosted, or kept by
    person only) could hold it. Files, when a planned store is a volume or a folder."""
    reset = [s for s in plan.stores if s.strategy not in _NOT_RESET and s.kind != "hosted"]
    if not reset:
        return frozenset()
    found = {"conversation"}
    if len(reset) == len(plan.stores):
        found.add("memory")
    if any(s.kind in _FILE_KINDS for s in reset):
        found.add("files")
    return frozenset(found) & frozenset(CLEAN_KINDS)


def capabilities_on(capabilities: Capabilities, found: Footing) -> Capabilities:
    """*capabilities* as a run on *found* plays them: managed, when it plays on copies."""
    if not found.managed or found.plan is None:
        return capabilities
    note = (
        f"Each try plays on evalution's own copy of the app, put back by a proven plan "
        f"(version {found.version}) and checked before it starts."
    )
    return capabilities.managed(covered(found.plan), note)


async def rehearse_again(config: Config, target: str, found: Footing) -> Footing:
    """Rehearse *found*'s plan again on slot 0's copy and save it, proven (its evidence's hashes
    taken afresh) or failed; what a run then stands on. Sends one probe message."""
    plan = found.plan
    assert plan is not None
    try:
        plan = await rehearse_plan(config, target, plan, slot=0)
    except (LookupError, LaunchError) as exc:
        plan.status = "failed"
        plan.report = f"{plan.report}\n\nRehearsing again before a run failed: {exc}"
    if plan.status == "proven":
        with contextlib.suppress(LookupError):  # a target with no code folder cites no file
            folder = code_folder(config, target)
            plan.evidence_hashes = hashes(folder, [e for s in plan.stores for e in s.evidence])
        plan.stale_files = []
    version = PlanStore(config.data_dir).save(plan)
    if plan.status == "proven":
        return Footing("proven", plan, version)
    return Footing(
        found.how, plan, version, problem="its isolation plan failed its rehearsal again"
    )


@dataclass
class Slot:
    """One copy of the app, the drivers on it, and its starting state."""

    index: int
    copy: Copy
    work: Path
    drivers: list[Driver] = field(default_factory=list)
    #: Each driver and the folder its starting state is kept in.
    kept: list[tuple[Driver, Path]] = field(default_factory=list)
    #: Each store's hash in the starting state.
    before: dict[str, str] = field(default_factory=dict)
    #: Containers left running while the copy is stopped: a driver works in them.
    keep_up: list[str] = field(default_factory=list)
    adapter: Target | None = None
    #: Stores the plan names that no driver resets, in words.
    notes: list[str] = field(default_factory=list)


class CopyLost(TargetGone):
    """A copy of the app didn't come back after being put back: the run can't go on."""


class Intake:
    """Where a run's copies send their traces: a trace intake of the run's own, on this computer
    only, on a free port, into the same store as evalution's. The copies' containers reach it as
    ``host.docker.internal``, a name evalution's server rightly doesn't answer under; this one
    takes traces and nothing else, for as long as the copies run (like ``evalution run``'s own
    intake when no server is up)."""

    def __init__(self, server: uvicorn.Server, task: asyncio.Task[None], port: int) -> None:
        self.server, self.task, self.port = server, task, port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    async def start(cls, spans: SpanStore) -> Intake:
        app = FastAPI()

        @app.post("/v1/traces")
        async def post_traces(request: Request) -> Response:
            return await otlp.receive(request, spans)

        port = free_port()
        settings = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(settings)
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        task = asyncio.create_task(server.serve())
        while not server.started and not task.done():  # noqa: ASYNC110 - uvicorn only has the flag
            await asyncio.sleep(0.02)
        if task.done():
            raise LaunchError(f"the copies' trace intake didn't start on port {port}")
        return cls(server, task, port)

    async def close(self) -> None:
        self.server.should_exit = True
        with contextlib.suppress(Exception):
            await self.task


def trace_env(config: Config, target: str, url: str) -> dict[str, str]:
    """The settings that send *target*'s traces to evalution's intake at *url*: the adapter's
    own lines when it has them (:attr:`AdapterInfo.trace_setup`), else the standard OpenTelemetry
    ones naming the target. Read from ``KEY=VALUE`` lines; anything else in them is skipped."""
    info = infos().get(config.targets[target].adapter)
    told = None
    if info is not None:
        try:
            told = info.trace_setup(url)
        except TypeError:  # an adapter written for ``(url, builtin)``: evalution's own intake
            told = info.trace_setup(url, True)  # type: ignore[call-arg]
    if not told or not told.get("lines"):
        told = setup.advice({"otel": True}, url, f"{TARGET_PREFIX}{target}")
    out: dict[str, str] = {}
    for line in str(told.get("lines") or "").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            out[key] = value
    return out


class Copies:
    """The copies a run plays on, one per slot (see the module's docstring)."""

    def __init__(
        self, config: Config, target: str, found: Footing, spans: SpanStore | None = None
    ) -> None:
        assert found.plan is not None
        self.config = config
        #: Where the copies' traces are kept: the run reads a try's spans from it.
        self.spans = spans
        self.intake: Intake | None = None
        #: The settings that send each copy's traces to :attr:`intake`.
        self.env: dict[str, str] = {}
        self.target = target
        self.plan = found.plan
        self.version = found.version
        self.spec = plan_spec(config, target, found.plan)
        self.slots: list[Slot] = []

    async def start(self, slots: int) -> None:
        """Start *slots* copies one after another, keep each one's starting state, and point an
        adapter at each. Whatever was started stays in :attr:`slots`, for :meth:`close`.

        :raises LaunchError: A copy didn't start.
        """
        if self.spans is not None and self.intake is None:
            self.intake = await Intake.start(self.spans)
            self.env = trace_env(self.config, self.target, self.intake.url)
        for index in range(slots):
            work = await asyncio.to_thread(work_folder, self.config, self.target)
            copy = copy_of(self.spec, self.target, index, work, env=self.env)
            slot = Slot(index, copy, work)
            self.slots.append(slot)
            await slot.copy.start()
            roots = roots_of(slot.copy, self.spec)
            slot.drivers = await drivers_for(self.plan, slot.copy, roots=roots, notes=slot.notes)
            slot.keep_up = [d.container for d in slot.drivers if isinstance(d, PostgresStore)]
            await self._pause(slot)
            slot.before = await _digests(slot.drivers)
            for i, driver in enumerate(slot.drivers):
                await asyncio.to_thread(driver.keep, work / "kept" / str(i))
                slot.kept.append((driver, work / "kept" / str(i)))
            await self._resume(slot)
            slot.adapter = adapter_on(self.config, self.target, slot.copy.url)

    async def _pause(self, slot: Slot) -> None:
        if isinstance(slot.copy, ComposeCopy):
            await slot.copy.pause(slot.keep_up)
        else:
            await slot.copy.stop()

    async def _resume(self, slot: Slot) -> None:
        if isinstance(slot.copy, ComposeCopy):
            await slot.copy.resume()
        else:
            await slot.copy.start()

    async def restore(self, index: int) -> dict[str, Any]:
        """Put slot *index*'s copy back to its starting state and check it: the try's
        ``isolation``. Its ``problem`` says why the reset didn't hold, when it didn't.

        :raises CopyLost: The copy didn't come back.
        """
        slot = self.slots[index]
        started = time.monotonic()
        problem = None
        try:
            await self._pause(slot)
            for driver, kept in slot.kept:
                await asyncio.to_thread(driver.put_back, kept)
        except (Refused, DockerError, OSError) as exc:
            problem = f"{DIDNT_HOLD}: {type(exc).__name__}: {exc}"
        except LaunchError as exc:
            raise CopyLost(f"evalution's copy of the app didn't stop: {exc}") from exc
        try:
            await self._resume(slot)
        except LaunchError as exc:
            raise CopyLost(f"evalution's copy of the app didn't come back: {exc}") from exc
        seconds = round(time.monotonic() - started, 1)
        try:
            now = await _digests(slot.drivers)
        except (DockerError, OSError) as exc:
            now = {}
            problem = problem or f"{DIDNT_HOLD}: the stores couldn't be read ({exc})"
        same = [d for d in slot.drivers if now.get(d.name) == slot.before[d.name]]
        checked = [store_id(d) for d in same]
        differ = [store_id(d) for d in slot.drivers if d not in same]
        if differ and problem is None:
            problem = f"{DIDNT_HOLD}: {', '.join(differ)}"
        return isolation("proven", self.version, seconds, checked, problem)

    async def close(self) -> list[str]:
        """Put every store back, drop the databases' kept copies and take every copy down, each
        step tried whatever the one before did. What was left behind, in words."""
        left: list[str] = []
        for slot in self.slots:
            try:
                if slot.kept:
                    await self._pause(slot)
                    for driver, kept in slot.kept:
                        await asyncio.to_thread(driver.put_back, kept)
            except Exception as exc:  # the copy goes anyway: it is evalution's own
                _logger.warning("evals: %s not put back: %s", self.target, exc)
            for driver, kept in slot.kept:
                drop = getattr(driver, "drop_golden", None)
                if drop is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(drop, kept)
            try:
                left += await take_down(slot.copy)
            except (LaunchError, DockerError, OSError) as exc:
                left.append(f"{getattr(slot.copy, 'project', slot.copy)}: {exc}")
            await asyncio.to_thread(shutil.rmtree, slot.work / "kept", True)
        if self.intake is not None:
            await self.intake.close()
            self.intake = None
        return left


async def _digests(drivers: Sequence[Driver]) -> dict[str, str]:
    return {d.name: await asyncio.to_thread(d.digest) for d in drivers}
