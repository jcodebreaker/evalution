"""A plan for resetting an app's stores, kept as data (ADR 0009, phase 1).

The code read (:mod:`.discover`) names many candidates; the probe (:mod:`.observe` and
:mod:`.containers`) says which ones the app really touched. :func:`draft_from` turns both into a
first plan with no model: one :class:`PlannedStore` per real store, the settings that move it, a
strategy chosen by rule, and the trace tables to leave alone. The plan cites the files its
evidence comes from, and keeps their hashes, so :func:`stale` can say when the code under it moved.

:class:`PlanStore` keeps a target's plan as a plain JSON file in evalution's data folder, every
saved version beside it, so a plan can be undone.

Strategies, the best one each store allows:

* ``relocate``: a setting (``moved_by``) or the compose file points the store at a copy of its
  starting state: a database made from a template, a fresh volume. For an in-process store
  (kind ``memory``) it means a restart clears it: the process is the store, and a new one starts
  empty.
* ``snapshot``: the store is a path on this computer; it is kept and put back in place, with the
  app stopped.
* ``identity``: nothing can reset it, but it is keyed by person, so each try uses its own.
* ``none``: hosted, or nothing above applies. Results say the store isn't isolated.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from evalution.isolation.containers import Found
from evalution.isolation.discover import Store
from evalution.isolation.observe import Observation

#: The shape of a saved plan; a newer one is refused rather than misread.
FORMAT = 1
#: Saved versions kept per target, the oldest dropped first.
VERSIONS_KEPT = 20

Strategy = Literal["relocate", "snapshot", "identity", "none"]
Status = Literal["draft", "proven", "stale", "failed"]

#: Stores reached over a socket, where the database (not a file) is the store.
_SERVED = {"postgres", "mysql", "redis", "mongo", "vector"}
#: Stores kept in files on this computer.
_FILES = {"sqlite", "dir", "vector"}
#: Locators that are settings or names, not a place.
_NAMES = ("env:", "service:", "package:", "volume:", "socket:")
#: What :func:`evalution.isolation.containers.reconcile` cites for a store it found.
_CONTAINER_NOTE = "found inside a container, by the probe"
#: What :func:`evalution.isolation.observe.reconcile` cites for a file no code names.
_UNNAMED = "not named anywhere in the code"
#: A table or schema of traces: kept as evidence of what happened, never reset.
#: A table, schema or dotted ``db.schema.table`` name that holds traces.
TRACES = re.compile(r"(^|[._])(spans?|traces?|otel|opentelemetry|telemetry)([._]|$)", re.IGNORECASE)
_TRACES = TRACES
#: What a store keyed by person is keyed by.
_PERSON = re.compile(r"user|person|account|member|customer|owner|identity|profile", re.IGNORECASE)
_SIDECAR = re.compile(r"-(wal|shm|journal)$")
_CITED = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<line>\d+)$")
_TARGET = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class PlannedStore:
    """One real store and how it is reset.

    :param locator: Where it is: ``container:database``, ``volume:NAME`` (as the compose file names
        it), an absolute path, or the setting that names it when nothing more is known.
    :param moved_by: Env names whose value points the app at it.
    :param keyed_by: What the store is keyed by (a person, a session), when known.
    :param evidence: ``path:line`` in the project, and notes on how it was found.
    :param seen: What the probe showed of it.
    :param found_as: The code read's and the probe's names merged into this one store.
    """

    id: str
    kind: str
    locator: str
    moved_by: list[str] = field(default_factory=list)
    keyed_by: str | None = None
    strategy: Strategy = "none"
    evidence: list[str] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)
    found_as: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """What to reset between tries, what to leave alone, and how far it has been proven.

    :param exclude: ``database.schema.table`` (or a path) found holding the probe that is traces,
        kept as evidence and never reset.
    :param launch: How evalution starts its own copy of the app: ``{"kind": "compose" |
        "process", ...}``. Opaque here; :mod:`evalution.isolation.launch` reads it.
    :param report: The last rehearsal's account, or the draft's, in plain words.
    :param evidence_hashes: ``path -> sha256`` of each project file the evidence cites.
    :param leaks: Where the last rehearsal found the canary, or a write, outside the plan.
    """

    target: str
    stores: list[PlannedStore] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    launch: dict[str, Any] = field(default_factory=dict)
    status: Status = "draft"
    report: str = ""
    evidence_hashes: dict[str, str] = field(default_factory=dict)
    leaks: list[str] = field(default_factory=list)
    #: The files a commit changed under the evidence, when the plan was marked ``stale``.
    stale_files: list[str] = field(default_factory=list)
    #: The app keeps state in its own memory (a store of kind ``memory``): only a restart clears
    #: it, so every put back stops the whole copy.
    app_holds_state: bool = False
    #: Whether the rehearsal proved the copy can be put back with the app up (or only the
    #: services a store needs stopped): ``proven``, ``failed`` (it needs a restart), or ``None``
    #: when it wasn't tried. A plan saved before this was kept reads ``None``: today's restart.
    hot: str | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({"version": FORMAT, **asdict(self)}, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Plan:
        """
        :raises ValueError: Not a plan, or one saved by a newer evalution.
        """
        data = json.loads(text)
        if not isinstance(data, dict) or "target" not in data:
            raise ValueError("not an isolation plan")
        version = data.pop("version", FORMAT)
        if not isinstance(version, int) or version > FORMAT:
            raise ValueError(f"isolation plan format {version} is newer than this evalution")
        stores = [PlannedStore(**s) for s in data.pop("stores", [])]
        return cls(**data, stores=stores)


#: Strategies under which evalution's copy gets a store of its own, put back between tries.
GIVEN = ("relocate", "snapshot")
#: Prefixes of a locator that is a name, not a place.
_NAMED = ("volume:", "env:", "service:", "package:", "socket:")


#: Kinds of database in a container a driver puts back.
PUT_BACK = ("postgres",)
#: Kinds kept in a process's memory, which only a restart clears: the app's own, and a database
#: in a container that keeps nothing on disk (one that does has its volume planned too).
RESTARTED = ("memory", "redis")
_IN_CONTAINER = re.compile(r"^[\w.-]+:[\w$-]+$")


def in_container(locator: str) -> bool:
    """Whether *locator* names a database in a container (``container:db``), not a setting, a
    volume or a path."""
    return not locator.startswith(_NAMES) and bool(_IN_CONTAINER.match(locator))


def holds_state(plan: Plan) -> bool:
    """Whether *plan*'s app keeps state in memory: said on the plan, or one of its stores is the
    process's memory or a database that keeps nothing on disk, which only a restart clears."""
    return plan.app_holds_state or any(s.kind in RESTARTED for s in plan.stores)


