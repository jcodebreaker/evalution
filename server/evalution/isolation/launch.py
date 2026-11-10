"""Evalution's own copy of an app, started next to the person's and never in its place.

A compose app starts under evalution's own project name, ``eval-<target>-<slot>``, from the
person's compose files and an override file evalution writes in its own folder. The override moves
every published port to a free one on 127.0.0.1, and points settings that name the app's own
published ports on this computer (a URL its workers call it back on) at the copy's. Named volumes
are the copy's own, since compose names them after the project. Bind mounts can't be copied: they
are reported, as shared with the person's app. Images are never built or pulled: one missing is
an error that names it.

An app that isn't in Docker starts from its command, with settings that move its stores pointed
at evalution's copies and the port it listens on set.

Values from the project's env file never leave it: the file is passed to compose by path, only
setting names are reported, and what compose prints is scrubbed of them before it is shown.
Evalution removes only projects named ``eval-*``. Containers an app starts on its own while the
copy runs are found and reported, never removed.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import re
import secrets as tokens
import shutil
import signal
import socket
import sys
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit

import httpx

from evalution.adapters.base import NOT_APP_PORTS
from evalution.core import net
from evalution.core.version import Image

_logger = logging.getLogger(__name__)

#: Every project evalution starts is named like this, and it removes no other.
PREFIX = "eval-"
#: The labels on each container of a copy a run starts: the evalution that started it (an id
#: whose lock file, ``<data_dir>/isolation/owners/<id>.lock``, it holds while it runs) and the run.
OWNER_LABEL = "evalution.owner"
RUN_LABEL = "evalution.run"
_OWNER = re.compile(r"^[0-9a-f]{16}$")
#: This process's owner id and its held lock file, by data folder.
_OWNERS: dict[Path, tuple[str, TextIO]] = {}
_PROJECT = re.compile(r"^eval-[a-z0-9][a-z0-9_-]*$")
#: The first compose that reads the ``!override`` tag.
OVERRIDE_TAG = (2, 24, 4)
#: A URL to this computer, with no credentials in it, and its port.
_LOCAL_URL = re.compile(
    r"(?P<head>\b(?:https?|wss?)://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal):)"
    r"(?P<port>\d{2,5})\b"
)
#: Env values shorter than this aren't scrubbed: they'd blank out ordinary words and numbers.
_SCRUB_MIN = 6
#: What makes ``host.docker.internal`` this computer on Linux, where Docker doesn't name it.
HOST_GATEWAY = "host.docker.internal:host-gateway"


class LaunchError(RuntimeError):
    """The copy couldn't start or stop. The message never holds a value from the env file."""


@dataclass
class Mount:
    """A host path a service mounts, which the copy shares with the person's app."""

    service: str
    source: str
    target: str
    #: Whether the service may write in it (not mounted ``read_only``).
    writable: bool = True


@dataclass
class Extra:
    """A container that isn't in the copy's compose project but may belong to the copy."""

    name: str
    image: str
    created: str
    why: str


@dataclass
class Copy:
    """A started compose copy: what it is called, where it answers, and what it holds.

    :param ports: ``service -> {container port: host port}``, on 127.0.0.1.
    :param shared: Bind mounts, and volumes declared external: the person's own data.
    :param rewired: Settings naming the app's own published ports, pointed at the copy's.
    :param fixed: Settings naming a port on this computer the copy doesn't publish: they reach
        something outside the copy (the person's app, or a service beside it).
    :param notes: Anything else that keeps the copy from being wholly its own, in plain words.
    :param started: When ``up`` began, seconds since the epoch.
    """

    project: str
    ports: dict[str, dict[int, int]]
    url: str
    containers: list[str]
    volumes: list[str]
    shared: list[Mount]
    rewired: list[str]
    fixed: list[str]
    notes: list[str]
    started: float
    start_seconds: float
    override: Path
    log: Path | None = None


@dataclass
class Override:
    """The override file for one copy, and what writing it found."""

    text: str
    ports: dict[str, dict[int, int]]
    url: str
    shared: list[Mount] = field(default_factory=list)
    rewired: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


_THIS_COMPUTER = re.compile(r"(?<=//)(127\.0\.0\.1|localhost)(?=[:/]|$)")


def for_containers(value: str) -> str:
    """*value* with this computer's loopback address as a container reaches it."""
    return _THIS_COMPUTER.sub("host.docker.internal", value)


