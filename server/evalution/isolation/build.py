"""Building a version of the app into evalution's own names, proving a recipe, and removing what
was built (ADR 0011, phase 3).

:func:`build_version` builds one version from a recipe (:mod:`.recipe`), a source folder (the
disk checkout, or a version evalution checked out) and a tag. It writes only in evalution's own
folder (the build override, the logs), names only images under ``evalution/`` (compose's resolved
names are checked before anything is built, and a build is refused otherwise), never pushes, and
refuses with under :data:`MIN_FREE` free on this computer's disk, saying what to remove. What it
prints is passed on as it goes, scrubbed of the env file's values. It records the seconds it took
and the disk it took.

:func:`prove` builds the disk version, starts a copy from those images (the recipe's variables in
the copy's env, which win over the env file), checks the copy plays them and answers, and takes it
down: the recipe comes back ``proven`` or ``failed``.

:func:`remove_version` removes evalution's own tags of one version (``docker image rm``) and, on
Colima, trims the Docker VM's disk so the space comes back to this computer (phase 0 found it
otherwise stays taken).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evalution.code import Code
from evalution.isolation.isolate import code_folder, copy_of, launch_spec, plan_spec, work_folder
from evalution.isolation.launch import (
    ComposeCopy,
    LaunchError,
    ProcessCopy,
    _docker,
    _healthy,
    _run,
    _tail,
    scrub,
    secrets_of,
)
from evalution.isolation.plan import PlanStore
from evalution.isolation.recipe import (
    OWN,
    BuildRecipe,
    RecipeStore,
    build_env,
    check_tag,
    image_names,
    override_text,
    repository,
)

if TYPE_CHECKING:
    from evalution.config import Config

#: A build is refused with less than this free on this computer's disk (decision 6).
MIN_FREE = 5 * 1024**3
#: Seconds one build may take before it is stopped: a cold build of relay-lite took 233.
BUILD_SECONDS = 60 * 60
#: Seconds a proven copy has to answer once it is up.
PROBE_SECONDS = 30
#: What a build costs before one was measured here, from phase 0's cold relay-lite build.
FIRST_SECONDS, FIRST_BYTES = 240, 20 * 1000**3

#: Told each line a build prints, as it prints it.
Said = Callable[[str], object]
#: Told each stage of proving as it begins: ``build``, ``copy``, ``probe``, ``stop``.
OnStage = Callable[[str], object]


class BuildRefused(RuntimeError):
    """Nothing was built: too little disk, or a name that isn't evalution's. Says what to do."""


class BuildFailed(RuntimeError):
    """The build ran and stopped: the message ends with its last lines."""


@dataclass
class Built:
    """One version built into evalution's names.

    :param images: ``service -> image`` built.
    :param ids: ``service -> image id``, as Docker says it after the build.
    :param disk_bytes: How much less is free on this computer's disk after the build (a Docker
        VM's disk grows on it); 0 when it didn't shrink.
    :param image_bytes: The built images' sizes, as Docker says them (layers they share counted
        once per image).
    """

    tag: str
    source: str
    images: dict[str, str] = field(default_factory=dict)
    ids: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    disk_bytes: int = 0
    image_bytes: int = 0
    at: float = field(default_factory=time.time)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Removed:
    """What :func:`remove_version` removed, and whether the Docker VM's disk was trimmed."""

    removed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    #: ``True`` trimmed, ``False`` tried and couldn't, ``None`` not a VM evalution can trim.
    trimmed: bool | None = None
    note: str = ""


# Disk


def free_bytes(folder: Path) -> int:
    """Free bytes on the disk *folder* is on (the nearest folder that exists)."""
    place = folder.resolve()
    while not place.exists() and place != place.parent:
        place = place.parent
    return shutil.disk_usage(place).free


def gigabytes(size: float) -> str:
    """*size* bytes in words: "19.5 GB"."""
    return f"{size / 1000**3:.1f} GB"


