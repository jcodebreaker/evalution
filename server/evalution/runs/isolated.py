"""Tries played on evalution's own copies of the app, each put back and checked first (ADR 0009).

A target with a proven isolation plan, whose evidence hasn't changed since, plays on copies of the
app: one per slot, ``eval-<target>-<slot>``, started once per run (:class:`Copies`). Each copy's
stores, as the plan names them, are kept once as the **starting state**, with their hashes. Before
every try the copy is stopped as far as its stores need (:mod:`evalution.isolation.hot`: not at
all for a database, the services mounting a volume, else the whole copy; the whole copy unless the
plan's rehearsal proved less), every store is put back, the copy is started again, and every store
is checked against the starting state. A slot's first try needs no put back when the copy, started
again after keeping, still hashes as kept and nothing opened a session on it since.

The check is a full hash after a slot's first put back and once more at the end of the run, and a
cheap one between (each store's shape, and a database made again since the try began), unless
``isolation.verify`` is ``every_try``. A store that differs, or can't be put back, leaves the try
unjudged: "reset didn't hold: <store>". When the end-of-run hash doesn't hold, the tries checked
only cheaply since the last full hash are marked so too (:attr:`Copies.late`). After the run,
however it ends, every store is put back. A compose copy then stays up, parked for the target's
next run or page look (``close(keep=True)``), which reuses it slot by slot while it runs the same
image ids under the same plan version; else, and when the target is removed or rehearsed again or
the server stops (:func:`drop`, :func:`drop_all`), its databases' kept copies are dropped and it is
taken down.

The target's test setup (:mod:`evalution.isolation.setup`) says how many copies play side by
side, whether a copy is put back before every try or only before each test's first, what each
starts with (sample data loaded into its Postgres stores before the starting state is kept), and
the test person each copy's adapter plays as (``persona``, when the adapter has it).

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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response

from evalution.adapters import infos
from evalution.adapters.base import Capabilities, Isolation, Target, TargetGone
from evalution.config import Config, ConfigError
from evalution.core.case import CLEAN_KINDS
from evalution.core.version import Checkout, Version, on_images, on_tree, unstarted
from evalution.isolation import hot, outbound
from evalution.isolation.drivers import Driver, PostgresStore
from evalution.isolation.drivers.base import DockerError, Refused
from evalution.isolation.hot import NoAnswer
from evalution.isolation.isolate import (
    ComposeCopy,
    Copy,
    adapter_on,
    code_folder,
    copy_of,
    off_copy,
    plan_spec,
    play_as,
    rehearse_plan,
    roots_of,
    store_id,
    stores_on,
    take_down,
    work_folder,
)
from evalution.isolation.launch import (
    OWNER_LABEL,
    RUN_LABEL,
    LaunchError,
    down_idle,
    free_port,
    owner_gone,
    owner_id,
    sweep,
)
from evalution.isolation.lock import Busy, TargetLock
from evalution.isolation.plan import Plan, PlanStore, hashes, not_put_back, stale
from evalution.isolation.setup import Setup, SetupError, person_name, seed_path, setup_for
from evalution.results.record import isolation, run_isolation
from evalution.runs.page.map import page_map_of
from evalution.runs.page.target import Missing, on_page, page_lacks
from evalution.suite.cases import CaseError
from evalution.traces import otlp, setup
from evalution.traces.builtin import TARGET_PREFIX, SpanStore

_logger = logging.getLogger(__name__)

#: What fixes a target with no proven plan.
FIX = "evalution isolate --target {target}"
#: The start of a try's ``problem`` when its copy wasn't put back as the plan says.
DIDNT_HOLD = "reset didn't hold"
#: Copies started at the same time, at most: each is a compose up and a wait for healthy.
STARTING_AT_ONCE = 3
#: Kinds of store that hold files a person sent.
_FILE_KINDS = ("volume", "dir")


class NotIsolated(CaseError):
    """The target has no proven isolation plan, and ``isolation.require_proven`` asks for one.
    A :class:`~evalution.suite.cases.CaseError`, so every caller of an estimate shows it as it
    shows a test that can't run. *missing*: the piece UI testing lacks, when that's why, for the
    page to offer what fixes it."""

    def __init__(self, message: str, missing: Missing | None = None) -> None:
        super().__init__(message)
        self.missing = missing


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

    def run_label(self, person: str | None = None) -> dict[str, Any]:
        """The run's label; *person*, the test person its copies played as."""
        return run_isolation(self.how, self.version if self.managed else None, person)

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
    A plan to rehearse again isn't refused here: the rehearsal decides, as the run starts.

    A target with a page map plays through its page only on evalution's own copy (ADR 0010), so
    without a proven plan it is refused whatever ``isolation.require_proven`` says. One whose
    adapter would still reach the app as it is from the copy (:func:`off_copy`) is refused on a
    plan: its tries would be labelled as played on the copy."""
    conf = config.targets.get(target)
    try:
        page = page_map_of(conf) if conf is not None else None
    except ConfigError as exc:
        return f"{target} can't run: {exc}"
    if page is not None and (missing := page_refusal(target, found)) is not None:
        if missing.command is None:
            fix = (
                f"Set up testing on the Test environment page, or run `{FIX.format(target=target)}`"
            )
            return f"{missing.said} {fix} to find and prove one."
        return f"{missing.said} Run: {missing.command}"
    if (found.managed or found.to_rehearse) and (off := off_copy(config, target)) is not None:
        return f"{target} can't play on evalution's own copy of the app: {off}"
    if not config.isolation.require_proven or found.managed or found.to_rehearse:
        return None
    why = found.problem or "it has no isolation plan"
    return (
        f"{target} can't run: {why}, and isolation.require_proven is on. "
        f"Run `{FIX.format(target=target)}` to find and prove one."
    )


def page_refusal(target: str, found: Footing) -> Missing | None:
    """What a target that plays through its page lacks for a run, in one sentence with what fixes
    it: the copy first (installing a browser wouldn't make the run possible without one), then
    Playwright and a browser. ``None`` when it lacks none of them."""
    if not found.managed and not found.to_rehearse:
        why = found.problem or "it has no proven isolation plan yet"
        said = (
            f"{target} can't play through its page: that only happens on evalution's own copy "
            f"of the app, and {why}."
        )
        return Missing("copy", said)
    lacks = page_lacks()
    return lacks[0] if lacks else None


def covered(plan: Plan) -> frozenset[str]:
    """What a try on a copy this proven *plan* puts back starts clean of. Its rehearsal sent a
    message to be remembered, and found it nowhere once the stores were put back: so the
    conversation, and the memory too unless a store the plan can't reset (hosted, or kept by
    person only, or a database of a kind no driver puts back) could hold it. Files, when a
    planned store is a volume or a folder."""
    reset = [s for s in plan.stores if not not_put_back(s)]
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
    await drop(config, target)  # the rehearsal starts slot 0's copy under the same name
    try:
        plan = await rehearse_plan(config, target, plan, slot=0)
    except Busy:
        raise  # another holds the copy: says nothing of the plan
    except (LookupError, LaunchError, SetupError) as exc:
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
    #: How far the copy stops while it is put back (:mod:`evalution.isolation.hot`): ``all``
    #: unless the plan's rehearsal proved less, and ``all`` again once the app didn't answer.
    how: str = "all"
    #: The services stopped when :attr:`how` is ``services``: those mounting a kept volume.
    services: list[str] = field(default_factory=list)
    #: Nothing has opened a session on the copy since its starting state was kept: the next
    #: try needs no put back.
    fresh: bool = False
    #: Times the copy was put back in this run.
    restores: int = 0
    #: Each store's cheap shape in the starting state, for the check between full hashes.
    shapes: dict[str, str | None] = field(default_factory=dict)
    #: The labels of tries checked only cheaply since the last full hash that held: the
    #: end-of-run hash marks them when it doesn't hold.
    since_hash: list[dict[str, Any]] = field(default_factory=list)


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
        self,
        config: Config,
        target: str,
        found: Footing,
        spans: SpanStore | None = None,
        setup: Setup | None = None,
        *,
        spec: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        run: str | None = None,
    ) -> None:
        """*spec* and *env*: how each copy starts when not as the plan says, a version of the code
        evalution checked out and built (:mod:`evalution.runs.versioned`). *run*: the run they
        play, labelled on each compose copy with this evalution as its owner, so a copy a stopped
        server left behind is found and removed (:func:`~evalution.isolation.launch.sweep`)."""
        self.run = run
        assert found.plan is not None
        self.config = config
        #: The test setup the copies play on: the person, the sample data, when they're put back.
        self.setup = setup or setup_for(config, target)
        #: The test person each copy's adapter took, by name; ``None`` when none took one.
        self.person: str | None = None
        #: Where the copies' traces are kept: the run reads a try's spans from it.
        self.spans = spans
        self.intake: Intake | None = None
        #: The settings that send each copy's traces to :attr:`intake`.
        self.env: dict[str, str] = {}
        self.target = target
        self.plan = found.plan
        self.version = found.version
        self.spec = spec or plan_spec(config, target, found.plan)
        #: Settings every copy starts with beside the traces': a version's image names.
        self.version_env = dict(env or {})
        self.slots: list[Slot] = []
        #: How often a copy's stores are hashed whole once put back (``isolation.verify``).
        self.verify = config.isolation.verify
        #: Labels of tries the end-of-run hash found not put back: they read as not judged.
        self.late: list[dict[str, Any]] = []
        #: Held from :meth:`start` to :meth:`close`: no other run or rehearsal of the target,
        #: here or from the command line, starts copies meanwhile.
        self.lock = TargetLock(config.data_dir, target)
        #: Copies parked by an earlier run beyond the slots this one plays: parked again at close.
        self.spare: list[Slot] = []

    @property
    def reset(self) -> str:
        """When a copy is put back: before every ``try``, or before each ``test``'s first."""
        return self.setup.reset

    async def start(self, slots: int) -> None:
        """Start *slots* copies side by side (at most :data:`STARTING_AT_ONCE` at a time), load
        the setup's sample data into each one's Postgres stores, keep each one's starting state,
        point an adapter at each and have it play as the setup's test person. Whatever was started
        stays in :attr:`slots`, in slot order, for :meth:`close`, even when another failed.

        :raises LaunchError: A copy didn't start, or the setup's sample data isn't there.
        :raises Busy: Another run or rehearsal of the target holds its copies.
        """
        try:
            seed = seed_path(self.config, self.target, self.setup)
        except SetupError as exc:
            raise LaunchError(f"the test setup's sample data can't be loaded: {exc}") from exc
        self.lock.acquire()
        # What a server that stopped left of its copies goes first: it would hold their names.
        data_dir = self.config.data_dir
        await sweep(lambda owner: owner_gone(data_dir, owner))
        # Settings that reach outside the copy start empty, unless the person allowed them.
        shut = await asyncio.to_thread(
            outbound.blocked_for, self.config.data_dir, self.target, self.spec
        )
        parked = await self._reattach(shut)
        reuse = {s.index: s for s in parked.slots} if parked is not None else {}
        if parked is not None:
            self.intake, self.env = parked.intake, dict(parked.env)
            self.spare = [s for i, s in sorted(reuse.items()) if i >= slots]
        if self.spans is not None and self.intake is None:
            self.intake = await Intake.start(self.spans)
            self.env = trace_env(self.config, self.target, self.intake.url)
        at_once = asyncio.Semaphore(STARTING_AT_ONCE)

        async def one(index: int) -> bool:
            async with at_once:
                if index in reuse and await self._reuse_slot(reuse[index]):
                    return await self._point(reuse[index])
                return await self._start_slot(index, seed, shut)

        # Each one to its end, so none is left starting behind a failed one.
        done = await asyncio.gather(*(one(i) for i in range(slots)), return_exceptions=True)
        self.slots.sort(key=lambda s: s.index)
        failed = [d for d in done if isinstance(d, BaseException)]
        if failed:
            raise failed[0]
        took = all(done)
        self.person = person_name(self.setup) if self.slots and took else None

    async def _start_slot(self, index: int, seed: Path | None, shut: dict[str, str]) -> bool:
        """Start slot *index*'s copy and keep its starting state (see :meth:`start`); whether its
        adapter took the setup's test person."""
        work = await asyncio.to_thread(work_folder, self.config, self.target)
        copy = copy_of(
            self.spec, self.target, index, work, env={**shut, **self.env, **self.version_env}
        )
        if isinstance(copy, ComposeCopy) and self.run is not None:
            copy.labels = {OWNER_LABEL: owner_id(self.config.data_dir), RUN_LABEL: self.run}
        slot = Slot(index, copy, work)
        self.slots.append(slot)
        if isinstance(copy, ComposeCopy):
            # Another process's parked copy (this one's were reused or taken down): idle, as
            # the lock held says, so it goes.
            await down_idle(copy.project, self.config.data_dir)
        await slot.copy.start()
        roots = roots_of(slot.copy, self.spec)
        slot.drivers = await stores_on(
            self.plan, slot.copy, roots=roots, notes=slot.notes, seed=seed
        )
        slot.keep_up = [d.container for d in slot.drivers if isinstance(d, PostgresStore)]
        slot.how = hot.level(self.plan, slot.drivers, slot.copy)
        if slot.how == "services":
            slot.services = await hot.services_for(slot.copy, slot.drivers)
        await self._pause(slot, "all")
        slot.before = await _digests(slot.drivers)
        slot.shapes = await _shapes(slot.drivers)
        for i, driver in enumerate(slot.drivers):
            await asyncio.to_thread(driver.keep, work / "kept" / str(i))
            slot.kept.append((driver, work / "kept" / str(i)))
        await self._resume(slot, "all")
        # Started again, the copy is as kept unless the app wrote as it started: only then does
        # its first try play without a put back.
        try:
            slot.fresh = await _digests(slot.drivers) == slot.before
        except (DockerError, OSError):
            slot.fresh = False
        return await self._point(slot)

    async def _point(self, slot: Slot) -> bool:
        """Point an adapter at *slot*'s copy, playing as the setup's test person; whether it took
        the person."""
        index = slot.index
        # Through the app's page too, when the target has a page map (ADR 0010).
        slot.adapter = on_page(
            self.config.targets[self.target],
            adapter_on(self.config, self.target, slot.copy.url),
            slot.copy.url,
            index,
        )
        took = await play_as(slot.adapter, self.setup.person)
        _watch_opens(slot)
        return took

    async def _reattach(self, shut: dict[str, str]) -> _Parked | None:
        """The copies an earlier run or look of the target left running, when this one can play
        on them: they run the image ids this one would start (the same code version, or the
        version asked for) under the same isolation plan version. Else they are taken down here
        and ``None`` comes back, for new ones to start."""
        parked = _PARKED.pop(_key(self.config, self.target), None)
        if parked is None:
            return None
        # The plan version is checked too: a changed plan names other stores to put back, so a
        # copy kept under the old one has the wrong starting state kept.
        same = parked.version == self.version and (parked.intake is not None or self.spans is None)
        if same:
            probe = copy_of(
                self.spec, self.target, 0, parked.slots[0].work, env={**shut, **self.version_env}
            )
            try:
                want = await image_ids(probe)
                same = bool(want) and want == await image_ids(parked.slots[0].copy)
            except (LaunchError, DockerError, OSError, ValueError) as exc:
                _logger.warning("evals: %s: images not compared: %s", self.target, exc)
                same = False
        if same:
            return parked
        left = await _down_parked(parked)
        if left:
            _logger.warning("evals: %s left behind: %s", self.target, ", ".join(left))
        return None

    async def _reuse_slot(self, slot: Slot) -> bool:
        """Put a parked *slot*'s stores back, as :meth:`restore` does, and take it for this run;
        whether it could be. One that can't is taken down, for a new one to start in its place."""
        slot.restores, slot.since_hash = 0, []
        try:
            await self._pause(slot)
            for driver, kept in slot.kept:
                await asyncio.to_thread(driver.put_back, kept)
            try:
                await self._resume(slot)
            except NoAnswer as exc:
                await self._fall_back(slot, exc)
            slot.fresh = await _digests(slot.drivers) == slot.before
        except Exception as exc:  # a new copy starts in its place
            _logger.warning("evals: %s: parked copy not put back, so new: %s", self.target, exc)
            with contextlib.suppress(Exception):
                await _down(slot)
            return False
        self.slots.append(slot)
        return True

    def unrestored(self) -> dict[str, Any]:
        """A try's ``isolation`` when its copy isn't put back before it: a test's later try, when
        the setup puts the copy back before each test only."""
        return isolation("proven", self.version, reset=self.reset)

    async def _pause(self, slot: Slot, how: str | None = None) -> None:
        """Stop *slot*'s copy as far as *how* says, by default as far as its stores need."""
        how = how or slot.how
        await hot.stop(slot.copy, how, keep=slot.keep_up, services=slot.services)

    async def _resume(self, slot: Slot, how: str | None = None) -> None:
        """Start what :meth:`_pause` stopped and wait until the copy answers.

        :raises NoAnswer: Put back with it (partly) up, the app didn't answer.
        """
        await hot.start(slot.copy, how or slot.how)

    async def restore(self, index: int) -> dict[str, Any]:
        """Put slot *index*'s copy back to its starting state and check it: the try's
        ``isolation``. Its ``problem`` says why the reset didn't hold, when it didn't; ``check``
        which check ran: ``kept`` (nothing touched the copy since its starting state was kept, so
        it isn't put back), ``full`` (every store hashed) or ``quick`` (each store's shape, and a
        database made again). ``note`` says when the app didn't answer after a put back with it
        up, and was stopped and started, as it is for the rest of the run.

        The copy stops only as far as its stores need (:mod:`evalution.isolation.hot`), when the
        plan's rehearsal proved that; else wholly, as it always did.

        :raises CopyLost: The copy didn't come back.
        """
        slot = self.slots[index]
        if slot.fresh:
            # Its first try: nothing touched the copy since it was kept a moment before.
            slot.fresh = False
            ids = [store_id(d) for d in slot.drivers]
            label = isolation("proven", self.version, None, ids, None, reset=self.reset)
            return {**label, "check": "kept"}
        started = time.monotonic()
        problem = note = None
        full = self.verify == "every_try" or slot.restores == 0
        before: dict[str, str | None] = {}
        if not full:
            try:
                before = await _generations(slot.drivers)
            except (DockerError, OSError):
                full = True  # what was there can't be told: the full hash says
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
        except NoAnswer as exc:
            note = await self._fall_back(slot, exc)
        except LaunchError as exc:
            raise CopyLost(f"evalution's copy of the app didn't come back: {exc}") from exc
        seconds = round(time.monotonic() - started, 1)
        slot.restores += 1
        try:
            if full:
                now = await _digests(slot.drivers)
                same = [d for d in slot.drivers if now.get(d.name) == slot.before[d.name]]
            else:
                same = await self._quick(slot, before)
        except (DockerError, OSError) as exc:
            same = []
            problem = problem or f"{DIDNT_HOLD}: the stores couldn't be read ({exc})"
        checked = [store_id(d) for d in same]
        differ = [store_id(d) for d in slot.drivers if d not in same]
        if differ and problem is None:
            problem = f"{DIDNT_HOLD}: {', '.join(differ)}"
        label = {
            **isolation("proven", self.version, seconds, checked, problem, reset=self.reset),
            "check": "full" if full else "quick",
        }
        if note:
            label["note"] = note
        if not full:
            slot.since_hash.append(label)
        elif problem is None:
            slot.since_hash = []
        return label

    async def _fall_back(self, slot: Slot, why: Exception) -> str:
        """The app didn't answer after a put back with it up: stop and start the whole copy, as
        it is for the rest of the run. What the try says of it.

        :raises CopyLost: The copy didn't come back that way either.
        """
        _logger.warning("evals: %s: %s; restarting it from now on", self.target, why)
        slot.how = "all"
        try:
            await self._pause(slot, "all")
            await self._resume(slot, "all")
        except LaunchError as exc:
            raise CopyLost(f"evalution's copy of the app didn't come back: {exc}") from exc
        return (
            "The app didn't answer after being put back with it running, so it was stopped and "
            "started; it is for the rest of this run."
        )

    async def _quick(self, slot: Slot, before: dict[str, str | None]) -> list[Driver]:
        """The drivers whose store passes the cheap check: its shape is the starting state's, and
        a database was made again since *before* (each one's generation then). A driver with no
        cheap check is hashed whole."""
        same: list[Driver] = []
        for driver in slot.drivers:
            shape = slot.shapes.get(driver.name)
            if shape is None or not hasattr(driver, "shape"):
                ok = await asyncio.to_thread(driver.digest) == slot.before[driver.name]
            else:
                ok = await asyncio.to_thread(driver.shape) == shape  # type: ignore[attr-defined]
                was = before.get(driver.name)
                if ok and was is not None:
                    now = await asyncio.to_thread(driver.generation)  # type: ignore[attr-defined]
                    ok = bool(now) and now != was
            if ok:
                same.append(driver)
        return same

    async def _end_check(self, slot: Slot) -> None:
        """Once the copy is put back at the end of the run, hash every store whole when tries were
        checked only cheaply since the last full hash. One that differs marks those tries' labels
        as not held (they then read as not judged) and adds them to :attr:`late`."""
        if not slot.since_hash:
            return
        try:
            now = await _digests(slot.drivers)
            differ = [store_id(d) for d in slot.drivers if now.get(d.name) != slot.before[d.name]]
        except (DockerError, OSError) as exc:
            differ = [f"the stores couldn't be read ({exc})"]
        if differ:
            said = (
                f"{DIDNT_HOLD}: the end-of-run check found {', '.join(differ)} not put back, "
                "and this try was checked only cheaply since the last full check"
            )
            for label in slot.since_hash:
                if label.get("problem") is None:
                    label["problem"] = said
                    self.late.append(label)
        slot.since_hash = []

    async def close(self, keep: bool = False) -> list[str]:
        """Put every store back, check it whole when tries since the last full hash were checked
        only cheaply (:meth:`_end_check`), drop the databases' kept copies and take every copy
        down, each step tried whatever the one before did, then let the target's lock go. What
        was left behind, in words.

        *keep*: leave each compose copy put back running instead, parked for the target's next
        run or look (:meth:`start` reuses it), with the trace intake its containers send to."""
        try:
            return await self._close(keep)
        finally:
            self.lock.release()

    async def _close(self, keep: bool = False) -> list[str]:
        left: list[str] = []
        keep = keep and all(isinstance(s.copy, ComposeCopy) for s in self.slots)
        parked: list[Slot] = []
        for slot in self.slots:
            if slot.adapter is not None:
                # What it holds goes with the copy: a client, or the page's browser.
                with contextlib.suppress(Exception):
                    await slot.adapter.dispose()
            try:
                if slot.kept:
                    await self._pause(slot, "all")
                    for driver, kept in slot.kept:
                        await asyncio.to_thread(driver.put_back, kept)
                    await self._end_check(slot)
                if keep:
                    # Put back, it stays up: what was stopped for the put back starts again.
                    await self._resume(slot, "all")
                    slot.adapter = None
                    parked.append(slot)
                    continue
            except Exception as exc:  # the copy goes anyway: it is evalution's own
                _logger.warning("evals: %s not put back: %s", self.target, exc)
                for label in slot.since_hash:  # nothing can vouch for the cheap checks now
                    if label.get("problem") is None:
                        label["problem"] = f"{DIDNT_HOLD}: the end-of-run put back failed ({exc})"
                        self.late.append(label)
                slot.since_hash = []
            left += await _down(slot)
        if keep:
            parked += self.spare
        else:
            for slot in self.spare:
                left += await _down(slot)
        self.spare = []
        if parked:
            parked.sort(key=lambda s: s.index)
            _PARKED[_key(self.config, self.target)] = _Parked(
                parked, self.intake, dict(self.env), self.version
            )
        elif self.intake is not None:
            await self.intake.close()
        self.intake = None
        return left