def not_put_back(store: PlannedStore) -> bool:
    """Whether nothing resets *store* between tries: hosted, kept by person only, with no
    strategy, or a database in a container of a kind no driver puts back nor a restart clears."""
    if store.strategy in ("identity", "none") or store.kind == "hosted":
        return True
    return in_container(store.locator) and store.kind not in (*PUT_BACK, *RESTARTED)


def given(plan: Plan) -> list[PlannedStore]:
    """The stores evalution's copy of the app gets its own of: each one the plan puts back
    (relocated or snapshotted), never a hosted one."""
    return [s for s in plan.stores if s.strategy in GIVEN and s.kind != "hosted"]


def own_name(store: PlannedStore) -> str:
    """The store's own name: the database, the volume, the file, or the setting."""
    locator = store.locator
    for prefix in _NAMED:
        if locator.startswith(prefix):
            return locator.removeprefix(prefix)
    if locator.startswith("/"):
        return locator.rstrip("/").rsplit("/", 1)[-1] or locator
    return locator.split(":", 1)[-1]


def strategy_for(
    kind: str, locator: str, moved_by: list[str], keyed_by: str | None, compose: bool
) -> Strategy:
    """The best reset a store allows, by rule (see the module's docstring)."""
    if kind == "hosted":
        return "identity" if keyed_by and _PERSON.search(keyed_by) else "none"
    if kind == "memory" or moved_by or compose:
        return "relocate"
    if locator.startswith("/"):
        return "snapshot"
    if keyed_by and _PERSON.search(keyed_by):
        return "identity"
    return "none"


