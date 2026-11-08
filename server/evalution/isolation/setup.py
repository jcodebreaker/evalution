"""How a target's tests run on evalution's own copy of the app, as the person set it (ADR 0009).

A :class:`Setup` is what the page's "Test setup" panel changes: the test person the copy's
adapter plays as, what the copy starts with (nothing, or sample data loaded from a ``.sql`` file in
the app's folder), when the copy is put back (before every try, or before every test), and how many
copies play side by side.

:class:`SetupStore` keeps it as ``<data_dir>/isolation/<target>/setup.json``, with every saved
version in ``setup-versions/<n>.json`` beside it, the way :class:`.plan.PlanStore` keeps a plan.

A change to what the copy starts with changes the starting state the plan was proven on, so
:meth:`SetupStore.save` marks a proven plan ``stale`` (a new version of it), and it is rehearsed
again before the next run. The person, the reset and the copies don't change what is put back.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from evalution.config import Config
from evalution.isolation.plan import PlanStore

#: The shape of a saved setup; a newer one is refused rather than misread.
FORMAT = 1
#: Saved versions kept per target, the oldest dropped first.
VERSIONS_KEPT = 20
#: The most copies a run plays on side by side.
MOST_COPIES = 3
#: The longest a test person's name, language or time zone may be.
LONGEST = 80
#: What a test person has, in the order the page shows it.
PERSON_FIELDS = ("name", "language", "timezone")
#: What a stale plan's report says when the setup changed what the copy starts with.
START_CHANGED = {
    "seed": "Setup changed: starts with sample data",
    "empty": "Setup changed: starts empty",
}

Reset = Literal["try", "test"]
StartKind = Literal["empty", "seed"]
RESETS = ("try", "test")
_TARGET = re.compile(r"^[A-Za-z0-9_-]+$")
STARTS = ("empty", "seed")


class SetupError(ValueError):
    """A change to a setup that isn't one: the message says what, in words for the page."""


def default_copies(max_parallel: int) -> int:
    """The copies a target plays on until the person says: its ``max_parallel``, 1 to 3."""
    return max(1, min(MOST_COPIES, int(max_parallel or 1)))


def _person() -> dict[str, Any]:
    return {"name": "", "language": "", "timezone": "", "new_every_try": True}


def _start() -> dict[str, Any]:
    return {"kind": "empty", "file": None}


@dataclass
class Setup:
    """One target's test setup (see the module's docstring).

    :param person: ``{"name", "language", "timezone", "new_every_try"}``; empty strings leave
        each to the app.
    :param start: ``{"kind": "empty" | "seed", "file": path in the app's folder | None}``.
    :param reset: ``try``: the copy is put back before every try. ``test``: before each test's
        first try only.
    :param copies: Copies of the app a run plays on side by side, 1 to 3.
    :param version: The saved version's number; 0 for one never saved.
    :param updated: When it was saved; ``None`` for one never saved.
    """

    person: dict[str, Any] = field(default_factory=_person)
    start: dict[str, Any] = field(default_factory=_start)
    reset: Reset = "try"
    copies: int = 1
    version: int = 0
    updated: float | None = None

    @property
    def seeded(self) -> bool:
        """The copy starts with sample data."""
        return self.start.get("kind") == "seed" and bool(self.start.get("file"))

    def view(self) -> dict[str, Any]:
        return {
            "person": dict(self.person),
            "start": dict(self.start),
            "reset": self.reset,
            "copies": self.copies,
            "version": self.version,
            "updated": self.updated,
        }

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("version")
        return json.dumps({"format": FORMAT, **data}, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str, version: int = 0) -> Setup:
        """
        :raises ValueError: Not a setup, or one saved by a newer evalution.
        """
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not a test setup")
        shape = data.pop("format", FORMAT)
        if not isinstance(shape, int) or shape > FORMAT:
            raise ValueError(f"test setup format {shape} is newer than this evalution")
        made = cls()
        person = data.get("person")
        if isinstance(person, dict):
            made.person.update({k: v for k, v in person.items() if k in made.person})
        start = data.get("start")
        if isinstance(start, dict):
            made.start.update({k: v for k, v in start.items() if k in made.start})
        if data.get("reset") in RESETS:
            made.reset = data["reset"]
        copies = data.get("copies")
        if isinstance(copies, int) and not isinstance(copies, bool):
            made.copies = max(1, min(MOST_COPIES, copies))
        updated = data.get("updated")
        made.updated = float(updated) if isinstance(updated, int | float) else None
        made.version = version
        return made