def project_name(target: str, slot: int) -> str:
    """``eval-<target>-<slot>``, in the letters compose allows in a project name."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", target.lower()).strip("-_") or "app"
    return f"{PREFIX}{slug}-{slot}"


def guard(project: str) -> str:
    """*project*, when it is one evalution may remove; else :class:`LaunchError`."""
    if not _PROJECT.match(project):
        raise LaunchError(f"{project!r} isn't evalution's: only projects named eval-* are removed")
    return project


def free_port() -> int:
    """A port nothing listens on at the moment, on 127.0.0.1."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def secrets_of(env_file: Path | None) -> list[str]:
    """Every value in *env_file* long enough to scrub, longest first. Kept in memory only."""
    if env_file is None:
        return []
    try:
        lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    values = set()
    for line in lines:
        _, sep, value = line.strip().removeprefix("export ").partition("=")
        value = value.strip().strip("\"'")
        if sep and len(value) >= _SCRUB_MIN:
            values.add(value)
    return sorted(values, key=len, reverse=True)


def scrub(text: str, secrets: list[str]) -> str:
    """*text* with each of *secrets* replaced by ``***``."""
    for value in secrets:
        text = text.replace(value, "***")
    return text


def _quote(value: object) -> str:
    """*value* as a YAML scalar (JSON's quoting is YAML's)."""
    return json.dumps(value)


def override_for(
    config: dict,
    project: str,
    *,
    app_url: str | None = None,
    pick: Callable[[], int] = free_port,
    tag: bool = True,
    env: dict[str, str] | None = None,
    linux: bool | None = None,
    labels: Mapping[str, str] | None = None,
) -> Override:
    """The override for *config* (``docker compose config --format json``) run as *project*.

    Each service's published ports move to free ones on 127.0.0.1, one per container port.
    Settings holding a URL to this computer at a port the app publishes are pointed at the new
    one; any other setting naming such a port is reported by name. A ``container_name`` gets the
    project's, and a named volume with a name of its own gets the project's, so neither collides
    with the person's stack. Bind mounts and external volumes are reported as shared.

    With *tag* the result is an override file using ``!override``; without, the whole resolved
    config with those changes (it then holds the env file's values: keep it private).

    :param app_url: The app's address as the target knows it; the copy's is the same address on
        the port that took its place. Without it, the first published port that isn't a database
        or a trace viewer's, else the first.
    :param env: Settings the copy must have, such as where to send its traces: each is set on
        every service whose environment already names it.
    :param linux: Whether Docker runs on Linux (by default, whether this computer does): there
        ``host.docker.internal``, where the copy sends its traces, is only a name once
        ``extra_hosts`` maps it to the host, so each service that doesn't already gets that line.
    :param labels: Set on every service's containers beside its own (:data:`OWNER_LABEL`,
        :data:`RUN_LABEL`), so a copy left behind can be told and removed.
    """
    linux = sys.platform.startswith("linux") if linux is None else linux
    services: dict[str, dict] = config.get("services") or {}
    moved: dict[int, int] = {}  # the person's published port -> the copy's
    ports: dict[str, dict[int, int]] = {}
    out = Override("", ports, "")
    for name, service in services.items():
        mapped: dict[int, int] = {}
        for port in service.get("ports") or []:
            target, published = int(port["target"]), str(port.get("published") or "")
            if target not in mapped:
                mapped[target] = pick()
            if published.isdigit():
                moved[int(published)] = mapped[target]
            elif published:
                out.notes.append(f"{name}: published range {published} not copied")
        if mapped:
            ports[name] = mapped

    def repoint(match: re.Match[str]) -> str:
        return f"{match['head']}{moved.get(int(match['port']), int(match['port']))}"

    changes: dict[str, dict[str, str]] = {}
    for name, service in services.items():
        for mount in service.get("volumes") or []:
            if mount.get("type") == "bind":
                out.shared.append(
                    Mount(
                        name,
                        mount.get("source", ""),
                        mount.get("target", ""),
                        writable=not mount.get("read_only", False),
                    )
                )
                if str(mount.get("source", "")).endswith("docker.sock"):
                    out.notes.append(
                        f"{name} mounts the Docker socket: it can start containers on this "
                        "computer, outside the copy"
                    )
        for key, value in (service.get("environment") or {}).items():
            if not isinstance(value, str):
                continue
            new = _LOCAL_URL.sub(repoint, value)
            if new != value:
                changes.setdefault(name, {})[key] = new
                out.rewired.append(f"{name}.{key}")
            elif _LOCAL_URL.search(value) or any(f":{p}" in value for p in moved):
                out.fixed.append(f"{name}.{key}")
        for key in service.get("environment") or {}:
            if env and key in env:
                changes.setdefault(name, {})[key] = env[key]
                if f"{name}.{key}" not in out.rewired:
                    out.rewired.append(f"{name}.{key}")
                if f"{name}.{key}" in out.fixed:
                    out.fixed.remove(f"{name}.{key}")
    out.url = _copy_url(app_url, ports, moved, out.notes)
    gateway = [n for n, s in services.items() if linux and not _maps_host(s.get("extra_hosts"))]

    if not tag:
        out.text = _resolved(config, project, ports, changes, gateway, labels) + "\n"
        return out
    lines = ["services:"]
    for name, service in services.items():
        body = []
        if name in ports:
            body.append("    ports: !override")
            body += [f"      - {_quote(f'127.0.0.1:{h}:{t}')}" for t, h in ports[name].items()]
        if service.get("container_name"):
            body.append(f"    container_name: {_quote(f'{project}-{name}-1')}")
        if name in changes:
            body.append("    environment:")
            body += [f"      {k}: {_quote(v)}" for k, v in changes[name].items()]
        if name in gateway:
            body += ["    extra_hosts:", f"      - {_quote(HOST_GATEWAY)}"]
        if labels:
            body += ["    labels:", *(f"      {k}: {_quote(v)}" for k, v in labels.items())]
        if body:
            lines += [f"  {name}:", *body]
    if len(lines) == 1:
        lines = ["services: {}"]
    volumes = []
    for key, volume in (config.get("volumes") or {}).items():
        own = f"{project}_{key}"
        if volume.get("external"):
            out.shared.append(Mount("*", f"volume:{volume.get('name') or key}", key))
        elif volume.get("name") and volume["name"] != own:
            volumes += [f"  {key}:", f"    name: {_quote(own)}"]
    if volumes:
        lines += ["volumes:", *volumes]
    out.text = "\n".join(lines) + "\n"
    return out