def _add(into: list[str], items: list[str]) -> None:
    for item in items:
        if item not in into:
            into.append(item)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "store"


class _Builder:
    """Planned stores as they are merged, each with a unique id."""

    def __init__(self) -> None:
        self.stores: list[PlannedStore] = []
        self.compose: set[str] = set()

    def get(self, kind: str, locator: str, name: str) -> PlannedStore:
        for s in self.stores:
            if s.kind == kind and s.locator == locator:
                return s
        ids = {s.id for s in self.stores}
        base = f"{kind}-{_slug(name)}"
        sid = next(i for i in (base, *(f"{base}-{n}" for n in range(2, 1000))) if i not in ids)
        store = PlannedStore(sid, kind, locator)
        self.stores.append(store)
        return store

    def merge(self, into: PlannedStore, found: Store) -> None:
        _add(into.evidence, found.evidence)
        _add(into.seen, found.seen)
        _add(into.found_as, [found.locator])
        if found.locator.startswith("env:"):
            _add(into.moved_by, [found.locator.removeprefix("env:")])
        if found.locator.startswith(("service:", "volume:")):
            self.compose.add(into.id)


def _container_finds(stores: list[Store], found: list[Found] | None) -> dict[str, Found]:
    """``locator -> Found`` for the stores :func:`containers.reconcile` added."""
    if found is not None:
        return {f"{f.container}:{f.where}": f for f in found}
    out: dict[str, Found] = {}
    for s in stores:
        if _CONTAINER_NOTE in s.evidence and ":" in s.locator:
            container, where = s.locator.split(":", 1)
            out[s.locator] = Found(container, s.kind, where)
    return out


def _host_path(locator: str, folder: Path) -> str:
    path = Path(locator.removeprefix("sqlite:///")).expanduser()
    return str((path if path.is_absolute() else folder / path).resolve())


def _traces(finds: list[Found]) -> set[str]:
    """``db.schema`` holding a trace table: every table found in it is traces too."""
    schemas = set()
    for f in finds:
        db, _, rest = f.where.partition(".")
        schema, _, table = rest.rpartition(".")
        if _TRACES.search(table) or (schema and _TRACES.search(schema)):
            schemas.add(f"{db}.{schema}" if schema else f"{db}.{table}")
    return schemas


