"""A run of a version of the code other than what's on disk (ADR 0011, plan "Phase 4: run a
version").

A run asks for a **ref**: ``disk`` (the default: what's on disk now, exactly as before), or a
branch, a remote's branch, a tag or a commit. For a ref, before any copy starts:

1. **Refused before anything starts** (:func:`refusal`) when the target doesn't play on
   evalution's own copy (the person's app as it runs can't be another version), or has no proven
   build recipe: one plain next step, "Prove how <app> is built first, on Test environment."
2. **Checked out** (:func:`prepare`): the ref resolved, a remote's branch or a pull request
   (``pull/412``) fetched again into evalution's own refs every time (never an earlier fetch),
   and a detached worktree of its commit made in evalution's data folder
   (:mod:`evalution.project.versions`). The person's checkout is never touched.
3. **Built** with the target's proven recipe into evalution's own names, tagged with the commit's
   first twelve characters, unless that tag is built already (the recipe store's record, and the
   images still on this computer); a process app's setup runs in the worktree. What was built is a
   :class:`~evalution.project.building.BuiltPart` of the version: counted toward the target's
   limit, and removed with the worktree.
4. **Started from the version**: the copy's compose files are the same paths in the worktree as
   the plan's are in the person's checkout, the recipe's variables in the copy's env (they win
   over the env file), or its override after the compose files; the env file stays the person's
   (``.env`` isn't in a worktree, phase 0). A process copy starts in the worktree, its command's
   paths and its file stores moved there too.

A checkout or build that fails ends the run before any try (:class:`NotBuilt`): unjudged, with its
own reason and the build's last lines, never a fail of the app.

The reset plan and the page map rest on files. When the version's copies of the reset plan's
evidence files differ from those it was proven on, the plan is rehearsed again on the version's
copy before the first try (:func:`footing_on`), and not saved: the disk's plan stays as proven. A
page map whose evidence differs on the version is checked on the version's copy by the runner, not
saved either. Either is said on the run, in the version's ``notes``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy as copying
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from evalution.config import Config, ConfigError
from evalution.core.version import DISK, Version, asked, built, described, set_up, short
from evalution.isolation import build
from evalution.isolation.isolate import ComposeCopy, Copy, plan_spec
from evalution.isolation.launch import LaunchError
from evalution.isolation.plan import Plan, stale
from evalution.isolation.recipe import BuildRecipe, RecipeStore, build_env, override_text
from evalution.isolation.setup import SetupError
from evalution.project import versions
from evalution.project.building import BuiltPart, gigabytes_up, minutes, tag_of
from evalution.runs import isolated
from evalution.suite.cases import CaseError

#: The one next step when a version can't be built: the recipe is proven on Test environment.
PROVE_FIRST = "Prove how {target} is built first, on Test environment."
_HEX = re.compile(r"^[0-9a-fA-F]{4,64}$")

Said = Callable[[str], object]


class VersionRefused(CaseError):
    """A version that can't be run at all, said before anything starts, with the next step. A
    :class:`~evalution.suite.cases.CaseError`, so every caller of an estimate shows it as it shows
    a test that can't run."""


class NotBuilt(Exception):
    """Checking out or building the version failed: the run ends before any try, unjudged.

    :param version: What was asked, and the commit when it got that far.
    """

    def __init__(self, message: str, version: Version) -> None:
        super().__init__(message)
        self.version = version


def is_disk(ref: str | None) -> bool:
    return not ref or ref.strip() == DISK


def _recipe(config: Config, target: str) -> BuildRecipe | None:
    return RecipeStore(config.data_dir).load(target)