async def own_images() -> list[tuple[str, int]]:
    """``(image, bytes)`` of each image under ``evalution/`` here, the largest first."""
    code, out, _ = await _run(
        _docker(), "image", "ls", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}",
    )  # fmt: skip
    names = [line.split("\t")[0] for line in out.splitlines() if line.startswith(OWN)]
    if code != 0 or not names:
        return []
    sizes = await _sizes(names)
    return sorted(((n, sizes.get(n, (None, 0))[1]) for n in names), key=lambda p: -p[1])


async def guard_disk(data_dir: Path) -> int:
    """The free bytes on this computer's disk, when there are at least :data:`MIN_FREE`.

    :raises BuildRefused: Fewer; the message names evalution's own images to remove.
    """
    free = free_bytes(data_dir)
    if free >= MIN_FREE:
        return free
    kept: list[tuple[str, int]] = []
    with contextlib.suppress(LaunchError, OSError, ValueError):
        kept = await own_images()
    said = (
        f"Only {gigabytes(free)} is free on this computer's disk, and a build needs at least "
        f"{gigabytes(MIN_FREE)}. "
    )
    if kept:
        listed = ", ".join(f"{n} ({gigabytes(b)})" for n, b in kept[:5])
        said += f"Remove versions evalution built ({listed}) from the Test environment page"
        said += ", or free space elsewhere."
    else:
        said += "Evalution has built nothing to remove: free space elsewhere, then try again."
    raise BuildRefused(said)


# Building


def disk_tag(code: Code) -> str:
    """The tag of what's on disk now: ``disk-`` and a hash of its watched files' hashes, so the
    same files build to the same tag."""
    found = json.dumps(code.hashes(), sort_keys=True).encode()
    return "disk-" + hashlib.sha256(found).hexdigest()[:12]


def override_file(data_dir: Path, target: str, tag: str) -> Path:
    """Where a ``compose_override`` recipe's build override for *tag* is kept: in evalution's own
    folder. A copy started from that version passes it after the app's compose files."""
    return RecipeStore(data_dir).folder(target) / f"build-{check_tag(tag)}.override.json"


def _compose_args(
    recipe: BuildRecipe, source: Path, env_file: Path | None, override: Path | None
) -> list[str]:
    args = [_docker(), "compose"]
    for rel in recipe.files:
        args += ["-f", str(source / rel)]
    if override is not None:
        args += ["-f", str(override)]
    if env_file is not None:
        args += ["--env-file", str(env_file)]
    args += ["-p", f"eval-build-{repository(recipe.target).removeprefix(OWN)}"]
    for profile in recipe.profiles:
        args += ["--profile", profile]
    return args


async def stream(
    args: list[str],
    said: Said,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    secrets: list[str] | None = None,
    limit: float = BUILD_SECONDS,
) -> tuple[int, list[str]]:
    """Run *args* (never a shell), telling *said* each line as it comes, scrubbed of *secrets*;
    its exit code and its last lines. *env* is laid over this process's environment."""
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env={**os.environ, **env} if env else None,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    last: list[str] = []
    try:
        async with asyncio.timeout(limit):
            while raw := await process.stdout.readline():
                line = scrub(raw.decode("utf-8", errors="replace").rstrip(), secrets or [])
                if line.strip():
                    last = [*last[-29:], line]
                    said(line)
            return await process.wait(), last
    except TimeoutError:
        last.append(f"stopped: it took longer than {limit / 60:.0f} min")
        return -1, last
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await asyncio.shield(process.wait())


async def _resolved(
    args: list[str], env: dict[str, str], secrets: list[str]
) -> dict[str, dict[str, Any]]:
    code, out, err = await _run(*args, "config", "--format", "json", env=env,
                                       secrets=secrets)  # fmt: skip
    if code != 0:
        raise BuildRefused(f"compose couldn't read the app's files: {_tail(err)}")
    return json.loads(out).get("services") or {}


async def _sizes(names: list[str]) -> dict[str, tuple[str, int]]:
    """``image -> (id, bytes)`` of those on this computer."""
    out: dict[str, tuple[str, int]] = {}
    for name in names:
        code, found, _ = await _run(
            _docker(), "image", "inspect", "--format", "{{.Id}}\t{{.Size}}", name
        )
        image, _, size = found.strip().partition("\t")
        if code == 0 and image:
            out[name] = (image, int(size) if size.isdigit() else 0)
    return out