def draft_from(
    folder: Path,
    stores: list[Store],
    observation: Observation | None = None,
    found: list[Found] | None = None,
    *,
    target: str = "",
) -> Plan:
    """The first plan, by rule and with no model, from what the code read and the probe found.

    *stores* are :func:`.discover.scan`'s, after :func:`.observe.reconcile` and
    :func:`.containers.reconcile`, so each carries what the probe saw of it. *found* is what
    :func:`.containers.search` returned (told apart from *stores* by their locators when left
    out). *observation* only adds what was watched to the report.

    * A store the probe touched is kept; a literal it never saw (a fallback, an example in a test)
      is dropped.
    * Every name of one real store becomes one :class:`PlannedStore`: the env names and the
      compose service of a database the probe found in a container, a compose volume and what
      was found under it, a SQLite file and its sidecars, the files written in one folder.
    * Env names of a kind attach to the store of that kind only when there is one; with several,
      which moves which is left for the model, and the report says so.
    * A table of traces, and the rest of its schema, goes in ``exclude``.
    * Hosted and in-process stores are kept unconfirmed, since no probe can see them.
    """
    folder = folder.resolve()
    finds = _container_finds(stores, found)
    plan = Plan(target=target)
    build = _Builder()
    notes: list[str] = []

    # Databases and volumes found inside containers: one store per database, one per volume.
    projects = {f.container.rsplit("-", 2)[0] for f in finds.values()}
    trace_schemas = _traces([f for f in finds.values() if f.kind not in ("volume", "bind")])
    by_locator = {s.locator: s for s in stores}
    for locator, f in finds.items():
        origin = by_locator.get(locator) or Store(f.kind, locator, [_CONTAINER_NOTE])
        if f.kind == "volume":
            volume = f.where.split("/", 1)[0].removeprefix("volume:")
            short = next(
                (volume.removeprefix(f"{p}_") for p in projects if volume.startswith(f"{p}_")),
                volume,
            )
            store = build.get("volume", f"volume:{short}", short)
            build.compose.add(store.id)
            build.merge(store, origin)
            for s in stores:
                if s.locator in (f"volume:{volume}", f"volume:{short}"):
                    build.merge(store, s)
        elif f.kind == "bind":
            path = str(Path(f.where).parent)  # the folder of the file that held the canary
            build.merge(build.get("dir", path, Path(path).name), origin)
        else:
            db, _, rest = f.where.partition(".")
            schema = rest.rpartition(".")[0]
            if f"{db}.{schema}" in trace_schemas or f"{db}.{rest}" in trace_schemas:
                _add(plan.exclude, [f.where])
                continue
            store = build.get(f.kind, f"{f.container}:{db}", db)
            build.compose.add(store.id)
            build.merge(store, origin)

    rest = [s for s in stores if s.locator not in finds]
    # Files on this computer the probe saw: one per file, sidecars and folders merged.
    for s in rest:
        if s.kind not in _FILES or s.locator.startswith(_NAMES) or not s.seen:
            continue
        path = _SIDECAR.sub("", _host_path(s.locator, folder))
        if s.kind == "dir" and s.evidence == [_UNNAMED] and Path(path).parent != folder:
            path = str(Path(path).parent)  # files written in a folder: the folder is the store
        build.merge(build.get(s.kind, path, Path(path).name), s)

    # Databases reached over a socket from this computer, with no container to name them.
    for kind in sorted({s.kind for s in rest if s.kind in _SERVED and s.seen}):
        if any(p.kind == kind for p in build.stores):
            continue
        named = [s for s in rest if s.kind == kind and s.seen and s.locator.startswith(_NAMES)]
        if not named:
            continue
        sockets = [n.removeprefix("connected: ") for s in named for n in s.seen if "connected" in n]
        locator = sockets[0] if sockets else named[0].locator
        build.get(kind, locator, kind)

    # Settings and services that move or name a kept store, merged into it when it is the only one.
    for s in rest:
        if not s.locator.startswith(("env:", "service:", "package:", "socket:")):
            continue
        same = [p for p in build.stores if p.kind == s.kind]
        if s.locator.startswith("package:") and not s.seen:
            continue
        if len(same) == 1:
            build.merge(same[0], s)
        elif len(same) > 1 and s.locator.startswith("env:"):
            notes.append(f"{s.locator} could move any of {len(same)} {s.kind} stores; left out.")

    # Stores no probe can see: hosted ones and the process's own memory.
    for s in rest:
        if s.kind in ("hosted", "memory"):
            build.merge(build.get(s.kind, s.locator, s.locator.split(":")[-1]), s)

    for p in build.stores:
        p.strategy = strategy_for(p.kind, p.locator, p.moved_by, p.keyed_by, p.id in build.compose)
    plan.stores = build.stores
    plan.app_holds_state = holds_state(plan)
    plan.launch = (
        {"kind": "compose", "project": sorted(projects)[0]} if projects else {"kind": "process"}
    )
    kept = {loc for p in plan.stores for loc in p.found_as}
    dropped = [s for s in stores if s.locator not in kept and s.locator not in finds]
    unseen_settings = [s.locator for s in dropped if s.locator.startswith(("env:", "service:"))]
    plan.evidence_hashes = hashes(folder, [e for p in plan.stores for e in p.evidence])
    plan.report = _report(plan, len(dropped), unseen_settings, notes, observation)
    return plan


def _report(
    plan: Plan,
    dropped: int,
    unseen_settings: list[str],
    notes: list[str],
    observation: Observation | None,
) -> str:
    lines = [f"Drafted by rule from the code read and the probe: {len(plan.stores)} store(s)."]
    for p in plan.stores:
        moved = f", moved by {', '.join(p.moved_by)}" if p.moved_by else ""
        confirmed = "" if p.seen else " (not seen by the probe)"
        lines.append(f"- {p.id}: {p.strategy}, {p.locator}{moved}{confirmed}")
    if plan.exclude:
        lines.append(f"Left alone as traces: {', '.join(plan.exclude)}.")
    if dropped:
        lines.append(f"Dropped {dropped} name(s) the probe never reached.")
    if unseen_settings:
        lines.append(f"Settings not tied to a store yet: {', '.join(unseen_settings)}.")
    lines += notes
    if observation is not None:
        lines.append(f"Watched on this computer: {', '.join(observation.roots) or 'nothing'}.")
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cited(folder: Path, evidence: list[str]) -> list[str]:
    """The project files *evidence* cites (``path:line``), relative to *folder*."""
    out: list[str] = []
    root = folder.resolve()
    for item in evidence:
        if (m := _CITED.match(item)) is None:
            continue
        path = (root / m["path"]).resolve()
        if path.is_relative_to(root) and path.is_file():
            _add(out, [path.relative_to(root).as_posix()])
    return out