def refusal(config: Config, target: str, ref: str | None, found: isolated.Footing) -> str | None:
    """Why a run of *ref* on *target* is refused before it starts, with the one next step;
    ``None`` for what's on disk, or when it can be checked out and built."""
    if is_disk(ref):
        return None
    if not found.managed and not found.to_rehearse:
        why = found.problem or "it has no proven isolation plan yet"
        return (
            f"{ref} plays only on evalution's own copy of {target}, and {why}. Set up testing "
            "on the Test environment page first."
        )
    nxt = PROVE_FIRST.format(target=target)
    try:
        recipe = _recipe(config, target)
    except ValueError as exc:
        return f"{target}'s build recipe doesn't read ({exc}). {nxt}"
    if recipe is None:
        return f"Evalution doesn't know how to build {target} yet, so it can't build {ref}. {nxt}"
    if recipe.status != "proven":
        return f"{target}'s build recipe is {recipe.status}, not proven. {nxt}"
    assert found.plan is not None
    try:
        kind = plan_spec(config, target, found.plan).get("kind")
    except LookupError as exc:
        return f"{target} can't run {ref}: {exc}"
    if (kind == "process") != (recipe.kind == "process"):
        return (
            f"{target}'s build recipe builds a {recipe.kind.replace('_', ' ')} app, but its copy "
            f"starts as a {kind} app. {nxt}"
        )
    return None


def _versions(config: Config, target: str) -> versions.Versions:
    """:raises VersionError: No code folder, or it isn't a git checkout."""
    conf = config.targets.get(target)
    code = conf.code.path if conf is not None and conf.code is not None else None
    return versions.of(target, code, config.data_dir, parts=[BuiltPart(config.data_dir, target)])


def name_of(ref: str, commit: str | None) -> str | None:
    """The branch, tag or pull request (``pull/412``) *ref* names, as a person says it; ``None``
    when it is a commit."""
    ref = ref.strip()
    if (number := versions.pull_number(ref)) is not None:
        return versions.PULL_OURS.format(n=number)
    if commit and _HEX.match(ref) and commit.lower().startswith(ref.lower()):
        return None
    for prefix in ("refs/heads/", "refs/tags/", "refs/remotes/"):
        ref = ref.removeprefix(prefix)
    return ref


def where(name: str | None, commit: str | None) -> str:
    """ "feature-x @ a1b2c3d", or the commit alone."""
    return " @ ".join(p for p in (name, short(commit)) if p)


# Before a run: what it will do, and what that costs


def _last_build(config: Config, target: str, recipe: BuildRecipe) -> dict[str, Any]:
    """The newest build's timings: a version built here, else the recipe's proof."""
    found = sorted(
        RecipeStore(config.data_dir).built(target).values(),
        key=lambda r: -float(r.get("at") or 0),
    )
    done = [r for r in found if r.get("seconds")]
    return done[0] if done else (recipe.last_build or {})


def cost(
    config: Config,
    target: str,
    ref: str,
    name: str | None,
    commit: str | None,
    *,
    fetches: bool = False,
) -> str:
    """What running *ref* does first, and what that costs, said before Run: "Checks out
    feature-x @ a1b2c3d and builds it: about 4 min and up to 20 GB, once", or "Built already".
    A remote's branch or a pull request (*fetches*) is fetched again at Run, so its *commit* is
    only the one last seen here."""
    recipe = _recipe(config, target)
    at = where(name, commit) or ref
    untouched = " Your checkout isn't touched."
    if commit is None:
        first = f"Fetches {ref}, checks it out"
    elif fetches:
        first = f"Fetches {ref} again (last seen at {short(commit)}), checks it out"
    else:
        first = f"Checks out {at}"
    if recipe is None:
        return f"{first}." + untouched
    if commit and not fetches and tag_of(commit) in RecipeStore(config.data_dir).built(target):
        done = "Set up already" if recipe.kind == "process" else "Built already"
        return f"{done}: {at}." + untouched
    last = _last_build(config, target, recipe)
    if recipe.kind == "process":
        steps = ", ".join(" ".join(s.run) for s in recipe.setup)
        if not steps:
            return f"{first} and starts it from there." + untouched
        seconds = float(last.get("seconds") or 60)
        return f"{first} and sets it up ({steps}): {minutes(seconds)}, once." + untouched
    seconds = float(last.get("seconds") or build.FIRST_SECONDS)
    size = float(last.get("disk_bytes") or last.get("image_bytes") or build.FIRST_BYTES)
    return (
        f"{first} and builds it: {minutes(seconds)} and up to {gigabytes_up(size)}, once."
        + untouched
    )