@dataclass
class _Parked:
    """Copies of a target left running between runs: their slots (stores put back, starting
    state kept), the trace intake they send to, its settings, and the plan version they were
    kept under."""

    slots: list[Slot]
    intake: Intake | None
    env: dict[str, str]
    version: int | None


#: The copies parked in this server process, by data folder and target.
_PARKED: dict[tuple[Path, str], _Parked] = {}


def _key(config: Config, target: str) -> tuple[Path, str]:
    return (Path(config.data_dir).resolve(), target)


async def image_ids(copy: Copy) -> frozenset[tuple[str, str]]:
    """Each service's image id a compose copy runs (started) or would start from (not)."""
    if not isinstance(copy, ComposeCopy):
        return frozenset()
    return frozenset((i.service, i.id) for i in await copy.images())


async def _down(slot: Slot) -> list[str]:
    """Drop *slot*'s databases' kept copies and take its copy down; what was left behind."""
    left: list[str] = []
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
    return left


async def _down_parked(parked: _Parked) -> list[str]:
    left: list[str] = []
    for slot in parked.slots:
        left += await _down(slot)
    if parked.intake is not None:
        await parked.intake.close()
    return left


async def drop(config: Config, target: str) -> list[str]:
    """Take down *target*'s parked copies, if any: it was removed, or its plan is rehearsed
    again. What was left behind."""
    parked = _PARKED.pop(_key(config, target), None)
    return await _down_parked(parked) if parked is not None else []