# Changes -----------------------------------------------------------------------------------------


def _text(where: str, value: Any) -> str:
    if not isinstance(value, str):
        raise SetupError(f"{where}: must be text")
    value = value.strip()
    if len(value) > LONGEST:
        raise SetupError(f"{where}: at most {LONGEST} characters")
    return value


def seed_file(folder: Path | None, file: Any) -> str:
    """*file* as kept: a ``.sql`` file inside the app's *folder*, relative to it.

    :raises SetupError: No folder, or *file* isn't a ``.sql`` file in it.
    """
    if folder is None:
        raise SetupError("start: say where the app's code is first; sample data is read from it")
    if not isinstance(file, str) or not file.strip():
        raise SetupError("start.file: name the .sql file to load")
    root = folder.resolve()
    path = Path(file.strip())
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise SetupError("start.file: must be inside the app's folder")
    if path.suffix.lower() != ".sql":
        raise SetupError("start.file: must be a .sql file")
    if not path.is_file():
        raise SetupError(f"start.file: {path.relative_to(root).as_posix()} isn't there")
    return path.relative_to(root).as_posix()


def changed(setup: Setup, body: Any, *, folder: Path | None = None, seed: bool = True) -> Setup:
    """*setup* with *body*'s changes, every one checked; *setup* itself is left as it is.

    :param body: Any of ``person`` (any of its fields), ``start``, ``reset``, ``copies``.
    :param folder: The app's code folder, where a seed file must be.
    :param seed: Whether sample data can be loaded at all (the plan has a Postgres store).
    :raises SetupError: Something in *body* isn't a change a setup takes.
    """
    if not isinstance(body, dict):
        raise SetupError("send the changes as an object")
    unknown = sorted(set(body) - {"person", "start", "reset", "copies"})
    if unknown:
        raise SetupError(f"not part of the test setup: {', '.join(unknown)}")
    made = Setup.from_json(setup.to_json(), setup.version)
    if "person" in body:
        person = body["person"]
        if not isinstance(person, dict):
            raise SetupError("person: must be an object")
        extra = sorted(set(person) - {*PERSON_FIELDS, "new_every_try"})
        if extra:
            raise SetupError(f"person: no field {', '.join(extra)}")
        for key in PERSON_FIELDS:
            if key in person:
                made.person[key] = _text(f"person.{key}", person[key])
        if "new_every_try" in person:
            if not isinstance(person["new_every_try"], bool):
                raise SetupError("person.new_every_try: must be true or false")
            made.person["new_every_try"] = person["new_every_try"]
    if "start" in body:
        start = body["start"]
        if not isinstance(start, dict) or start.get("kind") not in STARTS:
            raise SetupError("start.kind: must be empty or seed")
        if start["kind"] == "empty":
            made.start = {"kind": "empty", "file": None}
        else:
            if not seed:
                raise SetupError("start: sample data needs a Postgres store the copy gets")
            made.start = {"kind": "seed", "file": seed_file(folder, start.get("file"))}
    if "reset" in body:
        if body["reset"] not in RESETS:
            raise SetupError("reset: must be try or test")
        made.reset = body["reset"]
    if "copies" in body:
        copies = body["copies"]
        if isinstance(copies, bool) or not isinstance(copies, int):
            raise SetupError("copies: must be a whole number")
        if not 1 <= copies <= MOST_COPIES:
            raise SetupError(f"copies: from 1 to {MOST_COPIES}")
        made.copies = copies
    return made