async def build_version(
    recipe: BuildRecipe,
    source: Path,
    tag: str,
    *,
    data_dir: Path,
    env_file: Path | None = None,
    said: Said = lambda _line: None,
) -> Built:
    """Build *recipe*'s version in *source* as *tag*, into evalution's names only.

    *source* is the folder that stands for the target's code folder in this version: the code
    folder itself for what's on disk, or the same place in a version evalution checked out.
    *env_file* is the person's (a version keeps reading theirs); its values are passed by path
    and scrubbed from every line said. A ``process`` recipe's setup runs in *source*, so only in
    a folder under *data_dir* (a version evalution checked out): never in the person's own.

    :raises BuildRefused: Under :data:`MIN_FREE` free; a name that isn't under ``evalution/``;
        a process version outside evalution's folder; compose can't read the files. Nothing built.
    :raises BuildFailed: The build stopped; its last lines are in the message.
    :raises ValueError: *tag* isn't an image tag.
    """
    check_tag(tag)
    source, own = await asyncio.to_thread(lambda: (source.resolve(), data_dir.resolve()))
    if recipe.kind == "process" and not source.is_relative_to(own):
        raise BuildRefused(
            f"{source} isn't a folder evalution checked out: a process app's setup runs only "
            "there, never in your own folder."
        )
    before = await guard_disk(data_dir)
    started = time.monotonic()
    done = Built(tag=tag, source=str(source))
    if recipe.kind == "process":
        await _setup(recipe, source, said)
    elif recipe.services:
        done.images = await _compose_build(recipe, source, tag, data_dir, env_file, said)
        found = await _sizes(list(done.images.values()))
        done.ids = {s: found[n][0] for s, n in done.images.items() if n in found}
        done.image_bytes = sum(found[n][1] for n in done.images.values() if n in found)
        if lacking := [n for n in done.images.values() if n not in found]:
            raise BuildFailed(f"the build ended without {', '.join(lacking)}")
    else:
        done.notes.append("The compose files build nothing: there was nothing to build.")
    done.seconds = round(time.monotonic() - started, 1)
    done.disk_bytes = max(0, before - free_bytes(data_dir))
    return done


async def _compose_build(
    recipe: BuildRecipe,
    source: Path,
    tag: str,
    data_dir: Path,
    env_file: Path | None,
    said: Said,
) -> dict[str, str]:
    names = image_names(recipe, tag)
    env = build_env(recipe, tag)
    folder = RecipeStore(data_dir).folder(recipe.target)
    folder.mkdir(parents=True, exist_ok=True)
    override = None
    if (text := override_text(recipe, tag)) is not None:
        override = override_file(data_dir, recipe.target, tag)
        override.write_text(text, encoding="utf-8")
    args = _compose_args(recipe, source, env_file, override)
    secrets = secrets_of(env_file)
    # The names compose will build, resolved as it resolves them: none may be the person's.
    services = await _resolved(args, env, secrets)
    for service in recipe.services:
        resolved = str((services.get(service) or {}).get("image") or "")
        if not resolved.startswith(OWN) or resolved != names[service]:
            raise BuildRefused(
                f"{service} would be built as {resolved or 'an image with no name'}, not "
                f"{names[service]}: evalution builds only into its own names, so nothing was built."
            )
    said(f"$ docker compose build {' '.join(recipe.services)}  ({', '.join(names.values())})")
    code, last = await stream(
        [*args, "build", *recipe.services], said, env=env, secrets=secrets, cwd=source
    )
    if code != 0:
        raise BuildFailed("The build stopped:\n" + "\n".join(last[-12:]))
    return names


