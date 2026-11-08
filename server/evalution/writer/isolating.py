"""The test writer's part in isolation (ADR 0009, phase 4): reading what was found, proposing a
change to the plan as data, and asking for a rehearsal once the admin approved it.

The model never acts on the app. It reads the evidence (:func:`evidence`), proposes edits that
are checked against it (:func:`propose`), and an approved proposal is rehearsed by deterministic
code (:func:`rehearse_plan`). Every edit has to rest on something found:

* a store is added only when a code-read candidate or a probe finding supports it, cited by id;
* a setting in ``moved_by`` must be one the code read found;
* an ``exclude`` entry must be something the probe found;
* a store the probe found holding the canary is never removed, and a trace table stays excluded.

A proposal is kept on the session as a draft until the admin approves it; the saved plan is
untouched until a rehearsal proves the proposal, which is then saved as a new version (so it can
be undone). A failed rehearsal stays on the session, its leaks become evidence for the next
proposal, and after :data:`MAX_FAILED` failed rehearsals in one session no more are run: the
admin is asked.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evalution.isolation import discover
from evalution.isolation.plan import Plan, PlannedStore, PlanStore, hashes, stale, strategy_for

if TYPE_CHECKING:
    from evalution.config import Config
    from evalution.targets import Targets

#: Failed rehearsals one session may run for a target; then the admin is asked.
MAX_FAILED = 3
#: Code-read candidates shown at most: real code names many fallbacks.
CANDIDATES_SHOWN = 60
#: Store kinds a proposal may name (:class:`evalution.isolation.discover.Store`).
KINDS = (
    "postgres",
    "mysql",
    "sqlite",
    "redis",
    "mongo",
    "vector",
    "dir",
    "volume",
    "hosted",
    "memory",
)
STRATEGIES = ("relocate", "snapshot", "identity", "none")
OPS = ("set", "add", "remove", "exclude", "include")
#: A table or schema of traces: evidence of what happened, never reset.
_TRACES = re.compile(r"(^|[._])(spans?|traces?|otel|opentelemetry|telemetry)([._]|$)", re.I)
_SETTING = re.compile(r"^[A-Z][A-Z0-9_]{1,80}$")
_STORE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,60}$")
_PREFIXES = ("env:", "service:", "package:", "volume:", "socket:", "sqlite:///")
#: Where the rehearsal says what leaked (:meth:`evalution.isolation.rehearse.Rehearsal.report`).
_LEAK_LINE = re.compile(r"^(NO\s|Not proven)")


async def rehearse_plan(config: Config, target: str, plan: Plan) -> Plan:
    """Rehearse *plan* on evalution's own copy of *target*: the plan back, ``proven`` or
    ``failed``, with the rehearsal's report. Imported when first used, so the writer loads
    without it."""
    from evalution.isolation.isolate import rehearse_plan as run

    return await run(config, target, plan)


@dataclass
class Evidence:
    """What a proposal may rest on.

    :param candidates: What the code read named: ``{"id": "c1", "kind", "locator", "evidence",
        "in_plan"}``.
    :param findings: What the probe or a rehearsal found: ``{"id": "p1", "what", "store"}``.
    :param settings: The env names the code read found.
    """

    candidates: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    settings: set[str] = field(default_factory=set)

    def find(self, cite: str) -> dict[str, Any] | None:
        return next((e for e in [*self.candidates, *self.findings] if e["id"] == cite), None)


def state(session: dict[str, Any]) -> dict[str, Any]:
    """This session's isolation work on its target: proposals, rehearsals, failures."""
    found = session.setdefault("isolation", {})
    return found.setdefault(
        str(session["target"]), {"proposals": {}, "latest": None, "rehearsals": [], "failed": 0}
    )


def _folder(targets: Targets, target: str) -> Path | None:
    conf = targets.conf(target).code
    return conf.path if conf else None


def _store(config: Config) -> PlanStore:
    return PlanStore(config.data_dir)