def hashes(folder: Path, evidence: list[str]) -> dict[str, str]:
    """``path -> sha256`` of each project file *evidence* cites."""
    return {rel: _sha256(folder / rel) for rel in sorted(cited(folder, evidence))}


def stale(plan: Plan, folder: Path) -> list[str]:
    """The files the plan's evidence cites that changed or are gone since it was drafted."""
    out = []
    for rel, digest in sorted(plan.evidence_hashes.items()):
        path = folder / rel
        if not path.is_file() or _sha256(path) != digest:
            out.append(rel)
    return out


class PlanStore:
    """Each target's plan as ``<data_dir>/isolation/<target>/plan.json``, with every saved
    version in ``versions/<n>.json`` beside it (the newest :data:`VERSIONS_KEPT`)."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "isolation"

    def _dir(self, target: str) -> Path:
        if not _TARGET.match(target):
            raise ValueError(f"not a target name: {target!r}")
        return self.root / target

    def _versions(self, target: str) -> list[tuple[int, Path]]:
        folder = self._dir(target) / "versions"
        if not folder.is_dir():
            return []
        found = [(int(p.stem), p) for p in folder.glob("*.json") if p.stem.isdigit()]
        return sorted(found)

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def load(self, target: str) -> Plan | None:
        """The target's current plan; ``None`` when it has none.

        :raises ValueError: A file that isn't a plan this evalution reads.
        """
        path = self._dir(target) / "plan.json"
        return Plan.from_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save(self, plan: Plan) -> int:
        """Store *plan* as the target's current one and as a new version; its number."""
        plan.updated = time.time()
        kept = self._versions(plan.target)
        number = kept[-1][0] + 1 if kept else 1
        text = plan.to_json()
        self._write(self._dir(plan.target) / "versions" / f"{number}.json", text)
        self._write(self._dir(plan.target) / "plan.json", text)
        for _, old in kept[: max(0, len(kept) + 1 - VERSIONS_KEPT)]:
            old.unlink()
        return number

    def version(self, target: str) -> int | None:
        """The number of the target's current plan, as :meth:`save` gave it; ``None`` for none."""
        kept = self._versions(target)
        return kept[-1][0] if kept else None

    def save_evidence(self, target: str, data: dict[str, Any]) -> Path:
        """Keep what the last probe found for *target* (``evidence.json`` beside its plan): the
        candidates with what was seen of them, the finds inside containers, and the paths
        watched. Never a setting's value."""
        path = self._dir(target) / "evidence.json"
        self._write(path, json.dumps(data, indent=2, default=str) + "\n")
        return path

    def load_evidence(self, target: str) -> dict[str, Any] | None:
        """What :meth:`save_evidence` kept; ``None`` when nothing is."""
        path = self._dir(target) / "evidence.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def history(self, target: str) -> list[Plan]:
        """Every kept version, the oldest first; the last is the current plan."""
        return [Plan.from_json(p.read_text(encoding="utf-8")) for _, p in self._versions(target)]

    def undo(self, target: str) -> Plan | None:
        """Drop the current version and go back to the one before; ``None`` when none is left.

        :raises LookupError: The target has no plan.
        """
        kept = self._versions(target)
        if not kept:
            raise LookupError(f"{target} has no isolation plan")
        kept.pop()[1].unlink()
        current = self._dir(target) / "plan.json"
        if not kept:
            current.unlink(missing_ok=True)
            return None
        text = kept[-1][1].read_text(encoding="utf-8")
        self._write(current, text)
        return Plan.from_json(text)