async def _setup(recipe: BuildRecipe, source: Path, said: Said) -> None:
    for step in recipe.setup:
        cwd = (source / step.cwd).resolve()
        if not cwd.is_relative_to(source) or not cwd.is_dir():
            raise BuildRefused(f"{step.cwd} isn't a folder in {source}")
        tool = shutil.which(step.run[0]) if step.run else None
        if tool is None:
            raise BuildFailed(f"{step.run[0] if step.run else 'setup'} isn't installed here")
        said(f"$ {' '.join(step.run)}  (in {step.cwd})")
        code, last = await stream([tool, *step.run[1:]], said, cwd=cwd)
        if code != 0:
            raise BuildFailed(f"{' '.join(step.run)} stopped:\n" + "\n".join(last[-12:]))


# Removing


async def _colima() -> str | None:
    """The Colima profile Docker talks to, when it is Colima's VM; else ``None``."""
    if shutil.which("colima") is None:
        return None
    host = os.environ.get("DOCKER_HOST", "")
    context = ""
    with contextlib.suppress(LaunchError, OSError):
        _, context, _ = await _run(_docker(), "context", "show")
    for said in (context.strip(), host):
        if said.startswith("colima"):
            return said.removeprefix("colima").removeprefix("-") or "default"
        if ".colima/" in said:
            return said.split(".colima/", 1)[1].split("/", 1)[0] or "default"
    return None


async def trim() -> tuple[bool | None, str]:
    """Trim the Docker VM's disk so freed space comes back to this computer: on Colima,
    ``fstrim`` in its VM. ``(trimmed, said)``: ``None`` when this isn't a VM evalution trims."""
    profile = await _colima()
    if profile is None:
        return None, (
            "Docker's disk wasn't trimmed: it isn't Colima's here. If space doesn't come back, "
            "your Docker's own settings say how to reclaim it."
        )
    colima = shutil.which("colima") or "colima"
    args = [colima, "ssh", *(["-p", profile] if profile != "default" else []), "--"]
    code, _, err = await _run(*args, "sudo", "fstrim", "/var/lib/docker", limit=300)
    if code != 0:
        why = _tail(err, 2) or "no reason given"
        return False, f"Couldn't trim Colima's disk ({why})."
    return True, "Trimmed Colima's disk, so the space is free on this computer again."


async def remove_images(names: list[str]) -> Removed:
    """``docker image rm`` of *names*, every one evalution's, then trim the VM's disk.

    :raises ValueError: A name isn't under ``evalution/``: nothing is removed.
    """
    for name in names:
        if not name.startswith(OWN):
            raise ValueError(
                f"{name} isn't one of evalution's images: evalution removes only its own"
            )
    done = Removed()
    for name in names:
        code, _, err = await _run(_docker(), "image", "rm", name, limit=120)
        if code == 0:
            done.removed.append(name)
        elif "No such image" in err or "not found" in err.lower():
            done.missing.append(name)
        else:
            raise LaunchError(f"couldn't remove {name}: {_tail(err, 3)}")
    if done.removed:
        done.trimmed, done.note = await trim()
    return done


async def remove_version(recipe: BuildRecipe, tag: str, data_dir: Path | None = None) -> Removed:
    """Remove the images *recipe* built as *tag* (evalution's own tags only, never the person's),
    trim the VM's disk, and forget the build when *data_dir* keeps it."""
    done = await remove_images(list(image_names(recipe, tag).values()))
    if data_dir is not None:
        store = RecipeStore(data_dir)
        store.forget_built(recipe.target, tag)
        with contextlib.suppress(OSError):
            override_file(data_dir, recipe.target, tag).unlink(missing_ok=True)
    return done


# Proving


def copy_spec(config: Config, target: str) -> dict[str, Any]:
    """How a copy of *target* starts: its isolation plan's, else what its code folder says.

    :raises LookupError: No such target, no code folder, or nothing says how to start a copy.
    """
    plan = PlanStore(config.data_dir).load(target)
    if plan is not None:
        return plan_spec(config, target, plan)
    return launch_spec(code_folder(config, target).resolve(), config.targets[target].options)


