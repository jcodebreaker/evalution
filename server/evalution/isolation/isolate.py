"""Isolation end to end, on evalution's own copy of the app (ADR 0009, phase 4).

:func:`isolate` starts a copy of the app (:mod:`.launch`), points a copy of the target's adapter at
it, and sends one probe through it while the code is read (:mod:`.discover`), this computer is
watched (:mod:`.observe`) and the copy's containers are searched (:mod:`.containers`). The
findings become a draft plan (:func:`.plan.draft_from`); each of its stores gets a driver on the
copy (:func:`drivers_for`), and the plan is rehearsed there (:mod:`.rehearse`) with a second
probe. The plan is saved as ``proven`` or ``failed``, with the rehearsal's account, and the copy
is taken down.

:func:`rehearse_plan` rehearses a plan as it is (one the model revised, say) on a fresh copy.

Nothing here acts on the person's own app: every store reset is a driver's on the copy, every
container searched is named after the copy, and everything removed at the end is named after it
(``eval-*``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from evalution import adapters
from evalution.adapters.base import Target
from evalution.config import Config
from evalution.isolation import containers, discover, observe
from evalution.isolation.drivers import Driver, FileStore, PostgresStore, VolumeStore
from evalution.isolation.drivers.base import DockerError, Refused, docker
from evalution.isolation.launch import (
    ComposeCopy,
    LaunchError,
    ProcessCopy,
    _run,
    down,
    extras,
    guard,
)
from evalution.isolation.plan import Plan, PlanStore, draft_from
from evalution.isolation.rehearse import Rehearsal, rehearse, scan_files

#: Where a project's compose file usually is, first found first.
COMPOSE_FILES = (
    "deployment/compose.yaml",
    "deployment/compose.yml",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)
#: Seconds the app may take to answer one probe.
PROBE_SECONDS = 300
#: What a probe says: the canary, as something worth remembering.
PROBE = "Please remember this for later: my code word is {canary}."

Ask = Callable[[str], Awaitable[bool]]
Copy = ComposeCopy | ProcessCopy


class Declined(RuntimeError):
    """The person said no before the probe was sent; the copy was taken down."""


# Inputs ----------------------------------------------------------------------------------------


def launch_spec(folder: Path, options: dict[str, Any]) -> dict[str, Any]:
    """How to start a copy of the app in *folder*: the target's ``launch`` block when it has
    one, else its compose file (and ``.env`` beside the project) when there is one.

    ``{"kind": "compose", "files": [...], "env_file": path | None, "app_url": ..., "health": ...}``
    or ``{"kind": "process", "command": [...], "cwd": ..., "env": {...}, "health": ...,
    "roots": [...]}``. Paths only: no value from an env file is ever read into it.

    :raises LookupError: Nothing says how to start it.
    """
    given = options.get("launch")
    url = str(options.get("url") or "") or None
    if isinstance(given, dict) and given.get("kind") in ("compose", "process"):
        return {"app_url": url, **given}
    for name in COMPOSE_FILES:
        if (folder / name).is_file():
            env = folder / ".env"
            return {
                "kind": "compose",
                "files": [str(folder / name)],
                "env_file": str(env) if env.is_file() else None,
                "app_url": url,
                "health": None,
            }
    raise LookupError(
        f"no compose file in {folder}, and the target has no `launch` block saying how to start "
        "a copy of the app"
    )


def copy_of(
    spec: dict[str, Any],
    target: str,
    slot: int,
    folder: Path,
    *,
    env: dict[str, str] | None = None,
) -> Copy:
    """A copy started as *spec* says, with *env* (such as where to send its traces) set on it."""
    if spec.get("kind") == "compose":
        return ComposeCopy(
            [Path(f) for f in spec.get("files") or []],
            Path(spec["env_file"]) if spec.get("env_file") else None,
            target,
            slot,
            folder,
            app_url=spec.get("app_url"),
            health=spec.get("health"),
            env=env,
        )
    if spec.get("kind") == "process":
        return ProcessCopy(
            [str(a) for a in spec.get("command") or []],
            Path(spec.get("cwd") or "."),
            env={**{str(k): str(v) for k, v in (spec.get("env") or {}).items()}, **(env or {})},
            health=spec.get("health"),
            folder=folder,
        )
    raise LookupError(f"not a way to start a copy: {spec.get('kind')!r}")


def roots_of(copy: Copy, spec: dict[str, Any]) -> list[Path]:
    """Folders here that belong to the copy alone: a file store may be put back only in them.
    A compose copy has none (its bind mounts are the person's)."""
    if isinstance(copy, ComposeCopy):
        return []
    return [Path(r) for r in spec.get("roots") or []]


def adapter_on(config: Config, target: str, url: str) -> Target:
    """A new adapter of *target* pointed at *url*; the saved target is left as it is.

    :raises LookupError: No such target.
    """
    conf = config.targets.get(target)
    if conf is None:
        raise LookupError(f"no target {target}")
    adapters.add_folder(config.adapters_dir or config.data_dir / "adapters")
    return adapters.make(replace(conf, options={**conf.options, "url": url}))


def prober(adapter: Target, run: str) -> Callable[[str], Awaitable[None]]:
    """Sends one message carrying a canary through *adapter*, as the person *run* names."""

    async def probe(canary: str) -> None:
        session = await adapter.open(run)
        try:
            await adapter.send(
                session, PROBE.format(canary=canary), deadline=time.time() + PROBE_SECONDS
            )
        finally:
            await adapter.close(session)

    return probe


# The copy's containers -------------------------------------------------------------------------


def _owned(name: str, project: str) -> bool:
    return name.startswith((f"{project}-", f"{project}_"))


async def copy_containers(project: str) -> list[containers.Container]:
    """The copy's containers: its compose project's, and those it started (named after it).
    Never a container not named after the copy, so nothing of the person's is ever entered."""
    project = guard(project)
    label = f"label=com.docker.compose.project={project}"
    _, out, _ = await _run("docker", "ps", "-q", "--filter", label)
    ids = out.split()
    ids += [e.name for e in await extras(project) if _owned(e.name, project)]
    if not ids:
        return []
    _, out, _ = await _run("docker", "inspect", *ids)
    found = []
    for item in json.loads(out or "[]"):
        name = item.get("Name", "").lstrip("/")
        if not _owned(name, project) or not item.get("State", {}).get("Running"):
            continue
        env = dict(e.split("=", 1) for e in item["Config"].get("Env") or [] if "=" in e)
        mounts = {
            m["Destination"]: (f"volume:{m['Name']}" if m["Type"] == "volume" else m["Source"])
            for m in item.get("Mounts") or []
        }
        found.append(containers.Container(name, item["Config"]["Image"], mounts, env))
    return sorted(found, key=lambda c: c.name)


def _place(found: containers.Found) -> str:
    """Where the canary was, as the rehearsal names it: a host path, or ``container:where``."""
    return found.where if found.kind == "bind" else f"{found.container}:{found.where}"


async def search_copy(project: str, canary: str, roots: list[Path] = ()) -> list[str]:  # type: ignore[assignment]
    """Every place *canary* is in the copy: its containers, and *roots* here."""
    boxes = await copy_containers(project)
    found = [_place(f) for f in await containers.search(boxes, canary)]
    return sorted({*found, *await asyncio.to_thread(scan_files, list(roots), canary)})


async def clear_extras(project: str) -> list[str]:
    """Remove the containers and volumes the copy started outside its compose project: only those
    named after it (``<project>-...``), so nothing else can be. What was removed."""
    project = guard(project)
    named = [e.name for e in await extras(project) if _owned(e.name, project)]
    if named:
        await _run("docker", "rm", "-f", "-v", *named, limit=120)
    _, out, _ = await _run("docker", "volume", "ls", "-q", "--filter", f"name={project}")
    volumes = [v for v in out.split() if _owned(v, project)]
    if volumes:
        await _run("docker", "volume", "rm", *volumes, limit=120)
    return named + [f"volume:{v}" for v in volumes]


async def take_down(copy: Copy) -> list[str]:
    """Take the copy down, with what it started outside its project. What is left, in words:
    containers named after the copy that wouldn't go, and others that started while it ran (in
    no compose project), which aren't evalution's to remove."""
    if isinstance(copy, ProcessCopy):
        await copy.stop()
        return []
    project = copy.project
    seen = await down(copy.copy or project)
    await clear_extras(project)
    still = [e.name for e in await extras(project) if _owned(e.name, project)]
    others = [f"{e.name} ({e.why}; not removed)" for e in seen if not _owned(e.name, project)]
    return still + others


# Drivers ---------------------------------------------------------------------------------------


def _inspect_env(container: str) -> dict[str, str]:
    """*container*'s environment, kept in memory only."""
    out = docker("inspect", "--format", "{{json .Config.Env}}", container)
    return dict(e.split("=", 1) for e in json.loads(out or "[]") or [] if "=" in e)


def _on_copy(container: str, own: dict[str, str], project: str) -> str | None:
    """The copy's container that stands for *container* (named in a plan drafted anywhere):
    itself when it is the copy's, else the copy's container of the same service and number."""
    if container in own:
        return container
    best = None
    for name, service in own.items():
        number = name.rsplit("-", 1)[-1]
        fits = bool(service) and container.endswith(f"-{service}-{number}")
        if fits and (best is None or len(own[best]) < len(service)):
            best = name
    return best if best is None or best.startswith(f"{project}-") else None


async def drivers_for(
    plan: Plan,
    copy: Copy,
    *,
    roots: list[Path] = (),
    notes: list[str] | None = None,  # type: ignore[assignment]
) -> list[Driver]:
    """A driver on the copy for each of *plan*'s stores that one can reset.

    * ``container:db`` (a database found in a container): the copy's container of that service,
      the same database, the plan's left-out tables of it (``db.schema.table``) left out.
    * ``volume:NAME``: the copy's ``<project>_NAME``.
    * A path here: a file store, put back only inside *roots* (the copy's own folders).
    * In-process memory: nothing, a restart clears it. Identity, hosted, or with no strategy:
      nothing, and said in *notes*. So is anything the copy has no counterpart of: the rehearsal
      then names where the canary went.
    """
    notes = notes if notes is not None else []
    out: list[Driver] = []
    compose = isinstance(copy, ComposeCopy)
    own = await copy.services() if compose else {}
    project = copy.project if compose else ""
    volumes: set[str] = set()
    if compose:
        _, listed, _ = await _run("docker", "volume", "ls", "-q", "--filter", f"name={project}")
        volumes = {v for v in listed.split() if _owned(v, project)}
    for store in plan.stores:
        loc = store.locator
        made = len(out)
        try:
            if store.kind == "memory":
                notes.append(f"{store.id}: in the process's memory; a restart clears it")
            elif store.strategy in ("identity", "none") or store.kind == "hosted":
                notes.append(f"{store.id}: {store.strategy}, not reset ({loc})")
            elif loc.startswith("volume:") and compose:
                name = loc.removeprefix("volume:")
                volume = name if _owned(name, project) else f"{project}_{name}"
                if volume in volumes:
                    out.append(VolumeStore(volume))
                else:
                    notes.append(f"{store.id}: the copy has no volume {volume}")
            elif loc.startswith("/"):
                path = Path(loc)
                if any(path.is_relative_to(r) for r in roots):
                    out.append(FileStore(path, allowed_roots=roots))
                else:
                    notes.append(f"{store.id}: {loc} isn't in a folder of the copy's own")
            elif compose and re.match(r"^[\w.-]+:[\w$-]+$", loc):
                container, db = loc.split(":", 1)
                mine = _on_copy(container, own, project)
                if mine is None:
                    notes.append(f"{store.id}: the copy has no container like {container}")
                    continue
                env = await asyncio.to_thread(_inspect_env, mine)
                left_out = [
                    e.removeprefix(f"{db}.") for e in plan.exclude if e.startswith(f"{db}.")
                ]
                user = env.get("POSTGRES_USER") or "postgres"
                out.append(PostgresStore(mine, db, user, exclude=left_out))
            else:
                notes.append(f"{store.id}: no driver for a {store.kind} at {loc}")
        except (Refused, DockerError) as exc:
            notes.append(f"{store.id}: {exc}")
        finally:
            for driver in out[made:]:
                driver.store_id = store.id  # type: ignore[attr-defined]
    return out


def store_id(driver: Driver) -> str:
    """The plan's id for the store *driver* resets, else the driver's own name."""
    return str(getattr(driver, "store_id", None) or driver.name)


# Running the copy for a rehearsal ----------------------------------------------------------------


class ComposeRunner:
    """A compose copy as the rehearsal works it: paused with its volumes kept, the databases a
    driver works in left up."""

    def __init__(self, copy: ComposeCopy, probe: Callable[[str], Awaitable[None]], keep: list[str]):
        self.copy = copy
        self._probe = probe
        self.keep = keep

    async def start(self) -> None:
        await self.copy.resume()

    async def stop(self) -> None:
        await self.copy.pause(self.keep)

    async def probe(self, canary: str) -> None:
        await self._probe(canary)

    def watch_roots(self) -> list[Path]:
        return []

    async def search(self, canary: str) -> list[str]:
        return await search_copy(self.copy.project, canary)


class ProcessRunner:
    """A copy started from its command: stopped and started again whole."""

    def __init__(self, copy: ProcessCopy, probe: Callable[[str], Awaitable[None]], folder: Path):
        self.copy = copy
        self._probe = probe
        self.folder = folder
        self.roots: list[Path] = []

    async def start(self) -> None:
        if self.copy.proc is None:
            pid = await self.copy.start()
            opened = await asyncio.to_thread(observe.open_by, pid)
            self.roots = observe.roots_for(self.folder, opened[0], [])

    async def stop(self) -> None:
        await self.copy.stop()

    async def probe(self, canary: str) -> None:
        await self._probe(canary)

    def watch_roots(self) -> list[Path]:
        return list(self.roots)

    async def search(self, canary: str) -> list[str]:
        return await asyncio.to_thread(scan_files, self.roots, canary)


# The pipeline ----------------------------------------------------------------------------------


def work_folder(config: Config, target: str) -> Path:
    """An empty folder of evalution's own for one copy and its kept stores."""
    folder = config.data_dir / "isolation" / target / "work" / time.strftime("%Y%m%d-%H%M%S")
    folder = folder.with_name(f"{folder.name}-{secrets.token_hex(3)}")
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def code_folder(config: Config, target: str) -> Path:
    conf = config.targets.get(target)
    if conf is None:
        raise LookupError(f"no target {target}")
    if conf.code is None:
        raise LookupError(f"target {target} has no code folder: say where the app is")
    return conf.code.path


def summary(copy: Copy) -> str:
    """What the person is asked about before a probe goes to the copy."""
    if isinstance(copy, ProcessCopy):
        return f"A copy of the app runs at {copy.url} (pid {copy.pid})."
    started = copy.copy
    assert started is not None
    lines = [
        f"A copy of the app runs as {started.project} at {started.url}, "
        f"started in {started.start_seconds:.0f}s.",
        f"Containers: {', '.join(started.containers)}. Volumes: {', '.join(started.volumes)}.",
    ]
    if started.rewired:
        lines.append(f"Pointed at the copy: {', '.join(started.rewired)}.")
    if started.fixed:
        lines.append(f"Still pointing outside the copy: {', '.join(started.fixed)}.")
    lines += started.notes
    shared = [m.source for m in started.shared if not m.source.endswith(".sock")]
    if shared:
        lines.append(f"Shared with your app (not copied): {', '.join(shared)}.")
    return "\n".join(lines)


async def _rehearse_on(
    plan: Plan, copy: Copy, spec: dict[str, Any], probe: Callable[[str], Awaitable[None]],
    folder: Path, work: Path,
) -> tuple[Rehearsal, list[str]]:  # fmt: skip
    notes: list[str] = []
    drivers = await drivers_for(plan, copy, roots=roots_of(copy, spec), notes=notes)
    if isinstance(copy, ComposeCopy):
        keep = [d.container for d in drivers if isinstance(d, PostgresStore)]
        runner: ComposeRunner | ProcessRunner = ComposeRunner(copy, probe, keep)
    else:
        runner = ProcessRunner(copy, probe, folder)
        pid = copy.pid
        if pid is not None:
            opened = await asyncio.to_thread(observe.open_by, pid)
            runner.roots = observe.roots_for(folder, opened[0], [])
    result = await rehearse(drivers, runner, work / "kept", ignore=plan.exclude)
    return result, notes


def _account(plan: Plan, result: Rehearsal, notes: list[str], where: str, left: list[str]) -> str:
    lines = [f"Rehearsed on {where} in {result.seconds:.0f}s:", result.report()]
    if notes:
        lines.append("Not reset by a driver:")
        lines += [f"- {n}" for n in notes]
    if left:
        lines.append(f"Left after the copy was taken down: {', '.join(left)}.")
    return "\n".join(lines)


async def isolate(
    config: Config,
    target: str,
    folder: Path | None = None,
    launch: dict[str, Any] | None = None,
    *,
    slot: int = 0,
    yes: bool = False,
    ask: Ask | None = None,
    keep: bool = False,
    on_stage: Callable[[str], object] | None = None,
) -> Plan:
    """Find *target*'s stores on a copy of the app, draft a plan, rehearse it there, save it.

    :param folder: The app's code; by default the target's code folder.
    :param launch: How to start the copy (:func:`launch_spec`); by default found from *folder*.
    :param yes: Send the probes without asking; else *ask* is shown the copy first.
    :param keep: Leave the copy running afterwards (it is otherwise always taken down).
    :param on_stage: Told each stage as it begins: ``start``, ``find``, ``plan``, ``rehearse``.
    :raises Declined: The person said no; the copy is already taken down.
    """

    def stage(key: str) -> None:
        if on_stage is not None:
            on_stage(key)

    folder = (folder or code_folder(config, target)).resolve()
    spec = launch or launch_spec(folder, config.targets[target].options)
    work = work_folder(config, target)
    copy = copy_of(spec, target, slot, work)
    left: list[str] = []
    downed = False
    try:
        stage("start")
        if isinstance(copy, ComposeCopy):
            await copy.start()
            pid = None
        else:
            pid = await copy.start()
        if not yes and (ask is None or not await ask(summary(copy))):
            raise Declined("no probe was sent")
        adapter = adapter_on(config, target, copy.url)
        tag = secrets.token_hex(4)

        # The probe: the code read, this computer watched, the copy's containers searched.
        stage("find")
        stores = await asyncio.to_thread(discover.scan, folder)
        canary = f"eval-canary-{secrets.token_hex(6)}"
        send = prober(adapter, f"isolation-find-{tag}")
        seen = await observe.watch(folder, stores, lambda: send(canary), canary, pid=pid)
        boxes = await copy_containers(copy.project) if isinstance(copy, ComposeCopy) else []
        found = await containers.search(boxes, canary)
        stores = observe.reconcile(folder, stores, seen)
        stores = containers.reconcile(stores, boxes, found)
        stage("plan")
        plan = draft_from(folder, stores, seen, found, target=target)
        plan.launch = {**spec, "project": plan.launch.get("project")}
        PlanStore(config.data_dir).save_evidence(target, evidence(stores, found, seen))

        stage("rehearse")
        result, notes = await _rehearse_on(
            plan, copy, spec, prober(adapter, f"isolation-rehearse-{tag}"), folder, work
        )
        if not keep:
            left, downed = await take_down(copy), True
        where = copy.project if isinstance(copy, ComposeCopy) else copy.url
        plan.status = "proven" if result.ok else "failed"
        plan.leaks = list(result.leaks)
        plan.report = plan.report + "\n\n" + _account(plan, result, notes, where, left)
        PlanStore(config.data_dir).save(plan)
        return plan
    finally:
        if not keep and not downed:
            await _quietly(take_down(copy))


def plan_spec(config: Config, target: str, plan: Plan) -> dict[str, Any]:
    """How to start a copy for *plan*: its own ``launch`` when that says enough, else what
    :func:`launch_spec` finds in the target's code folder.

    :raises LookupError: No such target, or nothing says how to start a copy.
    """
    if plan.launch.get("files") or plan.launch.get("command"):
        return plan.launch
    return launch_spec(code_folder(config, target).resolve(), config.targets[target].options)


async def rehearse_plan(config: Config, target: str, plan: Plan, *, slot: int = 0) -> Plan:
    """Rehearse *plan* as it is on a fresh copy of the app and take the copy down. Sends one
    probe message. The plan comes back ``proven`` or ``failed``, with the rehearsal's account in
    ``report`` and where it leaked in ``leaks``; it is not saved (the caller decides).

    :raises LookupError: No such target, or nothing says how to start a copy.
    :raises LaunchError: The copy didn't start.
    """
    folder = code_folder(config, target).resolve()
    spec = plan_spec(config, target, plan)
    work = work_folder(config, target)
    copy = copy_of(spec, target, slot, work)
    left: list[str] = []
    downed = False
    try:
        await copy.start()
        adapter = adapter_on(config, target, copy.url)
        probe = prober(adapter, f"isolation-rehearse-{secrets.token_hex(4)}")
        result, notes = await _rehearse_on(plan, copy, spec, probe, folder, work)
        left, downed = await take_down(copy), True
        where = copy.project if isinstance(copy, ComposeCopy) else copy.url
        draft = plan.report.split("\n\nRehearsed on ", 1)[0]
        plan.status = "proven" if result.ok else "failed"
        plan.leaks = list(result.leaks)
        plan.report = draft + "\n\n" + _account(plan, result, notes, where, left)
        return plan
    finally:
        if not downed:
            await _quietly(take_down(copy))


async def _quietly(work: Awaitable[object]) -> None:
    """*work*, whose failure would hide the error already on its way out."""
    with contextlib.suppress(LaunchError, DockerError, OSError):
        await work


def evidence(
    stores: list[discover.Store], found: list[containers.Found], seen: observe.Observation
) -> dict[str, Any]:
    """What one probe found, to keep beside the plan: every candidate with what was seen of it,
    the finds inside containers, and the paths watched. Names and paths only, never a value."""
    return {
        "candidates": [
            {"kind": s.kind, "locator": s.locator, "evidence": s.evidence, "seen": s.seen}
            for s in stores
        ],
        "found": [{"container": f.container, "kind": f.kind, "where": f.where} for f in found],
        "observation": {
            "roots": seen.roots,
            "written": seen.written,
            "canary": seen.canary,
            "open_files": seen.open_files,
            "sockets": seen.sockets,
        },
    }


def plan_path(config: Config, target: str) -> Path:
    """Where *target*'s current plan is kept."""
    return PlanStore(config.data_dir).root / target / "plan.json"