def preview(config: Config, target: str, ref: str) -> dict[str, Any]:
    """What a run of *ref* would play, said before it starts: the ref, its commit (``None`` when
    it is a remote's branch not fetched yet), the line that says it and its cost. Reads git and
    the recipe store only: nothing is fetched, checked out or built.

    :raises VersionRefused: No such ref here, nor a remote's branch to fetch.
    """
    try:
        found = _versions(config, target)
        fetches = found.fetches(ref)
        try:
            commit: str | None = found.reach(ref, fetch=False)
        except versions.VersionError:
            if not fetches:
                raise
            commit = None
    except versions.VersionError as exc:
        raise VersionRefused(str(exc)) from exc
    name = name_of(ref, commit)
    version = Version(ref, name, commit, False, None, None, None)
    return {
        **version.as_dict(),
        "said": described(version.as_dict(), now=True),
        "cost": cost(config, target, ref, name, commit, fetches=fetches),
    }


# Checking out and building


@dataclass
class Ready:
    """A version checked out and built, and how its copy starts.

    :param folder: The target's code folder inside the worktree.
    :param spec: How its copy starts: the plan's, moved into the worktree.
    :param plan: The reset plan, its file stores moved into the worktree (a process copy).
    :param env: The recipe's variables, set on the copy.
    :param record: What was built (:class:`~evalution.isolation.build.Built`, as kept).
    :param reused: Built before, not again.
    :param notes: What the run says of it beside its version: files read from the person's
        checkout, the reset plan or page map checked on this version.
    :param held: The run's hold on the version's checkout: no other run, here or in another
        evalution, removes it while the run plays. Let go by :meth:`release` when the run ends
        (or when this is collected, or the process ends).
    """

    ref: str
    name: str | None
    commit: str
    folder: Path
    spec: dict[str, Any]
    plan: Plan
    env: dict[str, str]
    record: dict[str, Any]
    reused: bool
    recipe: BuildRecipe
    notes: list[str] = field(default_factory=list)
    held: versions.Held | None = None

    @property
    def where(self) -> str:
        return where(self.name, self.commit)

    def release(self) -> None:
        """Let go of the version's checkout: the run is over."""
        if self.held is not None:
            self.held.release()

    def unplayed(self, why: str | None = None) -> Version:
        return asked(self.ref, self.name, self.commit, why)


def _moved(path: str, top: Path, there: Path) -> Path | None:
    """*path* moved from the person's checkout (*top*) to the worktree (*there*); ``None`` when it
    isn't in the checkout."""
    found = Path(path)
    if not found.is_absolute():
        return None
    try:
        rel = found.resolve().relative_to(top)
    except ValueError:
        return None
    return there / rel


def on_version(
    spec: dict[str, Any], top: Path, there: Path, folder: Path, notes: list[str]
) -> dict[str, Any]:
    """*spec* (how a copy starts, as the plan says) moved from the person's checkout at *top* to
    the worktree at *there*, whose code folder is *folder*: compose files and a process's folder,
    command paths and roots. The env file stays the person's. What can't be moved is said in
    *notes*.

    :raises LookupError: The folder a process starts in isn't in the version.
    """
    out = copying.deepcopy(spec)
    if spec.get("kind") == "compose":
        files = []
        for raw in spec.get("files") or []:
            moved = _moved(str(raw), top, there)
            if moved is not None and moved.is_file():
                files.append(str(moved))
            else:
                files.append(str(raw))
                notes.append(f"{Path(str(raw)).name} isn't in this version: the copy reads yours.")
        out["files"] = files
        return out
    if spec.get("kind") == "process":
        cwd = str(spec.get("cwd") or folder)
        moved = _moved(cwd, top, there) if Path(cwd).is_absolute() else folder / cwd
        if moved is None:
            notes.append(f"It starts in {cwd}, outside the app's repository, as it does on disk.")
        elif not moved.is_dir():
            raise LookupError(f"{moved.relative_to(there)} isn't a folder in this version")
        else:
            out["cwd"] = str(moved)
        command = []
        for arg in spec.get("command") or []:
            found = _moved(str(arg), top, there)
            command.append(str(found) if found is not None and found.exists() else str(arg))
        out["command"] = command
        out["roots"] = [str(_moved(str(r), top, there) or r) for r in spec.get("roots") or []]
    return out