def _copy_url(
    app_url: str | None, ports: dict[str, dict[int, int]], moved: dict[int, int], notes: list[str]
) -> str:
    if app_url:
        parts = urlsplit(app_url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port in moved:
            return urlunsplit(parts._replace(netloc=f"127.0.0.1:{moved[port]}"))
        notes.append("the app's address isn't on a port the compose files publish")
    published = [(t, h) for mapped in ports.values() for t, h in mapped.items()]
    app = [h for t, h in published if t not in NOT_APP_PORTS] or [h for _, h in published]
    return f"http://127.0.0.1:{app[0]}" if app else ""


def _maps_host(extra: object) -> bool:
    """Whether a service's ``extra_hosts`` (a list of ``name:ip`` or ``name=ip``, or a mapping)
    already names ``host.docker.internal``."""
    if isinstance(extra, dict):
        return "host.docker.internal" in extra
    if isinstance(extra, list):
        return any(
            re.split(r"[:=]", str(e), maxsplit=1)[0] == "host.docker.internal" for e in extra
        )
    return False


def _resolved(
    config: dict,
    project: str,
    ports: dict[str, dict[int, int]],
    changes: dict[str, dict],
    gateway: Collection[str] = (),
    labels: Mapping[str, str] | None = None,
) -> str:
    """The whole config with the override's changes made in it, for a compose with no tag."""
    whole = json.loads(json.dumps(config))
    whole["name"] = project
    for name, service in whole.get("services", {}).items():
        if name in gateway:
            extra = service.get("extra_hosts")
            if isinstance(extra, dict):
                extra["host.docker.internal"] = "host-gateway"
            else:
                service["extra_hosts"] = [*(extra or []), HOST_GATEWAY]
        service["ports"] = [
            {"target": t, "published": str(h), "host_ip": "127.0.0.1", "protocol": "tcp"}
            for t, h in ports.get(name, {}).items()
        ]
        if service.get("container_name"):
            service["container_name"] = f"{project}-{name}-1"
        service.setdefault("environment", {}).update(changes.get(name, {}))
        if labels:
            service["labels"] = {**(service.get("labels") or {}), **labels}
    for key, volume in (whole.get("volumes") or {}).items():
        if volume.get("external"):
            continue
        volume["name"] = f"{project}_{key}"
    return json.dumps(whole, indent=2)


def supports_tag(version: str) -> bool:
    """Whether compose *version* (``docker compose version --short``) reads ``!override``."""
    numbers = tuple(int(n) for n in re.findall(r"\d+", version)[:3])
    return numbers >= OVERRIDE_TAG


# Docker


def _docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise LaunchError("Docker isn't installed on this computer")
    return docker


async def _run(
    *args: str,
    limit: float = 60,
    cwd: Path | None = None,
    secrets: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """``(code, out, err)`` of *args*; err scrubbed of *secrets*. Out is never scrubbed: the
    caller decides what of it to keep. *env* is laid over this process's environment."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env={**os.environ, **env} if env else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), limit)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", f"{Path(args[0]).name} {args[1]} took longer than {limit:.0f}s"
    text = err.decode(errors="replace")
    return proc.returncode or 0, out.decode(errors="replace"), scrub(text, secrets or [])


def _tail(text: str, lines: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


async def _names(*filters: str) -> list[str]:
    _, out, _ = await _run(_docker(), "ps", "-a", *filters, "--format", "{{.Names}}")
    return sorted(out.split())


async def _volumes(project: str) -> list[str]:
    label = f"label=com.docker.compose.project={project}"
    _, out, _ = await _run(_docker(), "volume", "ls", "-q", "--filter", label)
    return sorted(out.split())


async def copies() -> list[str]:
    """The ``eval-*`` compose projects on this computer, running or stopped."""
    if shutil.which("docker") is None:
        return []
    code, out, _ = await _run(_docker(), "compose", "ls", "-a", "--format", "json")
    if code != 0:
        return []
    names = {p.get("Name", "") for p in json.loads(out or "[]")}
    return sorted(n for n in names if _PROJECT.match(n))


def owner_id(data_dir: Path) -> str:
    """This process as the owner of the copies it starts on *data_dir*: an id whose lock file,
    ``<data_dir>/isolation/owners/<id>.lock``, it holds as long as it runs."""
    key = Path(data_dir).resolve()
    if key not in _OWNERS:
        folder = key / "isolation" / "owners"
        folder.mkdir(parents=True, exist_ok=True)
        found = tokens.token_hex(8)
        held = (folder / f"{found}.lock").open("w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _OWNERS[key] = (found, held)
    return _OWNERS[key][0]


def owner_gone(data_dir: Path, owner: str) -> bool:
    """Whether *owner* is an evalution of *data_dir* that no longer runs: its lock file is there,
    and nothing holds it. One this data folder doesn't know (another evalution's, with a data
    folder of its own) is never gone: its copies are not this one's to remove."""
    key = Path(data_dir).resolve()
    if not _OWNER.match(owner or "") or (key in _OWNERS and _OWNERS[key][0] == owner):
        return False
    path = key / "isolation" / "owners" / f"{owner}.lock"
    if not path.is_file():
        return False
    with path.open("a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(file, fcntl.LOCK_UN)
    return True


async def _owners() -> dict[str, set[str]]:
    """``project -> its containers' owner labels`` (``""`` for one with none), for every
    ``eval-*`` compose project on this computer."""
    if shutil.which("docker") is None:
        return {}
    label = f'{{{{.Label "com.docker.compose.project"}}}}\t{{{{.Label "{OWNER_LABEL}"}}}}'
    code, out, _ = await _run(
        _docker(), "ps", "-a", "--filter", "label=com.docker.compose.project", "--format", label
    )
    if code != 0:
        return {}
    found: dict[str, set[str]] = {}
    for line in out.splitlines():
        project, _, owner = line.partition("\t")
        if _PROJECT.match(project.strip()):
            found.setdefault(project.strip(), set()).add(owner.strip())
    return found


async def sweep(gone: Callable[[str], bool]) -> list[str]:
    """Remove the copies a stopped evalution left behind: every ``eval-*`` project whose
    containers all carry one :data:`OWNER_LABEL`, of an owner *gone* says no longer runs. One with
    a container without it is never removed (a rehearsal's, or not evalution's). The projects
    removed."""
    removed = []
    for project, owners in sorted((await _owners()).items()):
        if len(owners) != 1 or "" in owners or not gone(next(iter(owners))):
            continue
        try:
            await down(project)
        except LaunchError as exc:
            _logger.warning("evals: %s, left behind, not removed: %s", project, exc)
            continue
        removed.append(project)
    return removed


async def down_idle(project: str, data_dir: Path, app: str | None = None) -> bool:
    """Take *project* down when it is evalution's own idle copy: named ``eval-*``, its containers
    all carrying one :data:`OWNER_LABEL` of an evalution of *data_dir* (running or not). Only for
    a caller holding the target's lock (:class:`~evalution.isolation.lock.TargetLock`): no run
    plays on it then, so it is one a server parked between runs. One without the label, or
    another data folder's, is left, and its start says it is already running. Whether it was
    taken down; when it was, the log says so, of *app* (else of *project*)."""
    owners = (await _owners()).get(guard(project))
    if not owners or len(owners) != 1:
        return False
    if not _known(data_dir, next(iter(owners))):
        return False
    await down(project)
    _logger.warning(
        "evals: took down the server's idle copy of %s to start this one", app or project
    )
    return True


def _known(data_dir: Path, owner: str) -> bool:
    """Whether *owner* is an evalution of *data_dir*, running or not: its owner file is there."""
    if not _OWNER.match(owner):
        return False
    return (Path(data_dir).resolve() / "isolation" / "owners" / f"{owner}.lock").is_file()


def _created(value: str) -> float:
    """Seconds since the epoch of Docker's ``Created`` (nanoseconds, ``Z``)."""
    head, _, rest = value.partition(".")
    zone = rest.lstrip("0123456789") or "Z"
    fraction = rest[: len(rest) - len(zone)][:6]
    stamp = f"{head}.{fraction or '0'}{zone}".replace("Z", "+00:00")
    return datetime.fromisoformat(stamp).timestamp()


async def _image(ref: str) -> tuple[str, float] | None:
    """An image's id and when it was built; ``None`` when it isn't on this computer."""
    code, out, _ = await _run(
        _docker(), "image", "inspect", "--format", "{{.Id}}\t{{.Created}}", ref
    )
    image, _, created = out.strip().partition("\t")
    if code != 0 or not image:
        return None
    try:
        return image, _created(created)
    except ValueError:
        return None


async def extras(copy: Copy | str, since: float | None = None) -> list[Extra]:
    """Containers outside the copy's compose project that may be the copy's: named or labelled
    after it, or in no compose project and created since it started (*since*, else its start;
    by name alone, for a project known only by its name).
    For the probe to search; never removed."""
    project = copy if isinstance(copy, str) else copy.project
    if since is None and not isinstance(copy, str):
        since = copy.started
    if since is not None:
        since -= 2  # Docker's clock and this one's can differ by a little
    docker = _docker()
    _, ids, _ = await _run(docker, "ps", "-aq")
    if not ids.split():
        return []
    _, out, _ = await _run(docker, "inspect", *ids.split())
    found = []
    for item in json.loads(out or "[]"):
        labels = item.get("Config", {}).get("Labels") or {}
        owner = labels.get("com.docker.compose.project")
        if owner == project:
            continue
        name = item.get("Name", "").lstrip("/")
        image = item.get("Config", {}).get("Image", "")
        created = item.get("Created", "")
        if name.startswith(f"{project}-") or name.startswith(f"{project}_"):
            why = "named after the copy"
        elif any(str(v) == project or str(v).startswith(f"{project}-") for v in labels.values()):
            why = "labelled with the copy"
        elif owner is None and since is not None and created and _created(created) >= since:
            why = "started while the copy ran, in no compose project"
        else:
            continue
        found.append(Extra(name, image, created, why))
    return found


async def down(copy: Copy | str) -> list[Extra]:
    """Remove the copy's containers, networks and volumes. Only for a project named ``eval-*``;
    anything else raises before Docker is asked. What :func:`extras` finds is left running and
    returned, for the person to see."""
    project = guard(copy if isinstance(copy, str) else copy.project)
    left = await extras(copy)
    docker = _docker()
    # By label, with no compose file: the person's files can't widen what is removed.
    code, _, err = await _run(
        docker, "compose", "-p", project, "down", "-v", "--remove-orphans",
        limit=180, cwd=Path("/"),
    )  # fmt: skip
    if code != 0:
        raise LaunchError(f"couldn't remove {project}: {_tail(err)}")
    stray = [v for v in await _volumes(project) if v.startswith(f"{project}_")]
    if stray:
        await _run(docker, "volume", "rm", *stray)
    return left


#: What ``docker inspect`` says of a container: its service, its image's name and its image's id.
_RUNS = '{{index .Config.Labels "com.docker.compose.service"}}\t{{.Config.Image}}\t{{.Image}}'


class ComposeCopy:
    """A compose app started as ``eval-<target>-<slot>``.

    :param files: The person's compose files, in the order they pass them.
    :param env_file: The project's env file, passed to compose by path and never read for output.
    :param folder: Evalution's own folder, where the override file and the logs go.
    :param app_url: The app's address as the target knows it; :attr:`url` is the copy's.
    :param health: A path on :attr:`url` that answers below 400 once the app is up.
    """

    def __init__(
        self,
        files: list[Path],
        env_file: Path | None,
        target: str,
        slot: int,
        folder: Path,
        *,
        app_url: str | None = None,
        health: str | None = None,
        start_seconds: float = 300,
        env: dict[str, str] | None = None,
    ) -> None:
        self.files = [Path(f).resolve() for f in files]
        #: Settings the copy must have (where to send its traces), as its containers reach this
        #: computer. Given to compose too, so a value the compose file derives from one follows.
        self.env = {k: for_containers(v) for k, v in (env or {}).items()}
        self.env_file = Path(env_file).resolve() if env_file else None
        self.project = project_name(target, slot)
        self.folder = Path(folder)
        self.app_url = app_url
        self.health = health
        self.start_seconds = start_seconds
        self.copy: Copy | None = None
        self._secrets = secrets_of(self.env_file)
        #: Set on every container it starts: who started it, for which run (:func:`sweep`).
        self.labels: dict[str, str] = {}

    @property
    def url(self) -> str:
        if self.copy is None:
            raise LaunchError(f"{self.project} isn't started")
        return self.copy.url

    def _compose(self, *more: Path) -> list[str]:
        args = [_docker(), "compose"]
        for f in [*self.files, *more]:
            args += ["-f", str(f)]
        if self.env_file:
            args += ["--env-file", str(self.env_file)]
        return [*args, "-p", self.project]

    async def _config(self) -> dict:
        code, out, err = await _run(*self._compose(), "config", "--format", "json",
                                    secrets=self._secrets, env=self.env)  # fmt: skip
        if code != 0:
            raise LaunchError(f"compose couldn't read the app's files: {_tail(err)}")
        return json.loads(out)

    async def _missing(self, config: dict) -> list[str]:
        images = sorted({s["image"] for s in config.get("services", {}).values() if "image" in s})
        missing = []
        for image in images:
            code, _, _ = await _run(_docker(), "image", "inspect", "--format", "{{.Id}}", image)
            if code != 0:
                missing.append(image)
        unnamed = [n for n, s in config.get("services", {}).items() if "image" not in s]
        return missing + [f"{n} (built, with no image name)" for n in unnamed]

    async def _every(self) -> dict:
        """The compose config under every profile: an image the app's files build only to start
        it themselves (a host image its engine runs per person) is under one. The usual config
        when this compose can't say every profile."""
        code, out, _ = await _run(*self._compose(), "--profile", "*", "config", "--format", "json",
                                  secrets=self._secrets, env=self.env)  # fmt: skip
        return json.loads(out) if code == 0 else await self._config()

    async def images(self) -> list[Image]:
        """What this copy plays, each service's image with its id and when it was built
        (:mod:`evalution.core.version`). Started: the images its containers run, and those the
        app's files build that none of them runs (the app may start them itself). Not started: the
        images it would start from, as they are on this computer now. An image that isn't on this
        computer is left out. Read-only: nothing is started or pulled."""
        services = (await self._every()).get("services") or {}
        built = {n: s["image"] for n, s in services.items() if s.get("build") and s.get("image")}
        # ``service -> (image name, what to inspect)``: by id once a container runs it, so what
        # is read is what runs, even if its name was built over since.
        named: dict[str, tuple[str, str]] = {}
        if self.copy is not None and self.copy.containers:
            _, out, _ = await _run(_docker(), "inspect", "--format", _RUNS, *self.copy.containers)
            for line in out.splitlines():
                service, name, image = [*line.split("\t"), "", ""][:3]
                if service and image:
                    named[service] = (name, image)
        else:
            for n, s in services.items():
                if s.get("image") and not s.get("profiles"):
                    named[n] = (s["image"], s["image"])
        for service, name in built.items():
            named.setdefault(service, (name, name))
        found: list[Image] = []
        for service, (name, ref) in sorted(named.items()):
            seen = await _image(ref)
            if seen is not None:
                found.append(Image(service, name, seen[0], seen[1], service in built))
        return found

    async def start(self) -> Copy:
        """Start the copy and wait until its services are healthy. Raises :class:`LaunchError`
        with what went wrong, after removing what it started."""
        guard(self.project)
        if self.project in await copies():
            raise LaunchError(f"{self.project} is already running: take it down first")
        config = await self._config()
        if missing := await self._missing(config):
            raise LaunchError(
                "images not on this computer (evalution never builds or pulls them): "
                + ", ".join(missing)
            )
        _, version, _ = await _run(_docker(), "compose", "version", "--short")
        tag = supports_tag(version)
        plan = override_for(
            config, self.project, app_url=self.app_url, tag=tag, env=self.env, labels=self.labels
        )
        self.folder.mkdir(parents=True, exist_ok=True)
        override = self.folder / f"{self.project}.override.{'yaml' if tag else 'json'}"
        _write_private(override, plan.text)
        log = self.folder / f"{self.project}.log"
        compose = self._compose(override) if tag else self._resolved_compose(override)

        started = time.time()
        code, _, err = await _run(
            *compose, "up", "-d", "--wait", "--wait-timeout", str(int(self.start_seconds)),
            "--no-build", "--pull", "never", "--remove-orphans",
            limit=self.start_seconds + 60, secrets=self._secrets, env=self.env,
        )  # fmt: skip
        check = code == 0 and self.health and plan.url
        if check and not await _healthy(plan.url + self.health, self.start_seconds):
            code, err = 1, f"{plan.url}{self.health} didn't answer"
        if code != 0:
            await self._keep_logs(log)
            await down(self.project)
            raise LaunchError(f"{self.project} didn't start: {_tail(err)} (logs: {log})")
        self.copy = Copy(
            project=self.project,
            ports=plan.ports,
            url=plan.url,
            containers=await _names("--filter", f"label=com.docker.compose.project={self.project}"),
            volumes=await _volumes(self.project),
            shared=plan.shared,
            rewired=plan.rewired,
            fixed=plan.fixed,
            notes=plan.notes,
            started=started,
            start_seconds=round(time.time() - started, 1),
            override=override,
            log=log,
        )
        return self.copy

    def _resolved_compose(self, resolved: Path) -> list[str]:
        """Compose on the resolved file alone, for a compose that can't read ``!override``."""
        return [_docker(), "compose", "-f", str(resolved), "-p", self.project]

    async def _keep_logs(self, log: Path) -> None:
        _, out, err = await _run(*self._compose(), "logs", "--no-color", "--tail", "200")
        _write_private(log, scrub(out + err, self._secrets))

    async def logs(self) -> Path:
        """The copy's logs so far, scrubbed of the env file's values, kept in its folder."""
        log = self.folder / f"{self.project}.log"
        await self._keep_logs(log)
        return log

    async def services(self) -> dict[str, str]:
        """``container -> service`` of the copy's containers, running or stopped."""
        project = guard(self.project)
        label = f"label=com.docker.compose.project={project}"
        _, out, _ = await _run(
            _docker(), "ps", "-a", "--filter", label,
            "--format", '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
        )  # fmt: skip
        pairs = (line.split("\t", 1) for line in out.splitlines() if "\t" in line)
        return {name: service for name, service in pairs if name.startswith(f"{project}-")}

    async def pause(self, keep: Collection[str] = ()) -> list[str]:
        """Stop the copy's services, keeping its containers and volumes, all but the ones whose
        container is named in *keep* (a database a driver works in through its container). Only
        for a project named ``eval-*``. The services stopped."""
        project = guard(self.project)
        stop = sorted({s for c, s in (await self.services()).items() if c not in keep and s})
        if not stop:
            return []
        # By label, with no compose file, as ``down``: nothing outside the project is named.
        code, _, err = await _run(
            _docker(), "compose", "-p", project, "stop", *stop, limit=180, cwd=Path("/"),
            secrets=self._secrets,
        )  # fmt: skip
        if code != 0:
            raise LaunchError(f"couldn't stop {project}: {_tail(err)}")
        return stop

    async def mounting(self, volumes: Collection[str]) -> list[str]:
        """The copy's services whose containers mount any of *volumes*: what must stop while
        they are put back. Only for a project named ``eval-*``."""
        project = guard(self.project)
        found: set[str] = set()
        for volume in volumes:
            _, out, _ = await _run(
                _docker(), "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                "--filter", f"volume={volume}",
                "--format", '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
            )  # fmt: skip
            for line in out.splitlines():
                name, _, service = line.partition("\t")
                if service and name.startswith(f"{project}-"):
                    found.add(service)
        return sorted(found)

    async def stop_services(self, services: Collection[str]) -> list[str]:
        """Stop only *services* of the copy, keeping their containers and volumes; the rest of
        the copy stays up. Only for a project named ``eval-*``. The services stopped."""
        project = guard(self.project)
        own = set((await self.services()).values())
        stop = sorted(s for s in services if s in own)
        if not stop:
            return []
        code, _, err = await _run(
            _docker(), "compose", "-p", project, "stop", *stop, limit=180, cwd=Path("/"),
            secrets=self._secrets,
        )  # fmt: skip
        if code != 0:
            raise LaunchError(f"couldn't stop {', '.join(stop)} of {project}: {_tail(err)}")
        return stop

    async def answers(self, seconds: float = 60) -> bool:
        """Whether the copy's app answers within *seconds*: :attr:`health` below 400 when given,
        else anything at all on its address. One health call, with the app left as it is."""
        url = self.copy.url if self.copy else ""
        if not url:
            return False
        if self.health:
            return await _healthy(url + self.health, seconds)
        return await _healthy(url, seconds, any_answer=True)

    async def resume(self) -> None:
        """Start what :meth:`pause` stopped and wait until every container is healthy (and
        :attr:`health` answers, when given). Only for a project named ``eval-*``."""
        project = guard(self.project)
        code, _, err = await _run(
            _docker(), "compose", "-p", project, "start", limit=180, cwd=Path("/"),
            secrets=self._secrets,
        )  # fmt: skip
        if code != 0:
            raise LaunchError(f"couldn't start {project} again: {_tail(err)}")
        if not await _settled(project, self.start_seconds):
            raise LaunchError(f"{project} didn't come back healthy in {self.start_seconds:.0f}s")
        url = self.copy.url if self.copy else ""
        if self.health and url and not await _healthy(url + self.health, self.start_seconds):
            raise LaunchError(f"{url}{self.health} didn't answer after a restart")

    async def stop(self) -> list[Extra]:
        """Take the copy down; the containers it left outside its project, never removed."""
        left = await down(self.copy or self.project)
        self.copy = None
        return left


def _write_private(path: Path, text: str) -> None:
    """Write *text* to *path*, readable by this user only (it may hold the env file's values)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


async def _settled(project: str, seconds: float) -> bool:
    """Whether every container of *project* is running, and healthy where it has a check, within
    *seconds*. One that ran and exited cleanly (a one-off job) counts as settled. One whose check
    Docker hasn't run yet (it waits the check's interval first, seconds after the app is up) is
    healthy once its own check passes, run in it here (:func:`_passes`)."""
    label = f"label=com.docker.compose.project={project}"
    deadline = time.monotonic() + seconds
    checks: dict[str, list[str] | None] = {}
    while time.monotonic() < deadline:
        _, out, _ = await _run(
            _docker(), "ps", "-a", "--filter", label, "--format", "{{.Names}}\t{{.Status}}"
        )
        rows = [line.partition("\t")[::2] for line in out.splitlines() if line.strip()]
        ready = bool(rows)
        for name, status in rows:
            if ready and not _ready(status):
                ready = await _passes(name, status, checks)
        if ready:
            return True
        await asyncio.sleep(0.5)
    return False


def _health_test(test: object) -> list[str] | None:
    """A container's health check (Docker's ``Config.Healthcheck.Test``) as a command to run in
    it; ``None`` when it has none."""
    if not isinstance(test, list) or len(test) < 2:
        return None
    kind, *args = (str(t) for t in test)
    if kind == "CMD-SHELL":
        return ["sh", "-c", " ".join(args)]
    return args if kind == "CMD" else None


async def _passes(name: str, status: str, checks: dict[str, list[str] | None]) -> bool:
    """Whether container *name*, up with its health check not yet run by Docker (*status* says
    ``health: starting``), passes that check now, run in it as Docker would. *checks*: each
    container's check, read once."""
    if not status.startswith("Up") or "health: starting" not in status:
        return False
    if name not in checks:
        _, out, _ = await _run(
            _docker(), "inspect", "--format", "{{json .Config.Healthcheck}}", name
        )
        try:
            checks[name] = _health_test((json.loads(out or "null") or {}).get("Test"))
        except (ValueError, AttributeError):
            checks[name] = None
    test = checks[name]
    if test is None:
        return False
    code, _, _ = await _run(_docker(), "exec", name, *test, limit=10)
    return code == 0