def evidence(plan: Plan | None, scanned: list[discover.Store], work: dict[str, Any]) -> Evidence:
    """The candidates and findings a proposal may cite, from the saved *plan*, the code read
    *scanned*, and this session's failed rehearsals. Ids follow that order, so they hold for the
    session while the saved plan stays as it is."""
    found = Evidence()
    stores = plan.stores if plan else []
    owner = {loc: s.id for s in stores for loc in s.found_as}
    rows: dict[str, dict[str, Any]] = {}
    for s in scanned:
        row = rows.setdefault(s.locator, {"kind": s.kind, "locator": s.locator, "evidence": []})
        row["evidence"] += [e for e in s.evidence if e not in row["evidence"]]
    for p in stores:
        for loc in p.found_as:
            rows.setdefault(loc, {"kind": p.kind, "locator": loc, "evidence": list(p.evidence)})
    for n, row in enumerate(rows.values(), 1):
        found.candidates.append({"id": f"c{n}", **row, "in_plan": owner.get(row["locator"])})
    found.settings = {r["locator"].removeprefix("env:") for r in rows.values()}
    found.settings = {s for s in found.settings if _SETTING.match(s)}
    found.settings |= {m for p in stores for m in p.moved_by}

    said: list[tuple[str, str | None]] = []
    for p in stores:
        said += [(f"probe: {line}", p.id) for line in p.seen]
    if plan:
        said += [(f"probe: found as traces, left alone: {e}", None) for e in plan.exclude]
    for n, done in enumerate(work.get("rehearsals") or [], 1):
        if done.get("status") == "proven":
            continue
        lines = [line for line in str(done.get("report", "")).splitlines() if line.strip()]
        leaks = [line for line in lines if _LEAK_LINE.match(line)] or lines[-1:]
        said += [(f"rehearsal {n}: {line.strip()}", None) for line in leaks]
    found.findings = [
        {"id": f"p{n}", "what": what, "store": store} for n, (what, store) in enumerate(said, 1)
    ]
    return found


async def _scan(targets: Targets, target: str) -> list[discover.Store]:
    folder = _folder(targets, target)
    if folder is None or not folder.is_dir():
        return []
    return await asyncio.to_thread(discover.scan, folder)


def _current(saved: Plan | None, work: dict[str, Any]) -> tuple[Plan | None, str]:
    """What a new proposal starts from, and how to name it to the admin: this session's last
    proposal (or the plan its rehearsal returned), else the saved plan."""
    latest = work["proposals"].get(work.get("latest") or "")
    if latest is not None:
        how = {"failed": "the plan rehearsed last, which failed"}.get(
            latest.get("rehearsed") or "", f"proposal {latest['version']}"
        )
        return Plan.from_json(latest["plan"]), how
    return saved, "the saved plan"


async def read(targets: Targets, session: dict[str, Any]) -> dict[str, Any]:
    """The ``read_isolation`` result: the saved plan, how fresh it is, and every piece of
    evidence a proposal may cite."""
    target = str(session["target"])
    work = state(session)
    saved = await asyncio.to_thread(_store(targets.config).load, target)
    scanned = await _scan(targets, target)
    found = evidence(saved, scanned, work)
    out: dict[str, Any] = {"target": target}
    if saved is None:
        out["plan"] = None
        out["note"] = (
            f"No isolation plan yet. `evalution isolate {target}` makes one: it reads the code, "
            "watches one probe and drafts a plan. Until then, nothing can be proposed."
        )
    else:
        out["plan"] = {
            "status": saved.status,
            "stores": [asdict(s) for s in saved.stores],
            "exclude": saved.exclude,
            "report": saved.report,
        }
        folder = _folder(targets, target)
        out["stale"] = (
            await asyncio.to_thread(stale, saved, folder)
            if folder
            else "this target has no code folder, so whether the code moved can't be told"
        )
    tied = {c["locator"] for c in found.candidates if c["in_plan"]}
    moved = {m for s in (saved.stores if saved else []) for m in s.moved_by}
    out["candidates"] = [
        {**c, "evidence": c["evidence"][:2]} for c in found.candidates[:CANDIDATES_SHOWN]
    ]
    if len(found.candidates) > CANDIDATES_SHOWN:
        out["candidates_note"] = f"{len(found.candidates) - CANDIDATES_SHOWN} more not shown"
    out["dropped"] = [
        c["locator"]
        for c in found.candidates
        if c["locator"] not in tied and not c["locator"].startswith("env:")
    ][:CANDIDATES_SHOWN]
    out["settings_not_tied"] = sorted(found.settings - moved)
    out["findings"] = found.findings
    latest = work["proposals"].get(work.get("latest") or "")
    if latest is not None:
        out["proposal"] = {
            k: latest.get(k) for k in ("id", "version", "status", "changes", "rehearsed")
        }
    out["rehearsals"] = {
        "failed": work["failed"],
        "left": max(0, MAX_FAILED - work["failed"]),
    }
    return out