async def prove(
    config: Config,
    target: str,
    recipe: BuildRecipe,
    *,
    said: Said = lambda _line: None,
    on_stage: OnStage = lambda _stage: None,
    slot: int = 0,
) -> BuildRecipe:
    """Build what's on disk with *recipe* into evalution's names, start a copy from those images,
    check it plays them and answers, and take it down. *recipe* comes back ``proven`` or
    ``failed``, with what happened in ``report`` and the build in ``last_build``; it isn't saved
    (the caller decides). A process recipe's setup isn't run in the person's folder: its copy
    starts from the folder as it is.

    :raises BuildRefused: Nothing was built (too little disk; a name not evalution's).
    :raises LookupError: No such target, or nothing says how to start a copy.
    """
    conf = config.targets.get(target)
    if conf is None or conf.code is None:
        raise LookupError(f"target {target} has no code folder: say where the app is")
    folder = code_folder(config, target).resolve()
    spec = copy_spec(config, target)
    tag = disk_tag(Code(conf.code))
    report = [recipe.report.split("\n\nProved on ", 1)[0].split("\n\nNot proven", 1)[0]]
    proved: list[str] = []
    on_stage("build")
    if recipe.kind == "process":
        built = Built(tag=tag, source=str(folder), notes=["Setup isn't run in your own folder."])
        proved.append("Setup not run: it runs only in versions evalution checks out.")
    else:
        try:
            built = await build_version(
                recipe, folder, tag, data_dir=config.data_dir,
                env_file=Path(spec["env_file"]) if spec.get("env_file") else None, said=said,
            )  # fmt: skip
        except BuildFailed as exc:
            recipe.status = "failed"
            recipe.report = "\n\n".join([*report, f"Not proven: {exc}"])
            return recipe
        RecipeStore(config.data_dir).record_built(target, built.as_dict())
        proved.append(
            f"Built {', '.join(built.images.values()) or 'nothing'} in {built.seconds:.0f}s, "
            f"taking {gigabytes(built.disk_bytes)} of this computer's disk."
        )
    recipe.last_build = built.as_dict()
    work = work_folder(config, target)
    env = build_env(recipe, tag)
    if recipe.kind == "compose_override" and recipe.services:
        override = override_file(config.data_dir, target, tag)
        spec = {**spec, "files": [*spec.get("files", []), str(override)]}
    copy = copy_of(spec, target, slot, work, env=env)
    try:
        on_stage("copy")
        await copy.start()
        on_stage("probe")
        why = await _check_copy(copy, built)
    except LaunchError as exc:
        why = f"the copy didn't start from them: {exc}"
    finally:
        on_stage("stop")
        with contextlib.suppress(LaunchError, OSError):
            await copy.stop()
    if why:
        recipe.status = "failed"
        recipe.report = "\n\n".join([*report, "Not proven: " + " ".join([*proved, why])])
        return recipe
    recipe.status = "proven"
    proved.append(f"A copy started from them, answered at {copy_url(copy)}, and was taken down.")
    recipe.report = "\n\n".join([*report, "Proved on " + time.strftime("%Y-%m-%d %H:%M") + ": "
                                 + " ".join(proved)])  # fmt: skip
    return recipe


def copy_url(copy: ComposeCopy | ProcessCopy) -> str:
    with contextlib.suppress(LaunchError):
        return copy.url
    return "its address"


async def _check_copy(copy: ComposeCopy | ProcessCopy, built: Built) -> str | None:
    """Why the started copy doesn't prove the build, or ``None`` when it does: it plays the
    built images (by id) and answers at its address."""
    if isinstance(copy, ComposeCopy) and built.ids:
        playing = {i.service: i for i in await copy.images()}
        for service, image in built.ids.items():
            found = playing.get(service)
            if found is None:
                continue  # built only to be started by the app itself (relay's host)
            if not found.image.startswith(OWN) or found.id != image:
                return f"{service} played {found.image}, not the image just built"
    url = copy_url(copy)
    answered = url.startswith("http") and await _healthy(url, PROBE_SECONDS, any_answer=True)
    if not answered:
        return f"the copy didn't answer at {url}"
    return None