def _ready(status: str) -> bool:
    """Whether a container's ``docker ps`` status says it is up and well, or done cleanly."""
    if status.startswith("Exited (0)"):
        return True
    return status.startswith("Up") and "starting" not in status and "unhealthy" not in status


async def _healthy(url: str, seconds: float, *, any_answer: bool = False) -> bool:
    """Whether *url* answers (below 400, or anything with *any_answer*) within *seconds*."""
    deadline = time.monotonic() + seconds
    # On this machine: never through a proxy from the environment, which can't reach it.
    async with net.client() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url, timeout=2)
                if any_answer or r.status_code < 400:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    return False


class ProcessCopy:
    """An app that isn't in Docker, started from its command.

    ``{port}`` in *command* and in *env*'s values becomes the copy's port: *port*, else a free
    one. *env* is laid over this process's environment; that is where settings that move the
    app's stores to evalution's copies go. Its output goes to a log in *folder* when given.

    :param health: A path on :attr:`url` that answers below 400 once the app is up; without one,
        any answer on :attr:`url` will do.
    """

    def __init__(
        self,
        command: list[str],
        cwd: Path,
        *,
        env: dict[str, str] | None = None,
        port: int | None = None,
        health: str | None = None,
        folder: Path | None = None,
        start_seconds: float = 60,
        stop_seconds: float = 10,
    ) -> None:
        self.command = command
        self.cwd = Path(cwd)
        self.env = env or {}
        self.port = port
        self.health = health
        self.folder = folder
        self.start_seconds = start_seconds
        self.stop_seconds = stop_seconds
        self.proc: asyncio.subprocess.Process | None = None

    @property
    def url(self) -> str:
        if self.port is None:
            raise LaunchError("the copy isn't started")
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    async def start(self) -> int:
        """Start the app and wait until it answers; its pid. Stops it and raises
        :class:`LaunchError` when it exits or doesn't answer in time."""
        self.port = self.port or free_port()
        fill = {"port": str(self.port)}
        args = [a.format_map(fill) for a in self.command]
        env = {**os.environ, **{k: v.format_map(fill) for k, v in self.env.items()}}
        out = _log_file(self.folder, f"process-{self.port}.log")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *args, cwd=self.cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=out, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            )  # fmt: skip
        finally:
            if out is not asyncio.subprocess.DEVNULL:
                os.close(out)
        url = self.url + (self.health or "/")
        waiting = asyncio.create_task(
            _healthy(url, self.start_seconds, any_answer=self.health is None)
        )
        exited = asyncio.create_task(self.proc.wait())
        await asyncio.wait({waiting, exited}, return_when=asyncio.FIRST_COMPLETED)
        if waiting.done() and waiting.result():
            exited.cancel()
            return self.proc.pid
        waiting.cancel()
        code = self.proc.returncode
        await self.stop()
        why = f"exited with code {code}" if code is not None else f"{url} didn't answer"
        raise LaunchError(f"the app didn't start: {why}")

    async def stop(self) -> None:
        """Ask the app and every process it started to stop, then make them."""
        proc, self.proc = self.proc, None
        if proc is None:
            return
        _signal_group(proc.pid, signal.SIGTERM)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), self.stop_seconds)
        _signal_group(proc.pid, signal.SIGKILL)  # children that outlived it too
        await proc.wait()


def _signal_group(pid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def _log_file(folder: Path | None, name: str) -> int:
    if folder is None:
        return asyncio.subprocess.DEVNULL
    folder.mkdir(parents=True, exist_ok=True)
    return os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