def start_changed(before: Setup, after: Setup) -> bool:
    """Whether *after* starts the copy from another state than *before* does."""
    return before.start != after.start


# Keeping it --------------------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    """*text* into *path* whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class SetupStore:
    """Each target's setup as ``<data_dir>/isolation/<target>/setup.json``, with every saved
    version in ``setup-versions/<n>.json`` beside it (the newest :data:`VERSIONS_KEPT`)."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.plans = PlanStore(data_dir)

    def _dir(self, target: str) -> Path:
        if not _TARGET.match(target):
            raise ValueError(f"not a target name: {target!r}")
        return self.plans.root / target

    def _versions(self, target: str) -> list[tuple[int, Path]]:
        folder = self._dir(target) / "setup-versions"
        if not folder.is_dir():
            return []
        return sorted((int(p.stem), p) for p in folder.glob("*.json") if p.stem.isdigit())

    def load(self, target: str, *, copies: int = 1) -> Setup:
        """The target's setup; the defaults, with *copies* copies, when it has none saved.

        :raises ValueError: A file that isn't a setup this evalution reads.
        """
        path = self._dir(target) / "setup.json"
        kept = self._versions(target)
        if not path.is_file():
            return Setup(copies=default_copies(copies))
        return Setup.from_json(path.read_text(encoding="utf-8"), kept[-1][0] if kept else 0)

    def save(self, target: str, setup: Setup, *, before: Setup | None = None) -> Setup:
        """Keep *setup* as the target's current one and as a new version, and mark a proven plan
        ``stale`` when it starts the copy from another state than *before* did. What was saved,
        with its version and time."""
        setup.updated = time.time()
        kept = self._versions(target)
        number = kept[-1][0] + 1 if kept else 1
        text = setup.to_json()
        _write(self._dir(target) / "setup-versions" / f"{number}.json", text)
        _write(self._dir(target) / "setup.json", text)
        for _, old in kept[: max(0, len(kept) + 1 - VERSIONS_KEPT)]:
            old.unlink()
        setup.version = number
        if before is not None and start_changed(before, setup):
            self._stale(target, setup)
        return setup

    def _stale(self, target: str, setup: Setup) -> None:
        plan = self.plans.load(target)
        if plan is None or plan.status not in ("proven", "stale"):
            return
        plan.status = "stale"
        kind = "seed" if setup.seeded else "empty"
        plan.report = f"{plan.report}\n\n{START_CHANGED[kind]}."
        self.plans.save(plan)

    def history(self, target: str) -> list[Setup]:
        """Every kept version, the oldest first."""
        kept = self._versions(target)
        return [Setup.from_json(p.read_text(encoding="utf-8"), n) for n, p in kept]


def setup_for(config: Config, target: str) -> Setup:
    """*target*'s setup as a run or a rehearsal reads it: the defaults when none is saved, or
    when the saved one doesn't read."""
    conf = config.targets.get(target)
    copies = conf.max_parallel if conf is not None else 1
    try:
        return SetupStore(config.data_dir).load(target, copies=copies)
    except ValueError:
        return Setup(copies=default_copies(copies))


def seed_path(config: Config, target: str, setup: Setup) -> Path | None:
    """The ``.sql`` file the copy starts with, when *setup* says sample data; else ``None``.

    :raises SetupError: It says sample data, and the file isn't a ``.sql`` file in the app's
        folder (any more).
    """
    if not setup.seeded:
        return None
    conf = config.targets.get(target)
    folder = conf.code.path if conf is not None and conf.code is not None else None
    rel = seed_file(folder, setup.start.get("file"))
    assert folder is not None
    return folder.resolve() / rel


def person_name(setup: Setup) -> str | None:
    """The test person's name; ``None`` when the app's own is used."""
    return str(setup.person.get("name") or "").strip() or None