def plan_on(plan: Plan, spec: dict[str, Any], top: Path, there: Path) -> Plan:
    """*plan* with its file stores moved into the worktree, for a process copy that runs there;
    a compose copy's stores are its own containers and volumes, as they are."""
    if spec.get("kind") != "process":
        return plan
    stores = []
    for store in plan.stores:
        moved = _moved(store.locator, top, there)
        stores.append(replace(store, locator=str(moved)) if moved is not None else store)
    return replace(plan, stores=stores)


async def _still_built(recipe: BuildRecipe, record: dict[str, Any], existed: bool) -> bool:
    """Whether what *record* says was built is still there to start from: every image on this
    computer (a compose app), or the worktree its setup ran in (a process app)."""
    if recipe.kind == "process":
        return existed
    names = [str(n) for n in (record.get("images") or {}).values()]
    try:
        found = await build._sizes(names)  # noqa: SLF001 - its own module's reading of images
    except (LaunchError, OSError):
        return False
    return all(n in found for n in names)


async def prepare(
    config: Config,
    target: str,
    ref: str,
    plan: Plan,
    *,
    said: Said = lambda _line: None,
    on_step: Callable[[str], object] = lambda _step: None,
) -> Ready:
    """Check *ref* out into evalution's folder and build it with the target's proven recipe,
    unless its tag is built already; how its copy starts.

    :param said: Told each line the build prints.
    :param on_step: Told what it does now, in words: "Checking out feature-x".
    :raises NotBuilt: It couldn't be checked out or built; nothing of the person's touched.
    """
    name = name_of(ref, None)
    commit: str | None = None

    def failed(why: str) -> NotBuilt:
        return NotBuilt(why, asked(ref, name, commit, why))

    store = RecipeStore(config.data_dir)
    try:
        recipe = store.load(target)
    except ValueError as exc:
        raise failed(f"{target}'s build recipe doesn't read: {exc}") from exc
    if recipe is None:
        raise failed(f"{target} has no build recipe. {PROVE_FIRST.format(target=target)}")
    on_step(f"Checking out {ref}")
    try:
        found = await asyncio.to_thread(_versions, config, target)
        commit = await asyncio.to_thread(found.reach, ref)
        name = name_of(ref, commit)
        existed = found.path(commit).is_dir()
        # Held for the run: no other run, here or in another evalution, removes it meanwhile.
        held = await asyncio.to_thread(found.take, commit)
    except versions.VersionError as exc:
        raise failed(f"{ref} couldn't be checked out: {exc}") from exc
    try:
        ready = await _ready(config, target, ref, plan, recipe, found, held, existed, said, on_step)
    except BaseException:
        held.release()
        raise
    return ready


async def _ready(
    config: Config,
    target: str,
    ref: str,
    plan: Plan,
    recipe: BuildRecipe,
    found: versions.Versions,
    held: versions.Held,
    existed: bool,
    said: Said,
    on_step: Callable[[str], object],
) -> Ready:
    """:func:`prepare` once the version is checked out and held: built, unless it is already."""
    commit, top = held.commit, held.path
    name = name_of(ref, commit)

    def failed(why: str) -> NotBuilt:
        return NotBuilt(why, asked(ref, name, commit, why))

    store = RecipeStore(config.data_dir)
    folder = found.code_folder(commit)
    notes: list[str] = []
    try:
        spec = on_version(plan_spec(config, target, plan), found.top, top, folder, notes)
    except LookupError as exc:
        raise failed(f"{where(name, commit)} can't start: {exc}") from exc
    tag = tag_of(commit)
    record = store.built(target).get(tag)
    reused = record is not None and await _still_built(recipe, record, existed)
    if not reused:
        on_step(f"Building {where(name, commit)}")
        env_file = Path(spec["env_file"]) if spec.get("env_file") else None
        try:
            done = await build.build_version(
                recipe, folder, tag, data_dir=config.data_dir, env_file=env_file, said=said
            )
        except (build.BuildRefused, build.BuildFailed, ValueError, LaunchError, OSError) as exc:
            raise failed(f"{where(name, commit)} wasn't built. {exc}") from exc
        record = {**done.as_dict(), "commit": commit, "ref": ref}
        await asyncio.to_thread(store.record_built, target, record)
        # What it built now counts toward the limit: older versions go first, never this one
        # nor one another run holds.
        with contextlib.suppress(versions.VersionError):
            await asyncio.to_thread(found.trim, [commit])
    assert record is not None
    spec = _started_from(config, target, recipe, spec, tag)
    return Ready(
        ref=ref,
        name=name,
        commit=commit,
        folder=folder,
        spec=spec,
        plan=plan_on(plan, spec, found.top, top),
        env=build_env(recipe, tag),
        record=record,
        reused=reused,
        recipe=recipe,
        notes=notes,
        held=held,
    )


