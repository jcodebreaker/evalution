"""The app watched from inside Docker, when the port it answers on is a container's.

From the host, such an app is a port forwarder and nothing else: what it stores lives in its
containers' volumes and in database containers beside it. This finds the container publishing the
port, takes every container of its compose project, and after the probe looks for the canary:

* in each Postgres container, table by table, from a data-only ``pg_dump`` it reads as it streams;
* in each container's mounts (volumes and bind mounts), with ``grep``.

Everything here reads. The commands run inside the containers are ``pg_dump`` and ``grep``.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field

from evalution.isolation.discover import Store
from evalution.isolation.drivers.postgres import DumpScanner

#: Seconds one search inside a container may take.
SEARCH_SECONDS = 120
#: Mounts never searched: sockets, and the usual read-only config.
_SKIP_MOUNT = re.compile(r"\.sock$|^/(etc|proc|sys|dev)(/|$)|\.(sql|conf|ya?ml|json|sh|py)$")


@dataclass
class Container:
    name: str
    image: str
    #: ``destination -> source`` (a volume's name, or a host path).
    mounts: dict[str, str]
    env: dict[str, str] = field(repr=False, default_factory=dict)

    @property
    def postgres(self) -> bool:
        return "postgres" in self.image.lower() or "pgvector" in self.image.lower()


@dataclass
class Found:
    """Where the canary turned up inside a container."""

    container: str
    kind: str  # "postgres" or "volume" or "bind"
    where: str  # "db.table", or "volume:NAME/path", or a host path


async def _run(*args: str, limit: float = 30) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), limit)
    except TimeoutError:
        proc.kill()
        return ""
    return out.decode(errors="replace")


async def project_of(port: int) -> list[Container]:
    """Every container of the compose project whose container publishes *port*; just that one
    when it has no project; none when no container does or Docker isn't there."""
    docker = shutil.which("docker")
    if docker is None:
        return []
    ids = (await _run(docker, "ps", "-q", "--filter", f"publish={port}")).split()
    if not ids:
        return []
    first = json.loads(await _run(docker, "inspect", ids[0]) or "[]")
    if not first:
        return []
    project = first[0]["Config"]["Labels"].get("com.docker.compose.project")
    if project:
        ids = (
            await _run(
                docker, "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"
            )
        ).split()
    out = []
    for item in json.loads(await _run(docker, "inspect", *ids) or "[]"):
        env = dict(e.split("=", 1) for e in item["Config"].get("Env") or [] if "=" in e)
        mounts = {
            m["Destination"]: (f"volume:{m['Name']}" if m["Type"] == "volume" else m["Source"])
            for m in item.get("Mounts") or []
        }
        out.append(Container(item["Name"].lstrip("/"), item["Config"]["Image"], mounts, env))
    return out


async def _postgres_tables(container: Container, canary: str) -> list[str]:
    """``db.table`` of each table holding *canary*, from a data-only dump read as it streams."""
    user = container.env.get("POSTGRES_USER", "postgres")
    db = container.env.get("POSTGRES_DB", user)
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", container.name, "pg_dump", "-U", user, "-d", db, "--data-only",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=256 << 20,
    )  # fmt: skip
    scanner = DumpScanner(db, canary)
    assert proc.stdout is not None
    try:
        async with asyncio.timeout(SEARCH_SECONDS):
            async for raw in proc.stdout:
                scanner.feed(raw.decode(errors="replace"))
    except TimeoutError:
        proc.kill()
    await proc.wait()
    return scanner.tables


async def _mount_files(container: Container, dest: str, canary: str) -> list[str]:
    out = await _run(
        "docker", "exec", container.name, "grep", "-rlsa", "--", canary, dest, limit=SEARCH_SECONDS
    )
    return [line for line in out.splitlines() if line]


async def search(containers: list[Container], canary: str) -> list[Found]:
    """Everywhere *canary* turned up in *containers*."""
    found: list[Found] = []
    for c in containers:
        if c.postgres:
            found += [Found(c.name, "postgres", t) for t in await _postgres_tables(c, canary)]
            continue  # its data folder is the same thing, as pages
        for dest, source in c.mounts.items():
            if _SKIP_MOUNT.search(dest) or _SKIP_MOUNT.search(source):
                continue
            for path in await _mount_files(c, dest, canary):
                kind = "volume" if source.startswith("volume:") else "bind"
                found.append(Found(c.name, kind, f"{source}{path.removeprefix(dest)}"))
    return found


def reconcile(stores: list[Store], containers: list[Container], found: list[Found]) -> list[Store]:
    """Stores marked with where the canary turned up in containers, plus ones only that found."""
    projects = {c.name.rsplit("-", 2)[0] for c in containers}
    added: list[Store] = []
    for f in found:
        note = f"holds the canary: {f.container}: {f.where}"
        if f.kind == "postgres":
            # What moves or names the app's database, not URLs from examples in the code.
            hit = [
                s
                for s in stores
                if s.kind == "postgres" and s.locator.startswith(("env:", "service:", "package:"))
            ]
        else:
            volume = f.where.split("/", 1)[0].removeprefix("volume:")
            # Compose names a volume "<project>_<name>".
            short = next(
                (volume.removeprefix(f"{p}_") for p in projects if volume.startswith(f"{p}_")),
                volume,
            )
            hit = [s for s in stores if s.locator in (f"volume:{volume}", f"volume:{short}")]
        for s in hit:
            s.seen.append(note)
        locator = f"{f.container}:{f.where}"
        added.append(Store(f.kind, locator, ["found inside a container, by the probe"], [note]))
    return [*stores, *added]