def _names(locator: str) -> list[str]:
    """The words of *locator* a finding would carry: its name without prefix or container, and
    its last part."""
    name = locator
    for prefix in _PREFIXES:
        name = name.removeprefix(prefix)
    if ":" in name and not name.startswith("/"):
        name = name.split(":", 1)[1]
    out = [name, Path(name).name, name.split("/")[0]]
    return [n for n in dict.fromkeys(out) if len(n) >= 3]


def _ties(locator: str, item: dict[str, Any]) -> bool:
    """Whether the cited *item* is about the store at *locator*."""
    text = item.get("locator") or item.get("what") or ""
    if text == locator:
        return True
    return any(n in text for n in _names(locator)) or any(n in locator for n in _names(text))


def _words(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{what}: a list of names")
    return [v.strip() for v in value]


class _Edit:
    """One proposal applied to a copy of the plan, its reasons for refusal and its diff in words
    kept as it goes."""

    def __init__(self, plan: Plan, found: Evidence) -> None:
        self.plan = plan
        self.found = found
        self.refused: list[str] = []
        self.said: list[str] = []
        self.evidence: list[str] = []

    def store(self, sid: Any) -> PlannedStore | None:
        return next((s for s in self.plan.stores if s.id == sid), None)

    def settings(self, names: list[str], where: str) -> bool:
        unknown = [n for n in names if n not in self.found.settings]
        if unknown:
            self.refused.append(
                f"{where}: {', '.join(unknown)} isn't a setting the code read found, so nothing "
                "shows it moves this store. Name one from read_isolation's candidates."
            )
        return not unknown

    def set(self, change: dict[str, Any]) -> None:
        sid = change.get("store")
        target = self.store(sid)
        if target is None:
            self.refused.append(f"set: no store {sid!r} in the plan.")
            return
        if "moved_by" in change:
            names = _words(change["moved_by"], "moved_by")
            if self.settings(names, f"set {sid}"):
                was = ", ".join(target.moved_by) or "nothing"
                target.moved_by = names
                self.said.append(f"{sid} is moved by {', '.join(names) or 'nothing'} (was {was}).")
        if "keyed_by" in change:
            keyed = change["keyed_by"]
            if keyed is not None and not (isinstance(keyed, str) and keyed.strip()):
                raise ValueError("keyed_by: text, or null")
            was = target.keyed_by or "not known"
            target.keyed_by = keyed.strip() if keyed else None
            self.said.append(f"{sid} is kept per {target.keyed_by or 'nothing known'} (was {was}).")
        if "strategy" in change:
            how = change["strategy"]
            if how not in STRATEGIES:
                raise ValueError(f"strategy: one of {', '.join(STRATEGIES)}")
            if how == "snapshot" and not target.locator.startswith("/"):
                self.refused.append(
                    f"set {sid}: snapshot needs a path on this computer, and "
                    f"{target.locator} isn't one."
                )
            else:
                self.said.append(f"{sid} is reset by {_how(how)} (was {_how(target.strategy)}).")
                target.strategy = how

    def add(self, change: dict[str, Any]) -> None:
        given = change.get("new")
        if not isinstance(given, dict):
            raise ValueError("add: new must be an object with id, kind and locator")
        sid, kind, locator = given.get("id"), given.get("kind"), given.get("locator")
        if not isinstance(sid, str) or not _STORE_ID.match(sid):
            raise ValueError("add: new.id must be short, lowercase letters, digits and dashes")
        if kind not in KINDS:
            raise ValueError(f"add: new.kind must be one of {', '.join(KINDS)}")
        if not isinstance(locator, str) or not locator.strip():
            raise ValueError("add: new.locator must be text")
        if self.store(sid) is not None:
            self.refused.append(f"add: the plan already has a store {sid}.")
            return
        cite = change.get("cite")
        item = self.found.find(cite) if isinstance(cite, str) else None
        if item is None:
            self.refused.append(
                f"add {sid}: nothing found supports it (cite {cite!r} isn't a candidate or "
                "finding id from read_isolation). Never add a store no evidence shows."
            )
            return
        if not _ties(locator, item):
            self.refused.append(
                f"add {sid}: {cite} is about {item.get('locator') or item.get('what')}, not "
                f"{locator}."
            )
            return
        moved = _words(given.get("moved_by") or [], "new.moved_by")
        if not self.settings(moved, f"add {sid}"):
            return
        keyed = given.get("keyed_by") if isinstance(given.get("keyed_by"), str) else None
        how = given.get("strategy") or strategy_for(
            kind, locator, moved, keyed, locator.startswith(("volume:", "service:"))
        )
        if how not in STRATEGIES:
            raise ValueError(f"new.strategy: one of {', '.join(STRATEGIES)}")
        cited = list(item.get("evidence") or [])
        new = PlannedStore(
            sid,
            kind,
            locator.strip(),
            moved_by=moved,
            keyed_by=keyed,
            strategy=how,
            evidence=[*cited, f"cited {cite}: {item.get('what') or item['locator']}"],
            seen=[item["what"]] if "what" in item else [],
            found_as=[item["locator"]] if "locator" in item else [],
        )
        self.plan.stores.append(new)
        self.evidence += cited
        self.said.append(
            f"Adds {sid}: {kind} at {new.locator}, reset by {_how(how)}, because of "
            f"{item.get('what') or 'the code read: ' + item['locator']}."
        )

    def remove(self, change: dict[str, Any]) -> None:
        sid = change.get("store")
        target = self.store(sid)
        if target is None:
            self.refused.append(f"remove: no store {sid!r} in the plan.")
            return
        if target.seen:
            self.refused.append(
                f"remove {sid}: the probe found the canary in it ({target.seen[0]}); a plan "
                "without it would leak."
            )
            return
        self.plan.stores.remove(target)
        self.said.append(f"No longer resets {sid} ({target.locator}).")

    def exclude(self, change: dict[str, Any]) -> None:
        entry = change.get("entry")
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError("exclude: entry must be text")
        entry = entry.strip()
        if entry in self.plan.exclude:
            self.refused.append(f"exclude: {entry} is left alone already.")
            return
        if not any(entry in f["what"] for f in self.found.findings):
            self.refused.append(
                f"exclude: the probe never found {entry}; only what it found can be left alone."
            )
            return
        self.plan.exclude.append(entry)
        self.said.append(f"Leaves {entry} alone (not reset).")

    def include(self, change: dict[str, Any]) -> None:
        entry = change.get("entry")
        if entry not in self.plan.exclude:
            self.refused.append(f"include: {entry!r} isn't left alone now.")
            return
        if _TRACES.search(str(entry)):
            self.refused.append(
                f"include: {entry} is a trace table; it stays excluded, since resetting it "
                "would erase the evidence of what happened."
            )
            return
        self.plan.exclude.remove(entry)
        self.said.append(f"Resets {entry} again (it was left alone).")


def _how(strategy: str) -> str:
    return {
        "relocate": "pointing it at a fresh copy",
        "snapshot": "putting a kept copy back in place",
        "identity": "giving each try its own person",
        "none": "nothing (not isolated)",
    }.get(strategy, strategy)


_HANDLERS = {
    "set": _Edit.set,
    "add": _Edit.add,
    "remove": _Edit.remove,
    "exclude": _Edit.exclude,
    "include": _Edit.include,
}


async def propose(targets: Targets, session: dict[str, Any], args: dict[str, Any]) -> Any:
    """The ``propose_isolation_plan`` result: a draft kept on the session, or why not."""
    target = str(session["target"])
    work = state(session)
    changes = args.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("changes: a list of edits, each with an op")
    saved = await asyncio.to_thread(_store(targets.config).load, target)
    base, named = _current(saved, work)
    if base is None:
        return (
            f"Refused: {target} has no isolation plan to change yet. Tell the admin "
            f"`evalution isolate {target}` makes one."
        )
    found = evidence(saved, await _scan(targets, target), work)
    edit = _Edit(Plan.from_json(base.to_json()), found)
    for change in changes:
        if not isinstance(change, dict) or change.get("op") not in OPS:
            raise ValueError(f"changes.op: one of {', '.join(OPS)}")
        _HANDLERS[change["op"]](edit, change)
    if edit.refused:
        return "Refused, nothing proposed. Fix these and propose again:\n- " + "\n- ".join(
            edit.refused
        )
    if not edit.said:
        return "Refused: these edits change nothing."
    plan = edit.plan
    plan.status, plan.report = "draft", ""
    folder = _folder(targets, target)
    if folder is not None and edit.evidence:
        cited = [e for s in plan.stores for e in s.evidence]
        plan.evidence_hashes = await asyncio.to_thread(hashes, folder, cited)
    why = args.get("why") if isinstance(args.get("why"), str) else ""
    pid = f"i{secrets.token_hex(4)}"
    version = len(work["proposals"]) + 1
    work["proposals"][pid] = {
        "id": pid,
        "version": version,
        "status": "draft",
        "from": named,
        "changes": edit.said,
        "why": why.strip(),
        "plan": plan.to_json(),
        "rehearsed": None,
    }
    work["latest"] = pid
    return {
        "proposal": pid,
        "version": version,
        "status": "draft",
        "from": named,
        "changes": edit.said,
        "next": (
            f"Show it to the admin with reply(text=..., isolation={pid!r}), in plain words, and "
            "end your turn. It is rehearsed only once they approve it."
        ),
    }


def shown(session: dict[str, Any], pid: str) -> tuple[dict[str, Any], str]:
    """Proposal *pid* as the admin is shown it: the data on the message, and the diff in words.

    :raises LookupError: No such proposal in this session.
    """
    proposal = state(session)["proposals"].get(pid)
    if proposal is None:
        raise LookupError(f"no isolation proposal {pid}: propose it with propose_isolation_plan")
    lines = "\n".join(f"- {line}" for line in proposal["changes"])
    words = f"What changes in the isolation plan (from {proposal['from']}):\n{lines}"
    data = {
        "id": pid,
        "version": proposal["version"],
        "status": proposal["status"],
        "from": proposal["from"],
        "changes": proposal["changes"],
    }
    return data, words


def _tried(work: dict[str, Any]) -> str:
    """What was tried and what leaked, in plain words, for asking the admin."""
    out = []
    for done in work["rehearsals"]:
        if done.get("status") == "proven":
            continue
        leaks = [
            line.strip()
            for line in str(done.get("report", "")).splitlines()
            if _LEAK_LINE.match(line)
        ]
        changes = "; ".join(done.get("changes") or []) or "the plan as saved"
        out.append(
            f"- Proposal {done['version']} ({changes}) leaked: "
            f"{' / '.join(leaks) or 'see its report'}"
        )
    return "\n".join(out)


async def rehearse(targets: Targets, session: dict[str, Any], args: dict[str, Any]) -> Any:
    """The ``rehearse_isolation`` result: the rehearsal's report and status, or why it didn't
    run."""
    target = str(session["target"])
    work = state(session)
    if work["failed"] >= MAX_FAILED:
        return (
            f"Refused: {work['failed']} rehearsals failed in this session; no more are run. Ask "
            "the admin what to do, telling them in plain words what leaked and what you tried:\n"
            + _tried(work)
        )
    pid = str(args.get("plan_id") or work.get("latest") or "")
    proposal = work["proposals"].get(pid)
    if proposal is None:
        return (
            "Refused: no isolation proposal to rehearse. Propose one with propose_isolation_plan."
        )
    asked = (session.get("plans") or {}).get(pid)
    if asked is None or not asked.get("approved"):
        return (
            f"Refused: proposal {pid} is not approved. Show it with reply(isolation={pid!r}) and "
            "end your turn; rehearse it only once the admin approves."
        )
    if proposal["rehearsed"]:
        return (
            f"Refused: proposal {pid} was rehearsed already ({proposal['rehearsed']}). Propose a "
            "change to rehearse again."
        )
    plan = Plan.from_json(proposal["plan"])
    try:
        result = await rehearse_plan(targets.config, target, plan)
    except Exception as exc:  # the rehearsal couldn't run: no verdict on the plan
        return f"Error: the rehearsal didn't finish: {str(exc)[:500]}. Tell the admin."
    status = result.status if result.status in ("proven", "failed") else "failed"
    proposal["rehearsed"] = status
    proposal["status"] = status
    proposal["plan"] = result.to_json()
    work["rehearsals"].append(
        {
            "version": proposal["version"],
            "status": status,
            "report": result.report,
            "changes": proposal["changes"],
        }
    )
    if status == "proven":
        result.status = "proven"
        number = await asyncio.to_thread(_store(targets.config).save, result)
        return {
            "status": "proven",
            "report": result.report,
            "saved_as_version": number,
            "next": "Tell the admin in plain words what is reset between tries and what is left "
            "alone. They can undo it.",
        }
    work["failed"] += 1
    left = MAX_FAILED - work["failed"]
    found: dict[str, Any] = {
        "status": "failed",
        "report": result.report,
        "failed_rehearsals": work["failed"],
        "left": left,
    }
    found["next"] = (
        "Read what leaked (read_isolation lists it as findings), then propose a change that "
        "covers it, citing the finding, and show it to the admin."
        if left
        else "No rehearsals are left in this session. Ask the admin what to do, telling them in "
        "plain words what leaked and what you tried:\n" + _tried(work)
    )
    return found
