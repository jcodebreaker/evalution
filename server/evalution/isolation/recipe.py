"""How a version of the app is built into evalution's own names, kept as data (ADR 0011, phase 3).

A :class:`BuildRecipe` is one of three kinds:

* ``compose_env``: the compose files name each built image through two variables, one carrying
  the name (``image: ${IMAGE_REGISTRY:-relay}/engine:${IMAGE_TAG:-local}``) and one the tag.
  Setting them to ``evalution/<target>`` and the version's tag builds into evalution's names with
  nothing written over the app's files: relay-lite's shape (phase 0).
* ``compose_override``: the names are fixed, so a build override evalution writes in its own
  folder rewrites each built service's ``image:`` to ``evalution/<target>-<service>:<tag>``.
* ``process``: the app runs from its folder, so a version is set up there (``uv sync``,
  ``npm ci``) before the plan's start command runs in it.

:func:`propose` finds one by rule, with no model: from the compose files read raw (``${VAR}`` in
image names) or from lockfiles. Every recipe cites the files it rests on (``path:line``, as
isolation plans do) with their hashes, so :func:`stale` says when one changed.
:class:`RecipeStore` keeps a target's recipe as ``<data>/builds/<target>/recipe.json`` with every
saved version beside it (``versions/<n>.json``), undoable (ADR 0005), and what was built from it
(``built.json``).

Names are always evalution's: :func:`image_names` gives only names under ``evalution/``, and
building refuses any other (:mod:`evalution.isolation.build`).
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from evalution.isolation.plan import hashes

#: The shape of a saved recipe; a newer one is refused rather than misread.
FORMAT = 1
#: Saved versions kept per target, the oldest dropped first.
VERSIONS_KEPT = 20
#: Every image evalution builds is named under this, and it builds and removes no other.
OWN = "evalution/"

Kind = Literal["compose_env", "compose_override", "process"]
KINDS: tuple[str, ...] = ("compose_env", "compose_override", "process")
Status = Literal["proposed", "proven", "stale", "failed"]

#: Lockfiles, and the command that sets a folder up from each, first found first per folder.
LOCKFILES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uv.lock", ("uv", "sync")),
    ("package-lock.json", ("npm", "ci")),
    ("pnpm-lock.yaml", ("pnpm", "install", "--frozen-lockfile")),
    ("yarn.lock", ("yarn", "install", "--frozen-lockfile")),
)
#: Folders never searched for lockfiles: installed things, not the app's own.
_SKIP = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".git"}


#: ``${NAME}``, ``${NAME:-default}``, ``${NAME-default}``, ``${NAME:?error}``: a variable compose
#: fills in.
def _var(group: str) -> str:
    return r"\$\{(?P<" + group + r">[A-Za-z_][A-Za-z0-9_]*)(?::?[-?+][^}]*)?\}"


#: An image named through a variable for its name and one for its tag: ``${A}/engine:${B}``.
_ENV_IMAGE = re.compile(
    "^" + _var("name") + r"(?P<rest>/[a-z0-9][a-z0-9._/-]*):" + _var("tag") + "$"
)
_ANY_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_TARGET = re.compile(r"^[A-Za-z0-9_-]+$")
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass
class Claim:
    """One thing the recipe rests on: ``at`` (``path:line`` in the app's folder) and what it
    says there."""

    at: str
    says: str


@dataclass
class SetupStep:
    """One setup command of a process recipe, run in ``cwd`` (relative to the version's folder)."""

    run: list[str]
    cwd: str = "."


@dataclass
class BuildRecipe:
    """How to build a version of *target* (see the module's docstring).

    :param files: The compose files, relative to the app's code folder, in the order passed.
    :param services: The services built, each one with a ``build:`` section.
    :param profiles: The profiles those services are under (a build-only image's ``build``).
    :param name_var: ``compose_env``: the variable carrying the image name's start.
    :param tag_var: ``compose_env``: the variable carrying the tag.
    :param images: Each built service's image as the compose files name it, unresolved.
    :param setup: ``process``: the commands that set a version's folder up, in order.
    :param command: ``process``: the start command, as the isolation plan gives it.
    :param cwd: ``process``: where it starts, relative to the code folder.
    :param evidence: What it rests on, ``path:line`` each.
    :param evidence_hashes: ``path -> sha256`` of each cited file when it was proposed.
    :param by: ``rule`` (:func:`propose`) or ``agent`` (the test writer adjusted it).
    :param last_build: What the last build of it took (:class:`evalution.isolation.build.Built`).
    """

    target: str
    kind: Kind
    files: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    name_var: str | None = None
    tag_var: str | None = None
    images: dict[str, str] = field(default_factory=dict)
    setup: list[SetupStep] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    cwd: str = "."
    evidence: list[Claim] = field(default_factory=list)
    evidence_hashes: dict[str, str] = field(default_factory=dict)
    status: Status = "proposed"
    report: str = ""
    by: str = "rule"
    why: str = ""
    last_build: dict[str, Any] | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({"version": FORMAT, **asdict(self)}, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> BuildRecipe:
        """
        :raises ValueError: Not a recipe, or one saved by a newer evalution.
        """
        data = json.loads(text)
        if not isinstance(data, dict) or "target" not in data or "kind" not in data:
            raise ValueError("not a build recipe")
        version = data.pop("version", FORMAT)
        if not isinstance(version, int) or version > FORMAT:
            raise ValueError(f"build recipe format {version} is newer than this evalution")
        if data["kind"] not in KINDS:
            raise ValueError(f"not a kind of build recipe: {data['kind']!r}")
        setup = [SetupStep(**s) for s in data.pop("setup", [])]
        evidence = [Claim(**c) for c in data.pop("evidence", [])]
        return cls(**data, setup=setup, evidence=evidence)

    def same_as(self, other: BuildRecipe) -> bool:
        """Whether *other* builds the same way: what is built and how, whatever its status."""
        keep = ("kind", "files", "services", "profiles", "name_var", "tag_var", "setup", "cwd")
        return all(getattr(self, k) == getattr(other, k) for k in keep) and (
            self.command == other.command
        )


# Names


def repository(target: str) -> str:
    """Evalution's name for *target*'s images: ``evalution/<target>``, in Docker's spelling."""
    slug = re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-") or "app"
    return OWN + slug


def check_tag(tag: str) -> str:
    """*tag*, when Docker takes it as a tag.

    :raises ValueError: It isn't one.
    """
    if not _TAG.match(tag):
        raise ValueError(f"not an image tag: {tag!r}")
    return tag


def image_names(recipe: BuildRecipe, tag: str) -> dict[str, str]:
    """``service -> image`` evalution builds for *tag*, every one under ``evalution/``; nothing
    for a process recipe.

    :raises ValueError: *tag* isn't an image tag, or an image can't be named from the recipe.
    """
    return named(recipe, check_tag(tag))


def named(recipe: BuildRecipe, tag: str) -> dict[str, str]:
    """As :func:`image_names`, with *tag* unchecked: for words, such as ``<commit>``."""
    own = repository(recipe.target)
    names: dict[str, str] = {}
    for service in recipe.services:
        if recipe.kind == "compose_env":
            found = _ENV_IMAGE.match(recipe.images.get(service, ""))
            if found is None:
                raise ValueError(f"{service}'s image isn't named through variables")
            names[service] = f"{own}{found['rest']}:{tag}"
        elif recipe.kind == "compose_override":
            slug = re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-")
            names[service] = f"{own}-{slug}:{tag}"
    for name in names.values():
        if not name.startswith(OWN):
            raise ValueError(f"{name} isn't one of evalution's names")
    return names


def build_env(recipe: BuildRecipe, tag: str) -> dict[str, str]:
    """The variables that name the version's images, for the build and for a copy started from
    them (a copy's env wins over the env file): ``compose_env`` only."""
    if recipe.kind != "compose_env" or not recipe.name_var or not recipe.tag_var:
        return {}
    return {recipe.name_var: repository(recipe.target), recipe.tag_var: check_tag(tag)}


def override_text(recipe: BuildRecipe, tag: str) -> str | None:
    """The build override for a ``compose_override`` recipe: each built service's ``image:``
    rewritten to evalution's name. JSON, which compose reads as YAML. ``None`` for other kinds."""
    if recipe.kind != "compose_override":
        return None
    names = image_names(recipe, tag)
    return json.dumps({"services": {s: {"image": n} for s, n in names.items()}}, indent=2) + "\n"


# Proposing one by rule


def _relative(path: Path, folder: Path) -> str | None:
    try:
        return path.resolve().relative_to(folder).as_posix()
    except ValueError:
        return None


def _lines(text: str) -> dict[tuple[str, str | None], int]:
    """``(service, key) -> line`` of each top-level key under each service in a compose file
    (``(service, None)``: the service itself). Read by indent, as YAML lays it out."""
    out: dict[tuple[str, str | None], int] = {}
    in_services, service_indent, key_indent = False, None, None
    current: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_services, current = stripped.startswith("services:"), None
            continue
        if not in_services:
            continue
        if service_indent is None:
            service_indent = indent
        head = stripped.split(":", 1)[0].strip().strip("'\"")
        if indent == service_indent:
            current, key_indent = head, None
            out[(current, None)] = number
        elif current is not None and indent > service_indent:
            key_indent = indent if key_indent is None else key_indent
            if indent == key_indent:
                out.setdefault((current, head), number)
    return out


@dataclass
class _Service:
    image: str | None = None
    image_at: str | None = None
    build: Any = None
    build_at: str | None = None
    profiles: list[str] = field(default_factory=list)
    folder: Path | None = None


def _services(folder: Path, files: list[Path]) -> tuple[dict[str, _Service], list[str]]:
    """Each service as the compose files name it, raw (later files win), and the files that
    couldn't be read."""
    services: dict[str, _Service] = {}
    unread: list[str] = []
    for path in files:
        rel = _relative(path, folder) or str(path)
        try:
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text) or {}
        except (OSError, yaml.YAMLError):
            unread.append(rel)
            continue
        lines = _lines(text)
        for name, body in (data.get("services") or {}).items():
            if not isinstance(body, dict):
                continue
            found = services.setdefault(str(name), _Service())
            if "image" in body:
                found.image = str(body["image"])
                found.image_at = f"{rel}:{lines.get((name, 'image'), 1)}"
            if "build" in body:
                found.build = body["build"]
                found.build_at = f"{rel}:{lines.get((name, 'build'), 1)}"
                found.folder = path.parent
            if "profiles" in body:
                found.profiles = [str(p) for p in body.get("profiles") or []]
    return services, unread


def profiles_of(folder: Path, files: list[str], services: list[str]) -> list[str]:
    """The profiles *services* are under in the compose *files* (relative to *folder*)."""
    found, _ = _services(folder.resolve(), [(folder / f).resolve() for f in files])
    return sorted({p for n in services if n in found for p in found[n].profiles})


def _dockerfile(service: _Service, folder: Path) -> str | None:
    """The Dockerfile a service is built from, relative to *folder*, when it is in it."""
    build, base = service.build, service.folder or folder
    context = build if isinstance(build, str) else (build or {}).get("context", ".")
    name = "Dockerfile" if isinstance(build, str) else (build or {}).get("dockerfile", "Dockerfile")
    if not isinstance(context, str) or not isinstance(name, str) or "://" in context:
        return None
    path = (base / context / name).resolve()
    rel = _relative(path, folder)
    return rel if rel and path.is_file() else None


def env_vars(images: dict[str, str]) -> tuple[str, str] | None:
    """The one variable naming every image and the one tagging it, when each of *images* is
    ``${NAME}/<rest>:${TAG}`` with the same two; else ``None``."""
    pairs = set()
    for image in images.values():
        found = _ENV_IMAGE.match(image)
        if found is None:
            return None
        pairs.add((found["name"], found["tag"]))
    if len(pairs) != 1:
        return None
    name, tag = pairs.pop()
    return (name, tag) if name != tag else None


def _compose(folder: Path, target: str, files: list[Path]) -> BuildRecipe:
    services, unread = _services(folder, files)
    rels = [_relative(f, folder) or str(f) for f in files]
    built = {n: s for n, s in sorted(services.items()) if s.build is not None}
    evidence: list[Claim] = []
    notes: list[str] = [f"Couldn't read {f}." for f in unread]
    images = {n: s.image for n, s in built.items() if s.image}
    for name, service in built.items():
        if service.build_at:
            evidence.append(Claim(service.build_at, f"{name} is built from the app's own files"))
        if service.image_at and service.image:
            evidence.append(Claim(service.image_at, f"{name}'s image is {service.image}"))
        if dockerfile := _dockerfile(service, folder):
            evidence.append(Claim(f"{dockerfile}:1", f"{name} is built from this Dockerfile"))
    profiles = sorted({p for s in built.values() for p in s.profiles})
    found = env_vars(images) if images and len(images) == len(built) else None
    if found is not None:
        name_var, tag_var = found
        # A pulled image named through the same variables would be renamed too, and isn't built.
        shared = sorted(
            n
            for n, s in services.items()
            if n not in built and s.image and {name_var, tag_var} & set(_ANY_VAR.findall(s.image))
        )
        if shared:
            notes.append(
                f"{', '.join(shared)} also take their image name from {name_var} or {tag_var}, "
                "and evalution doesn't build them: their names are rewritten instead."
            )
            found = None
    recipe = BuildRecipe(
        target=target,
        kind="compose_env" if found else "compose_override",
        files=rels,
        services=list(built),
        profiles=profiles,
        name_var=found[0] if found else None,
        tag_var=found[1] if found else None,
        images={n: s.image or "" for n, s in built.items()},
        evidence=evidence,
    )
    if not built:
        notes.append(
            "The compose files build no image of their own: every image is pulled, so a version "
            "of the code doesn't change what plays."
        )
    elif found is None and images and len(images) == len(built):
        notes.append(
            "The image names aren't set through one variable for the name and one for the tag, "
            "so a build override evalution writes renames them."
        )
    recipe.report = "\n".join([words(recipe), *notes])
    return recipe


def _lockfiles(folder: Path, cwd: Path) -> list[tuple[Path, tuple[str, ...]]]:
    """Each folder's first lockfile and its setup command: the start folder, the app's folder,
    and the folders just under it."""
    places = [cwd, folder]
    with contextlib.suppress(OSError):
        places += sorted(
            p for p in folder.iterdir() if p.is_dir() and p.name not in _SKIP and p.name[0] != "."
        )
    found: list[tuple[Path, tuple[str, ...]]] = []
    seen: set[Path] = set()
    for place in places:
        if place in seen:
            continue
        seen.add(place)
        for name, command in LOCKFILES:
            if (place / name).is_file():
                found.append((place / name, command))
                break
    return found


def _process(folder: Path, target: str, spec: dict[str, Any]) -> BuildRecipe:
    raw_cwd = Path(str(spec.get("cwd") or folder))
    cwd = (raw_cwd if raw_cwd.is_absolute() else folder / raw_cwd).resolve()
    rel_cwd = _relative(cwd, folder)
    notes = []
    if rel_cwd is None:
        notes.append(f"It starts in {cwd}, outside the app's folder: a version starts there too.")
        rel_cwd, cwd = ".", folder
    setup, evidence = [], []
    for lock, command in _lockfiles(folder, cwd):
        rel = _relative(lock.parent, folder) or "."
        setup.append(SetupStep(list(command), rel))
        evidence.append(Claim(f"{_relative(lock, folder)}:1", f"sets up with {' '.join(command)}"))
    recipe = BuildRecipe(
        target=target,
        kind="process",
        setup=setup,
        command=[str(a) for a in spec.get("command") or []],
        cwd=rel_cwd,
        evidence=evidence,
    )
    if not setup:
        notes.append("No lockfile found: a version starts as it is checked out.")
    recipe.report = "\n".join([words(recipe), *notes])
    return recipe


def propose(folder: Path, target: str, spec: dict[str, Any]) -> BuildRecipe:
    """The recipe for *target* by rule, from how a copy of it starts (*spec*, as
    :func:`evalution.isolation.isolate.launch_spec` gives it) and the files in *folder*, its code
    folder. ``compose_env`` when every built service's image is named through the same two
    variables, else ``compose_override``; a process app's ``process``, set up from its
    lockfiles. Status ``proposed``, its evidence hashed.

    :raises ValueError: *target* isn't a target's name.
    :raises LookupError: *spec* says neither compose nor a process.
    """
    if not _TARGET.match(target):
        raise ValueError(f"not a target name: {target!r}")
    folder = folder.resolve()
    if spec.get("kind") == "compose":
        files = [Path(str(f)) for f in spec.get("files") or []]
        files = [f if f.is_absolute() else folder / f for f in files]
        recipe = _compose(folder, target, files)
    elif spec.get("kind") == "process":
        recipe = _process(folder, target, spec)
    else:
        raise LookupError(f"not a way to start a copy: {spec.get('kind')!r}")
    recipe.evidence_hashes = hashes(folder, [c.at for c in recipe.evidence])
    return recipe


def check(recipe: BuildRecipe, folder: Path) -> list[str]:
    """What is wrong with *recipe* against the files in *folder*, in plain words; nothing when
    it holds. For a recipe the test writer adjusted: names only what the files say."""
    if not _TARGET.match(recipe.target):
        return [f"not a target name: {recipe.target!r}"]
    if recipe.kind == "process":
        return _check_process(recipe, folder)
    files = [(folder / f).resolve() for f in recipe.files]
    outside = [f for f, p in zip(recipe.files, files, strict=True) if _relative(p, folder) is None]
    if outside or not recipe.files:
        return [f"compose files must be in the app's folder: {', '.join(outside) or 'none given'}"]
    services, unread = _services(folder.resolve(), files)
    wrong = [f"couldn't read {f}" for f in unread]
    built = {n for n, s in services.items() if s.build is not None}
    for service in recipe.services:
        if not _SERVICE.match(service) or service not in built:
            wrong.append(f"{service} isn't a service the compose files build")
    if not recipe.services and built:
        wrong.append("no service named to build")
    having = {p for n in recipe.services if n in services for p in services[n].profiles}
    wrong += [
        f"no built service is under the profile {p}" for p in recipe.profiles if p not in having
    ]
    missing = sorted(having - set(recipe.profiles))
    if missing:
        wrong.append(f"services named are under the profile {', '.join(missing)}: name it")
    if recipe.kind == "compose_env":
        images = {n: services[n].image or "" for n in recipe.services if n in services}
        if env_vars(images) != (recipe.name_var, recipe.tag_var):
            wrong.append(
                f"not every image is named ${{{recipe.name_var}}}/<name>:${{{recipe.tag_var}}}: "
                "use compose_override"
            )
    return wrong


def _check_process(recipe: BuildRecipe, folder: Path) -> list[str]:
    wrong = []
    for step in [*recipe.setup, SetupStep([], recipe.cwd)]:
        place = (folder / step.cwd).resolve()
        if _relative(place, folder.resolve()) is None or not place.is_dir():
            wrong.append(f"{step.cwd} isn't a folder in the app's folder")
    tools = {c[0] for _, c in LOCKFILES}
    for step in recipe.setup:
        if not step.run or step.run[0] not in tools:
            wrong.append(
                f"setup runs only an installer ({', '.join(sorted(tools))}): {' '.join(step.run)}"
            )
    return wrong


# What it says


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def words(recipe: BuildRecipe, tag: str = "<commit>") -> str:
    """The recipe in a sentence or two, as the page and the writer say it."""
    if recipe.kind == "process":
        steps = [" ".join(s.run) + (f" in {s.cwd}" if s.cwd != "." else "") for s in recipe.setup]
        setup = f"runs {_listed(steps)}, then " if steps else ""
        start = " ".join(recipe.command) or "the start command"
        where = f" in {recipe.cwd}" if recipe.cwd != "." else ""
        return (
            f"A version is checked out into evalution's folder, which {setup}starts it with "
            f"{start}{where}. Your folder isn't touched."
        )
    if not recipe.services:
        return "The compose files build nothing: a version plays the same pulled images."
    names = list(named(recipe, tag).values())
    built = f"Built with docker compose: {_listed(recipe.services)}"
    if recipe.profiles:
        built += f" (profile {', '.join(recipe.profiles)})"
    if recipe.kind == "compose_env":
        how = f", named through {recipe.name_var} and {recipe.tag_var}"
    else:
        how = ", renamed by an override evalution writes in its own folder"
    return f"{built}{how}: {_listed(names)}. Your own images aren't touched."


# Out of date


def stale(recipe: BuildRecipe, folder: Path) -> list[str]:
    """The files *recipe*'s evidence cites that differ in *folder* (the disk, or a version's
    folder) from when it was proposed, or are gone: a commit or an edit since, as a diff of those
    paths would show."""
    out = []
    for rel, digest in sorted(recipe.evidence_hashes.items()):
        path = folder / rel
        if not path.is_file() or hashes(folder, [f"{rel}:1"]).get(rel) != digest:
            out.append(rel)
    return out


# Kept


class RecipeStore:
    """Each target's recipe as ``<data_dir>/builds/<target>/recipe.json``, with every saved
    version in ``versions/<n>.json`` beside it (the newest :data:`VERSIONS_KEPT`), and what was
    built in ``built.json`` (``tag -> record``)."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "builds"

    def folder(self, target: str) -> Path:
        """Evalution's own folder for *target*'s builds: overrides and logs go here.

        :raises ValueError: Not a target's name.
        """
        if not _TARGET.match(target):
            raise ValueError(f"not a target name: {target!r}")
        return self.root / target

    def _versions(self, target: str) -> list[tuple[int, Path]]:
        folder = self.folder(target) / "versions"
        if not folder.is_dir():
            return []
        return sorted((int(p.stem), p) for p in folder.glob("*.json") if p.stem.isdigit())

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def load(self, target: str) -> BuildRecipe | None:
        """The target's current recipe; ``None`` when it has none.

        :raises ValueError: A file that isn't a recipe this evalution reads.
        """
        path = self.folder(target) / "recipe.json"
        return BuildRecipe.from_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save(self, recipe: BuildRecipe) -> int:
        """Store *recipe* as the target's current one and as a new version; its number."""
        recipe.updated = time.time()
        kept = self._versions(recipe.target)
        number = kept[-1][0] + 1 if kept else 1
        text = recipe.to_json()
        self._write(self.folder(recipe.target) / "versions" / f"{number}.json", text)
        self._write(self.folder(recipe.target) / "recipe.json", text)
        for _, old in kept[: max(0, len(kept) + 1 - VERSIONS_KEPT)]:
            old.unlink()
        return number

    def version(self, target: str) -> int | None:
        kept = self._versions(target)
        return kept[-1][0] if kept else None

    def history(self, target: str) -> list[tuple[int, BuildRecipe]]:
        """Every kept version with its number, the oldest first; the last is the current one."""
        return [
            (n, BuildRecipe.from_json(p.read_text(encoding="utf-8")))
            for n, p in self._versions(target)
        ]

    def undo(self, target: str) -> BuildRecipe | None:
        """Drop the current version and go back to the one before; ``None`` when none is left.

        :raises LookupError: The target has no recipe.
        """
        kept = self._versions(target)
        if not kept:
            raise LookupError(f"{target} has no build recipe")
        kept.pop()[1].unlink()
        current = self.folder(target) / "recipe.json"
        if not kept:
            current.unlink(missing_ok=True)
            return None
        text = kept[-1][1].read_text(encoding="utf-8")
        self._write(current, text)
        return BuildRecipe.from_json(text)

    # What was built

    def built(self, target: str) -> dict[str, dict[str, Any]]:
        """``tag -> record`` of each version built for *target* and not removed."""
        path = self.folder(target) / "built.json"
        if not path.is_file():
            return {}
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}

    def record_built(self, target: str, record: dict[str, Any]) -> None:
        """Keep *record* (with its ``tag``) as built."""
        found = self.built(target)
        found[str(record["tag"])] = record
        self._write(self.folder(target) / "built.json", json.dumps(found, indent=2) + "\n")

    def forget_built(self, target: str, tag: str) -> None:
        found = self.built(target)
        if found.pop(tag, None) is not None:
            self._write(self.folder(target) / "built.json", json.dumps(found, indent=2) + "\n")
