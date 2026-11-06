"""What the test writer's tools do (:data:`HANDLERS`), over the functions the routes use. What the
model is told about them is in :mod:`evalution.writer.schemas`.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalution.code import CodeError
from evalution.core.case import Case
from evalution.llm import cut, fenced
from evalution.project import drift, filememory, knowledge, library_fit, profile, understanding
from evalution.results.evidence import look_into
from evalution.results.views import case_detail, detail, latest_rows
from evalution.review.findings import SUSPECTS
from evalution.runs.estimate import suggest_seconds
from evalution.runs.fit import why_not
from evalution.runs.runner import Runs
from evalution.suite import cases, library, store
from evalution.targets import Targets
from evalution.traces.base import TraceSource
from evalution.traces.profile import digest, recent_sessions, render
from evalution.writer import linecheck
from evalution.writer.draft import draft, from_traces, sources
from evalution.writer.schemas import STEP_KINDS

#: Data files the agent may write for a test: plain names, text formats, small.
FIXTURE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(csv|tsv|json|txt|md|xml)$")
FIXTURE_BYTES = 200_000
OUTPUT_CHARS = 12_000
#: The most of one file a read_code result carries: the transcript is sent again at every step.
READ_CHARS = 32_000
#: How many of a try's model calls read_try shows; read_trace goes through the rest.
_CONTEXT_SHOWN = 3


@dataclass
class AgentTurn:
    """One stretch of work on a session: what the agent read since it last spoke."""

    session: dict[str, Any]
    read: list[str] = field(default_factory=list)
    #: The code files it read, which a partial evidence ref can then be matched against.
    code_read: set[str] = field(default_factory=set)
    saved: str | None = None
    replied: bool = False
    #: The traces and code as this turn took them (:func:`drift.take`): read once, however many
    #: times write_understanding is tried.
    taken: tuple[dict[str, Any], dict[str, Any]] | None = None

    async def take(self, targets: Targets) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.taken is None:
            name = str(self.session["target"])
            self.taken = await drift.take(targets, name, drift.PROFILE_SESSIONS)
        return self.taken


def say(session: dict[str, Any], role: str, text: str, **extra: Any) -> dict[str, Any]:
    message = {
        "id": f"m{secrets.token_hex(6)}",
        "role": role,
        "at": time.time(),
        "text": text,
        **{k: v for k, v in extra.items() if v},
    }
    session["messages"].append(message)
    return message


def activity_line(name: str, args: Mapping[str, Any]) -> str | None:
    """What a tool call is, in the words the page shows while the agent works."""
    case = args.get("case") if isinstance(args.get("case"), str) else None
    run = args.get("run")
    return {
        "list_sets": "Looking at the sets",
        "list_cases": "Looking at the cases",
        "read_case": f"Reading the case {case}",
        "read_docs": f"Reading {args.get('path')}",
        "approve": None,
        "save_case": "Saving the case",
        "estimate": f"Estimating a run of {case or 'the set'}",
        "read_run": f"Reading the results of run {run}",
        "read_try": f"Reading try {args.get('try')} of {case}",
        "read_trace": f"Reading the traces of {case}",
        "read_profile": "Reading the profile",
        "read_understanding": "Reading what I understood",
        "write_understanding": "Saving what I understood",
        "read_trace_digest": "Reading recent traces",
        "list_code": "Looking at the code",
        "read_code": f"Reading {args.get('path')}",
        "read_drift": "Checking what changed",
        "diff_code": "Reading the code changes",
        "note_drift": "Noting a change",
        "list_library": "Looking at the test library",
        "adapt_template": f"Adapting the library test {args.get('id')}",
        "list_conversations": "Looking at recent conversations",
        "draft_from_conversation": "Drafting a test from a conversation",
    }.get(name)


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what}: must be text")
    return value.strip()


def _plan(value: Any) -> dict[str, Any]:
    """A ``plan`` read strictly. :raises ValueError: A missing or wrong field."""
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        raise ValueError("plan.steps: must be a list")
    steps = []
    for step in value["steps"]:
        if not isinstance(step, dict) or step.get("kind") not in STEP_KINDS:
            raise ValueError(f"plan.steps.kind: must be one of {', '.join(STEP_KINDS)}")
        read = {"kind": step["kind"], "title": _text(step.get("title"), "plan.steps.title")}
        if isinstance(step.get("note"), str) and step["note"].strip():
            read["note"] = step["note"].strip()
        steps.append(read)
    tries = value.get("tries")
    if isinstance(tries, bool) or not isinstance(tries, int) or tries < 1:
        raise ValueError("plan.tries: must be a whole number of at least 1")
    return {
        "steps": steps,
        "tries": tries,
        "case": _text(value.get("case"), "plan.case"),
    }


def _same_test(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _review(value: Any) -> dict[str, Any]:
    """A ``review`` read strictly. :raises ValueError: A missing or wrong field."""
    if not isinstance(value, dict) or not isinstance(value.get("findings"), list):
        raise ValueError("review.findings: must be a list")
    findings = []
    for finding in value["findings"]:
        if not isinstance(finding, dict) or finding.get("suspected") not in SUSPECTS:
            raise ValueError(f"review.findings.suspected: must be one of {', '.join(SUSPECTS)}")
        read: dict[str, Any] = {
            "suspected": finding["suspected"],
            "text": _text(finding.get("text"), "review.findings.text"),
        }
        if isinstance(finding.get("case"), str) and finding["case"]:
            read["case"] = finding["case"]
        if isinstance(finding.get("evidence"), list):
            read["evidence"] = [str(e) for e in finding["evidence"] if str(e).strip()]
        findings.append(read)
    review: dict[str, Any] = {
        "summary": _text(value.get("summary"), "review.summary"),
        "findings": findings,
    }
    if isinstance(value.get("proposal"), str) and value["proposal"].strip():
        review["proposal"] = value["proposal"].strip()
    return review


class Tools:
    """The tools of the agent's loop; each takes the turn and the model's arguments."""

    def __init__(self, runs: Runs, save: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self.runs = runs
        self.targets = runs.targets
        self._save = save

    def _target(self, turn: AgentTurn) -> str:
        return str(turn.session["target"])

    async def list_sets(self, turn: AgentTurn, _args: dict) -> Any:
        return await asyncio.to_thread(self.runs.set_rows, self._target(turn))

    async def list_cases(self, turn: AgentTurn, args: dict) -> Any:
        target = self._target(turn)
        set_name = str(args.get("set") or turn.session["set"])
        docs = self.targets.docs
        if set_name not in store.list_sets(docs, target):
            raise LookupError(f"no set {set_name}")
        found = drift.read(self.targets, target)
        return await asyncio.to_thread(
            lambda: latest_rows(
                store.load_cases(docs, target, set_name),
                self.runs.all(target, set_name),
                lambda case: drift.stale(case, found),
            )
        )

    async def read_case(self, turn: AgentTurn, args: dict) -> Any:
        set_name = str(args.get("set") or turn.session["set"])
        stem = str(args.get("case") or "")
        case = await asyncio.to_thread(
            store.load_case, self.targets.docs, self._target(turn), set_name, stem
        )
        turn.read.append(f"Read case {case.case}")
        return {"case": case.case, "file": case.raw}

    def _docs_path(self, path: str) -> Path:
        """The file or folder *path* names under ``docs/``.

        :raises ValueError: Anything else, or a path that leaves its folder.
        """
        parts = Path(path.strip().lstrip("/")).parts
        if not parts or parts[0] != "docs" or ".." in parts:
            raise ValueError("only paths under docs/")
        root = (self.targets.config.root / "docs").resolve()
        target = root.joinpath(*parts[1:]).resolve()
        if not target.is_relative_to(root) or not target.exists():
            raise ValueError(f"{path}: not found")
        return target

    async def read_docs(self, turn: AgentTurn, args: dict) -> Any:
        path = str(args.get("path") or "")
        target = self._docs_path(path)
        if target.is_dir():
            names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
            text = "\n".join(n for n in names if not n.startswith("."))
        else:
            text = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        turn.read.append(f"Read {path.strip().lstrip('/')}")
        return fenced(path, cut(text, OUTPUT_CHARS * 3))

    async def approve(self, turn: AgentTurn, args: dict) -> Any:
        session = turn.session
        plan = (session.get("plans") or {}).get(str(args.get("plan_id") or ""))
        if plan is None:
            return "Refused: no such plan. Show it with reply(plan=...) and end your turn."
        if int(session.get("asks", 0)) <= plan["asks"]:
            return "Refused: the admin has not answered this plan yet. End your turn and wait."
        plan["approved"] = plan.get("approved") or "message"
        return "Approved."

    async def save_case(self, turn: AgentTurn, args: dict) -> Any:
        session = turn.session
        plan_id = str(args.get("plan_id") or "")
        plan = (session.get("plans") or {}).get(plan_id)
        if plan is None or not plan.get("approved"):
            return (
                "Refused: that plan is not approved. Show the plan with reply(plan=...) and end "
                "your turn; add the case only once the admin approves it."
            )
        if plan.get("saved"):
            return f"Refused: plan {plan_id} was already added as {plan['saved']}. Show a new plan."
        # What's saved is what the plan showed, time limit and all. The model may leave the test
        # out, or send it back as it proposed it.
        test = plan.get("file")
        given = args.get("case", test)
        if test is None or not (_same_test(test, given) or _same_test(plan.get("asked"), given)):
            return (
                "Refused: that isn't the test the admin approved. Show the test you mean to save "
                "as a new plan, with its file, and end your turn."
            )
        replace = args.get("replace")
        if replace is not None and replace not in session["cases"]:
            return (
                f"Refused: {replace} was not saved in this session. Save a new case instead, "
                "and tell the admin."
            )
        docs, target = self.targets.docs, self._target(turn)
        if replace != plan.get("replaces"):
            return (
                f"Refused: plan {plan_id} wasn't shown as replacing {replace}. Show a plan with "
                "replaces set, so the admin sees what changes."
            )
        if replace is not None:
            now = await asyncio.to_thread(store.load_case, docs, target, session["set"], replace)
            if now.version != plan.get("base"):
                return (
                    f"Refused: {replace} changed since the plan was shown. Show a new plan, so "
                    "the admin sees the difference from what it is now."
                )
        stem = await asyncio.to_thread(store.save_case, docs, target, session["set"], test, replace)
        if stem not in session["cases"]:
            session["cases"].append(stem)
        plan["saved"] = stem
        turn.saved = stem
        return {"case": stem, "set": session["set"]}

    async def save_fixture(self, turn: AgentTurn, args: dict) -> Any:
        session = turn.session
        plan = (session.get("plans") or {}).get(str(args.get("plan_id") or ""))
        if plan is None or not plan.get("approved"):
            return "Refused: that plan is not approved. Write files only for an approved plan."
        name = str(args.get("name") or "")
        if not FIXTURE_NAME.match(name):
            raise ValueError(
                "name: a plain file name ending in .csv, .tsv, .json, .txt, .md or .xml"
            )
        content = str(args.get("content") or "")
        if len(content.encode()) > FIXTURE_BYTES:
            raise ValueError(f"content: at most {FIXTURE_BYTES // 1000} KB")
        path = await asyncio.to_thread(
            store.write_file,
            self.targets.docs,
            self._target(turn),
            session["set"],
            f"fixtures/{name}",
            content.encode(),
        )
        return {"file": path, "lines": content.count("\n")}

    async def estimate(self, turn: AgentTurn, args: dict) -> Any:
        session = turn.session
        case, tries = args.get("case"), args.get("tries")
        if tries is not None and (not isinstance(tries, int) or not 1 <= tries <= 20):
            raise ValueError("tries: must be a whole number from 1 to 20")
        found = await asyncio.to_thread(
            self.runs.estimate, session["target"], session["set"], [case] if case else None, tries
        )
        return {k: v for k, v in found.items() if k != "token"}

    async def read_run(self, turn: AgentTurn, args: dict) -> Any:
        run = str(args.get("run") or "")
        doc = await asyncio.to_thread(self.runs.load, run)
        if doc is None:
            raise LookupError(f"no run {run}")
        others = await asyncio.to_thread(self.runs.all, doc["target"], doc["set"])
        found = detail(doc, others, None)
        found.pop("history", None)
        turn.read.append(f"Read run {run}")
        return found

    async def read_try(self, turn: AgentTurn, args: dict) -> Any:
        run, case, n = str(args.get("run") or ""), str(args.get("case") or ""), args.get("try")
        doc = await asyncio.to_thread(self.runs.load, run)
        found = case_detail(doc, case) if doc else None
        attempt = next((t for t in (found or {}).get("tries", []) if t["n"] == n), None)
        if found is None or attempt is None:
            raise LookupError(f"no try {n} of {case} in run {run}")
        turn.read.append(f"Read try {n} of {case}")
        keys = (
            "n",
            "outcome",
            "basis",
            "route",
            "checks",
            "seconds",
            "tokens",
            "tools",
            "reply",
            "steps",
            "rubric",
            "reply_text",
            "limits_broken",
            "error",
            "analysis",
            "trace_url",
            "context",
            "trajectory",
        )
        found = {k: attempt.get(k) for k in keys}
        # A long try made hundreds of calls: read_trace goes through them without this much text.
        if len(found.get("context") or ()) > _CONTEXT_SHOWN:
            found["context"] = found["context"][-_CONTEXT_SHOWN:]
            found["context_note"] = "only the last calls are here: use read_trace for the rest"
        return found

    async def read_trace(self, turn: AgentTurn, args: dict) -> Any:
        run, case, n = str(args.get("run") or ""), str(args.get("case") or ""), args.get("try")
        find = args.get("find")
        kept = await asyncio.to_thread(
            lambda: next(
                (e for e in self.runs.store.evidence(run) if e.case == case and e.n == n),
                None,
            )
        )
        if kept is None:
            raise LookupError(f"no traces kept for try {n} of {case} in run {run}")
        turn.read.append(f"Read the traces of try {n} of {case}")
        looked = look_into(kept, str(find) if find else None)
        return fenced("the traces", json.dumps(looked, ensure_ascii=False))

    async def read_profile(self, turn: AgentTurn, _args: dict) -> Any:
        turn.read.append("Read the profile")
        return fenced(
            "the profile", profile.read_profile(self.targets, self._target(turn)) or "(none yet)"
        )

    async def read_understanding(self, turn: AgentTurn, _args: dict) -> Any:
        turn.read.append("Read what I understood")
        found = await asyncio.to_thread(understanding.written, self.targets, self._target(turn))
        return found or "(nothing saved yet)"

    async def write_understanding(self, turn: AgentTurn, args: dict) -> Any:
        target = self._target(turn)
        found, taken = await turn.take(self.targets)
        known = found["tools_offered_known"]
        traced = {n for n, t in found["tools"].items() if t["offered"] or not known}
        code = self.targets.code(target)

        def in_code(path: str, name: str) -> bool:
            try:
                return code is not None and name in code.read(path)
            except (CodeError, OSError):
                return False

        try:
            templates = library.load_templates(self.targets.config.library_dir)
        except library.LibraryError:
            templates = []
        written, problems = await asyncio.to_thread(
            understanding.check,
            args,
            traced,
            in_code,
            {t.id for t in templates},
            understanding.admin_priorities(self.targets, target),
        )
        if problems:
            return "Refused, nothing saved. Fix these and save again:\n" + "\n".join(problems[:20])
        drift.keep_baseline(self.targets, target, taken)
        understanding.save(self.targets, target, understanding.build(written, found))
        if code is not None:
            await asyncio.to_thread(knowledge.written, self.targets, target, code, turn.code_read)
            await asyncio.to_thread(filememory.learned, self.targets, target, code)
        return (
            "Saved as a new version; the fingerprint is new and the drift list is clear. "
            "Now say one line to the admin and end your turn."
        )

    async def read_trace_digest(self, turn: AgentTurn, args: dict) -> Any:
        target = self._target(turn)
        source = self.targets.source(target)
        sessions = args.get("sessions") or 50
        if not isinstance(sessions, int) or not 1 <= sessions <= 200:
            raise ValueError("sessions: a whole number from 1 to 200")
        groups = await recent_sessions(source, self.targets.trace_attribute(target), sessions)
        turn.read.append(f"Read a digest of {len(groups)} sessions")
        return fenced("the trace digest", render(digest(groups)))

    def _code(self, turn: AgentTurn) -> Any:
        code = self.targets.code(self._target(turn))
        if code is None:
            raise LookupError("this target has no code folder")
        return code

    async def list_code(self, turn: AgentTurn, args: dict) -> Any:
        found, more = await asyncio.to_thread(self._code(turn).list, str(args.get("glob") or "**"))
        if more:
            found.append(f"({more} more files match: narrow the glob to see them)")
        return fenced("the files", "\n".join(found) or "(none)")

    async def read_code(self, turn: AgentTurn, args: dict) -> Any:
        """A file, or a pointer back to it when this session read it as it is already: the text is
        still in the transcript, and sending it again costs as much as the first time."""
        path = str(args.get("path") or "")
        text = await asyncio.to_thread(self._code(turn).read, path)
        rel = path.strip().lstrip("/")
        turn.read.append(f"Read {path}")
        turn.code_read.add(rel)
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        seen = turn.session.setdefault("code_seen", {})
        step = int(turn.session.get("steps") or 0)
        if (seen.get(rel) or {}).get("digest") == digest:
            return f"{rel} is unchanged since you read it at step {seen[rel]['step']}: see above."
        seen[rel] = {"digest": digest, "step": step}
        if len(text) > READ_CHARS:
            text = text[:READ_CHARS] + "\n… (cut here: the rest of the file is not shown)"
        return fenced(path, text)

    async def read_drift(self, turn: AgentTurn, _args: dict) -> Any:
        turn.read.append("Read the drift")
        return drift.read(self.targets, self._target(turn))

    async def diff_code(self, turn: AgentTurn, args: dict) -> Any:
        since = _text(args.get("since"), "since")
        text = await asyncio.to_thread(self._code(turn).diff, since)
        turn.read.append(f"Read the code diff since {since[:10]}")
        return fenced("the diff of the watched files", text)

    async def note_drift(self, turn: AgentTurn, args: dict) -> Any:
        return drift.note(
            self.targets,
            self._target(turn),
            _text(args.get("change"), "change"),
            _text(args.get("summary"), "summary"),
        )

    async def list_library(self, turn: AgentTurn, _args: dict) -> Any:
        turn.read.append("Read the test library")
        return await asyncio.to_thread(library_fit.for_target, self.targets, self._target(turn))

    async def adapt_template(self, turn: AgentTurn, args: dict) -> Any:
        target = self._target(turn)
        template_id = str(args.get("id") or "")
        rows = await asyncio.to_thread(library_fit.for_target, self.targets, target)
        row = next((r for r in rows if r["id"] == template_id), None)
        if row is None:
            raise LookupError(f"no library test {template_id}")
        if row["fit"]["verdict"] != "fits":
            return f"Refused: {row['fit']['label']}. {row['fit']['reason']}".strip()
        template = library.find(self.targets.config.library_dir, template_id)
        turn.read.append(f"Read the library test {template_id}")
        if not args.get("values"):
            return {
                **template.summary(),
                "case": template.case,
                "fill": {
                    name: {
                        "kind": library.PLACEHOLDERS[name][0],
                        "with": library.PLACEHOLDERS[name][1],
                    }
                    for name in template.placeholders
                },
            }
        values = dict(args["values"]) if isinstance(args["values"], dict) else args["values"]
        chosen = isinstance(values, dict) and "seconds" in values
        if isinstance(values, dict) and not chosen:
            values["seconds"] = library.dummy_values(["seconds"])["seconds"]
        found = library.adapt(template, values, profile.read_profile(self.targets, target))
        if not chosen and "seconds" in template.placeholders:
            case = cases.parse_case(found["case"], f"library: {template_id}")
            docs = await asyncio.to_thread(self.runs.all, target, str(turn.session["set"]))
            found["case"]["limits"]["seconds"] = suggest_seconds(case, docs)[0]
        return {**found, "next": "Show it as a plan; save it with save_case once approved."}

    def _source(self, turn: AgentTurn) -> TraceSource:
        return self.targets.source(self._target(turn))

    async def list_conversations(self, turn: AgentTurn, args: dict) -> Any:
        limit = args.get("limit") or 20
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit: a whole number from 1 to 50")
        target = self._target(turn)
        found = await sources(self._source(turn), self.targets.trace_attribute(target), limit)
        turn.read.append(f"Read {len(found)} recent conversations")
        return found

    async def draft_from_conversation(self, turn: AgentTurn, args: dict) -> Any:
        session_id, trace_id = args.get("session_id"), args.get("trace_id")
        if not (isinstance(session_id, str) and session_id) and not (
            isinstance(trace_id, str) and trace_id
        ):
            raise ValueError("give session_id or trace_id (from list_conversations)")
        target, set_name = self._target(turn), str(turn.session["set"])
        source = self._source(turn)
        try:
            material = await from_traces(
                source,
                self.targets.trace_attribute(target),
                session_id or None,
                trace_id or None,
            )
        except LookupError as exc:
            raise LookupError(f"no conversation {exc} in the last week") from exc
        eval_set = await asyncio.to_thread(store.load_set, self.targets.docs, target, set_name)
        found = await draft(self.runs.caller(eval_set.grader), *material)
        turn.read.append(f"Drafted a test from {'trace' if trace_id else 'conversation'}")
        return {"case": found, "next": "Show it as a plan; save it with save_case once approved."}

    async def reply(self, turn: AgentTurn, args: dict) -> Any:
        session = turn.session
        text = _text(args.get("text"), "text")
        plan = _plan(args["plan"]) if args.get("plan") else None
        file = args["plan"].get("file") if plan is not None else None
        base = None
        if plan is not None:
            plan["id"] = f"p{secrets.token_hex(4)}"
            if file is not None:
                file, base = await self._checked_plan(session, plan, args["plan"])
        review = _review(args["review"]) if args.get("review") else None
        run = args.get("run") if isinstance(args.get("run"), str) else None
        say(
            session,
            "agent",
            text,
            read=turn.read,
            plan=plan,
            run=run,
            review=review,
            saved=turn.saved,
        )
        if plan is not None:
            plans = session.setdefault("plans", {})
            # What the admin is shown this plan for: approving it approves this test, no other.
            plans[plan["id"]] = {
                "asks": int(session.get("asks", 0)),
                "approved": None,
                "file": file,
                "asked": args["plan"].get("file"),
                "replaces": plan.get("replaces"),
                "base": base,
                "weak_lines": plan.get("weak_lines") or [],
            }
        turn.read, turn.saved, turn.replied = [], None, True
        await self._save(session)
        if plan is None:
            return "Shown to the admin."
        limit = plan.get("timeout", {}).get("seconds")
        said = f" Its time limit is {limit} s." if limit else ""
        return f"Shown to the admin as plan {plan['id']}.{said} Saving takes plan_id alone."

    async def _checked_plan(
        self, session: dict[str, Any], plan: dict[str, Any], given: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        """Lay over *plan* what the admin should see before approving: its time limit and where it
        came from, what this app can't serve, and what it changes in the test it replaces. The
        test as it will be saved (its time limit filled in if it had none), and the version of the
        one it replaces.

        :raises CaseError: A test that doesn't load.
        :raises LookupError: A plan replacing a test that isn't there.
        """
        target, set_name = str(session["target"]), str(session["set"])
        file = copy.deepcopy(given["file"])
        case = cases.parse_case(file, "plan.file")
        docs = await asyncio.to_thread(self.runs.all, target, set_name)
        seconds, source = suggest_seconds(case, docs)
        limits = file.get("limits") if isinstance(file.get("limits"), dict) else {}
        if "seconds" in limits:
            if limits["seconds"] != seconds:
                source = f"set for this test; {seconds} s suggested: {source}"
            seconds = limits["seconds"]
        else:
            file["limits"] = {**limits, "seconds": seconds}
        plan["timeout"] = {"seconds": seconds, "from": source}

        flags = []
        capabilities = self.targets.adapter(target).capabilities
        traced = self.targets.source(target) is not None
        if why := why_not(case, capabilities, traced):
            flags.append(f"This app can't run this test: {why}.")
        missing = [
            kind
            for kind in profile.required_clean(self.targets, target)
            if kind not in case.needs_clean
        ]
        if missing:
            flags.append(f"You asked every test to start clean of {', '.join(missing)}; not this.")
        if flags:
            plan["flags"] = flags
        if weak := await self._weak_lines(session, case):
            plan["weak_lines"] = weak

        replaces = given.get("replaces")
        if not isinstance(replaces, str) or not replaces:
            return file, None
        try:
            old = await asyncio.to_thread(
                store.load_case, self.targets.docs, target, set_name, replaces
            )
        except cases.CaseError as exc:
            raise LookupError(f"plan.replaces: no test {replaces}") from exc
        plan["replaces"] = replaces
        plan["changes"] = cases.meaning_changes(old.raw, file)
        return file, old.version

    async def _weak_lines(self, session: dict[str, Any], case: Case) -> list[dict[str, Any]]:
        """The rubric lines of *case* its replies show a grader could misread
        (:mod:`evalution.writer.linecheck`). None without a model to draft on and grade with."""
        llm = self.runs.llm
        if not llm or not case.rubric:
            return []
        eval_set = await asyncio.to_thread(
            store.load_set, self.targets.docs, str(session["target"]), str(session["set"])
        )
        drafting = "light" if llm.model_id("light") else "strong"
        if not llm.model_id(drafting) or not llm.model_id(eval_set.grader):
            return []
        return await linecheck.weak_lines(case, llm.caller(drafting), llm.caller(eval_set.grader))


#: Every tool the model can call, by the name in :data:`evalution.writer.schemas.TOOLS`.
HANDLERS: dict[str, Callable[[Tools, AgentTurn, dict], Awaitable[Any]]] = {
    "list_sets": Tools.list_sets,
    "list_cases": Tools.list_cases,
    "read_case": Tools.read_case,
    "read_docs": Tools.read_docs,
    "approve": Tools.approve,
    "save_case": Tools.save_case,
    "save_fixture": Tools.save_fixture,
    "estimate": Tools.estimate,
    "read_run": Tools.read_run,
    "read_try": Tools.read_try,
    "read_profile": Tools.read_profile,
    "read_understanding": Tools.read_understanding,
    "write_understanding": Tools.write_understanding,
    "read_trace": Tools.read_trace,
    "read_trace_digest": Tools.read_trace_digest,
    "list_code": Tools.list_code,
    "read_code": Tools.read_code,
    "read_drift": Tools.read_drift,
    "diff_code": Tools.diff_code,
    "note_drift": Tools.note_drift,
    "list_library": Tools.list_library,
    "adapt_template": Tools.adapt_template,
    "list_conversations": Tools.list_conversations,
    "draft_from_conversation": Tools.draft_from_conversation,
    "reply": Tools.reply,
}