def _started_from(
    config: Config, target: str, recipe: BuildRecipe, spec: dict[str, Any], tag: str
) -> dict[str, Any]:
    """*spec* naming the version's images: a ``compose_override`` recipe's override after the
    compose files (written again when it went); ``compose_env`` names them through its
    variables, in the copy's env."""
    text = override_text(recipe, tag) if recipe.services else None
    if spec.get("kind") != "compose" or text is None:
        return spec
    path = build.override_file(config.data_dir, target, tag)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return {**spec, "files": [*spec.get("files", []), str(path)]}


# On the version's copy


async def footing_on(
    config: Config, target: str, found: isolated.Footing, ready: Ready
) -> isolated.Footing:
    """What a run of *ready* stands on: the reset plan as proven, when the files its evidence
    cites are the same in the version; else the plan rehearsed again on the version's copy before
    the first try, not saved (the disk's plan stays as it is), and said in *ready*'s notes.

    :raises NotIsolated: It didn't hold on the version.
    """
    assert found.plan is not None
    changed = await asyncio.to_thread(stale, found.plan, ready.folder)
    if not changed:
        return isolated.Footing("proven", ready.plan, found.version)
    files = ", ".join(changed)
    try:
        rehearsed = await isolated.rehearse_plan(
            config,
            target,
            copying.deepcopy(ready.plan),
            slot=0,
            spec=ready.spec,
            env=ready.env,
            folder=ready.folder,
        )
        held = rehearsed.status == "proven"
        why = rehearsed.report.rsplit("\n", 1)[-1] if not held else ""
    except (LookupError, LaunchError, SetupError, ConfigError) as exc:
        held, why = False, str(exc)
    if not held:
        raise isolated.NotIsolated(
            f"The reset plan was proven on another version, and doesn't hold on {ready.where} "
            f"({files} differ): {why or 'its rehearsal failed'}. Prove the plan again on the "
            "Test environment page with that version checked out, or run what's on disk."
        )
    ready.notes.append(
        f"The reset plan was proven on another version ({files} differ in {ready.where}): "
        "rehearsed again on this version's copy before the first try, and it held."
    )
    return isolated.Footing("proven", ready.plan, found.version)


async def played(ready: Ready, copy: Copy) -> Version:
    """What a run of *ready* played: the images its copy runs, vouched for as evalution's build
    of the commit; or, on a process copy, the commit's tree in the worktree."""
    if not isinstance(copy, ComposeCopy):
        found = set_up(ready.ref, ready.name, ready.commit, str(ready.folder))
    else:
        try:
            images = await copy.images()
        except (LaunchError, OSError, ValueError) as exc:
            return replace(
                built(ready.ref, ready.name, ready.commit, []),
                verified=None,
                why_unverified=f"evalution couldn't read the copy's images: {exc}",
            )
        found = built(ready.ref, ready.name, ready.commit, images, ready.record.get("ids") or {})
    return found


def version_dict(version: Version, notes: list[str]) -> dict[str, Any]:
    """The run's ``version`` block, with what was said of it."""
    return {**version.as_dict(), "notes": list(notes)}