async def drop_all() -> list[str]:
    """Take down every parked copy: the server stops."""
    left: list[str] = []
    while _PARKED:
        _, parked = _PARKED.popitem()
        left += await _down_parked(parked)
    return left


async def _version(copy: Copy, checkout: Checkout, code: str | None) -> Version:
    """What *copy* plays, or would: a process copy runs the working tree, a compose copy its
    images (:func:`~evalution.isolation.launch.ComposeCopy.images`)."""
    if not isinstance(copy, ComposeCopy):
        return on_tree(checkout, code)
    try:
        return on_images(checkout, await copy.images())
    except (LaunchError, OSError, ValueError) as exc:
        _logger.warning("evals: images of %s not read: %s", copy.project, exc)
        found = on_images(checkout, [])
        return replace(
            found, verified=None, why_unverified=f"evalution couldn't read the copy's images: {exc}"
        )


async def played(copies: Copies, checkout: Checkout, code: str | None) -> Version:
    """What a run on *copies* plays: slot 0's copy, since every slot starts from the same."""
    return await _version(copies.slots[0].copy, checkout, code)


async def preview(config: Config, target: str, found: Footing, checkout: Checkout) -> Version:
    """What a run would play, before it starts, from what is on this computer now: nothing is
    started. On the person's own app, nothing evalution can vouch for."""
    if not found.managed:
        return unstarted(checkout)
    assert found.plan is not None
    folder = config.data_dir / "isolation" / target / "work"
    copy = copy_of(plan_spec(config, target, found.plan), target, 0, folder)
    return await _version(copy, checkout, None)


async def _digests(drivers: Sequence[Driver]) -> dict[str, str]:
    return {d.name: await asyncio.to_thread(d.digest) for d in drivers}


async def _shapes(drivers: Sequence[Driver]) -> dict[str, str | None]:
    """Each store's cheap shape; ``None`` for a driver with no cheap check."""
    out: dict[str, str | None] = {}
    for d in drivers:
        shape = getattr(d, "shape", None)
        out[d.name] = await asyncio.to_thread(shape) if shape is not None else None
    return out


async def _generations(drivers: Sequence[Driver]) -> dict[str, str | None]:
    """Each database's generation (its ``oid``) before it is put back; ``None`` for a store that
    has none."""
    out: dict[str, str | None] = {}
    for d in drivers:
        made = getattr(d, "generation", None)
        out[d.name] = await asyncio.to_thread(made) if made is not None else None
    return out


def _watch_opens(slot: Slot) -> None:
    """Mark *slot*'s copy as touched once anything opens a session on it through its adapter (a
    check of the connection before the run, say): its first try then needs a put back after all.
    Until then the copy is as kept, and its first try plays without one."""
    adapter = slot.adapter
    opened = getattr(adapter, "open", None)
    if adapter is None or opened is None:
        return

    async def open_(*args: Any, **kwargs: Any) -> Any:
        slot.fresh = False
        return await opened(*args, **kwargs)

    try:
        adapter.open = open_  # type: ignore[method-assign]
    except (AttributeError, TypeError):
        slot.fresh = False  # can't be watched: put it back before its first try, as before
