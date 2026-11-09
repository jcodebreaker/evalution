"""A run: every case of a set, or some, played their tries one after another in a
background task, through the target's adapter; up to the target's ``max_parallel`` cases at once
when its adapter can.

How many tries: a run's own ``tries``, when the person gave one, is played exactly. Without one,
each case plays until its tries say something (:mod:`evalution.runs.sampling`), from its own
``tries`` up to :data:`~evalution.core.case.MOST_TRIES`, against the run it will be compared with;
its ``planned`` grows as it goes, and ends at what it played.

Nothing starts unconfirmed: :meth:`Runs.estimate` hands out a short-lived, single-use token and
:meth:`Runs.start` refuses without one. One run at a time. A run is ``running``, then ``done``,
``stopped`` (by the admin, or the server shut down or restarted under it:
interrupted; none says anything of the app) or ``failed`` (it could not go on: the target session
went away, or a try ran out of time with work the adapter can't stop); ``stop_reason`` says which.
A run that ends on its own (``done`` or ``failed``) with failed tries is then analysed
(:mod:`evalution.review.findings`) while ``analyzing`` is true; ``analysis_error`` says when that
could not finish, for a retry. The run document (:mod:`evalution.results.store`) is rewritten after
every try; the shapes the routes return are read from it in :mod:`evalution.results.views`.

Before a run, the target is checked for drift (:mod:`evalution.project.drift`), and each case's
``stale`` reason is recorded with it. The code as the run starts is kept
(:func:`evalution.project.impact.record_run`), and the run carries its ``commit``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from evalution.adapters.base import (
    AppError,
    AppRefused,
    Capabilities,
    Session,
    Target,
    TargetGone,
    failure_of,
)
from evalution.config import TargetConfig
from evalution.core import net
from evalution.core.case import MOST_TRIES, Case, EvalSet
from evalution.core.evidence import VIDEO, Failure, TraceStatus, TryEvidence, is_shot
from evalution.core.judgement import CheckKind, Decider, JudgeStamp, Outcome, Verdict, WhyUnjudged
from evalution.core.rules import (
    ADAPTER_BROKEN,
    APP_NOT_READY,
    OTHER_FAILURE,
    PAGE_MAP_BROKEN,
    RULES_VERSION,
    SERVER_RESTARTED,
    TARGET_GONE,
    current,
    judgement,
)
from evalution.core.version import Checkout, described, unstarted
from evalution.isolation.setup import setup_for
from evalution.judging import labels
from evalution.judging.checks import code_checks
from evalution.judging.claims import index_of
from evalution.judging.memory import grade_memory, with_fold
from evalution.judging.rubric import (
    FILLER_INSTRUCTIONS,
    PROMPT_HASH,
    FillerWriter,
    Graded,
    grade_reply,
)
from evalution.judging.steps import Step, check_line, chosen_calls, grade_tools
from evalution.llm import LLM, Caller, cut, fenced
from evalution.project import drift, impact, pagemaps
from evalution.results.compare import default_base
from evalution.results.record import (
    SCHEMA,
    add_judgement,
    environment,
    pass_marks,
    run_isolation,
    verdict_of,
)
from evalution.results.store import RunLock, RunStore
from evalution.results.views import summary, totals
from evalution.review import divergence
from evalution.review.findings import analyze_run, write_report
from evalution.runs import isolated, trust
from evalution.runs.estimate import messages, plays
from evalution.runs.fit import why_not
from evalution.runs.page import learn
from evalution.runs.page.map import PageMapBroken, page_map_of, page_only
from evalution.runs.page.target import PageFiles, PageTarget, page_capabilities
from evalution.runs.play import (
    ADAPTER_FAILURES,
    Played,
    cancel_quietly,
    close_quietly,
    play_with_retry,
    timeline,
)
from evalution.runs.preflight import (
    AdapterBroken,
    check_adapter,
    forget_setup,
    passed_setups,
    remember_setup,
    setup_key,
)
from evalution.runs.sampling import one_more
from evalution.storage import Docs
from evalution.suite.cases import CaseError, is_benchmark, parse_case
from evalution.suite.store import (
    list_sets,
    load_case,
    load_cases,
    load_set,
    read_file,
    unpack_files,
)
from evalution.targets import Targets
from evalution.traces.base import TraceSource

_logger = logging.getLogger(__name__)

#: Seconds an estimate's token stays good.
TOKEN_SECONDS = 600
#: Seconds a try's session gets to open, or to reset.
SETUP_SECONDS = 120.0
#: Seconds a try waits for traces once an earlier one of the run waited in full and found none.
NO_TRACES_WAIT = 2.0
STOPPED_BY_ADMIN = "Stopped by the admin"
#: How often a run looks for a stop asked of it from another process.
STOP_POLL_SECONDS = 1.0
SERVER_SHUTDOWN = "The server shut down during the run"
STILL_WORKING = (
    "A try ran out of time with the app still working, and the adapter can't stop it, so the next "
    "try would start on top of it. Give the adapter a way to cancel."
)
#: Tries in a row that run out of time with nothing answered before the run stops: waiting out the
#: rest would take hours and show nothing.
HANGS = 3
KEEPS_HANGING = (
    f"{HANGS} tries in a row ran out of time with no answer at all, so the run stopped. "
    "The app may be down, or this computer may have gone to sleep."
)
#: What ending a run with one of these says: the app was not there to play on.
APP_NOT_READY_ERRORS = (
    TargetGone,
    AppError,
    ConnectionError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)


def filler_writer(caller: Caller | None) -> FillerWriter | None:
    """The filler turns' writer over *caller*: one short call per message, with the recent
    exchanges fenced as data and the topic."""
    if caller is None:
        return None

    async def write(about: str, history: Sequence[Mapping[str, str]]) -> str:
        said = "\n\n".join(
            f"Person: {cut(t.get('message', ''), 400)}\nAssistant: {cut(t.get('reply', ''), 400)}"
            for t in history
        )
        text = await caller(
            instructions=FILLER_INSTRUCTIONS,
            input_text="\n\n".join(
                [
                    fenced("the conversation so far", said or "(none yet)"),
                    fenced("the topic", about),
                ]
            ),
        )
        return cut(text, 1_000)

    return write


@dataclass(frozen=True)
class _Plan:
    target: str
    set: str
    cases: tuple[str, ...]
    tries: int | None
    scope: str
    expires: float


class RunBusyError(Exception):
    """A run or an analysis is already going; one at a time."""


def _last_turn(evidence: TryEvidence) -> tuple[float, float] | None:
    """When the try's last turn ran, in seconds from its start as a model call's ``t`` is."""
    if not evidence.turns:
        return None
    last = evidence.turns[-1]
    return last.started - evidence.t0, last.ended - evidence.t0


def _plays(conf: TargetConfig) -> str:
    """How a run of *conf* reaches the app: ``api``, ``page`` (its API, with its page open beside
    it) or ``page_only`` (its page alone: every reply is what the page showed, ADR 0010)."""
    if page_only(conf):
        return "page_only"
    return "page" if conf.options.get("page") is not None else "api"


def _plan_beside(docs: Docs, target: str, set_name: str) -> dict[str, Any] | None:
    """The plan that chose a benchmark set, kept as its file ``plan.json``."""
    found = read_file(docs, target, set_name, "plan.json")
    return json.loads(found) if found is not None else None


class KeepsHanging(Exception):
    """Try after try ran out of time with nothing answered."""


class StillWorking(RuntimeError):
    """A try ran out of time with the app's work still going, and it can't be stopped."""


class Runs:
    """The runs of this server: estimates, the one running task, Stop."""

    def __init__(self, targets: Targets, llm: LLM | None) -> None:
        self.targets = targets
        self.store = RunStore(
            targets.docs, partial(trust.settle, targets.docs), files=targets.files
        )
        #: One run or analysis at a time on this data folder, whichever process plays it.
        self.lock = RunLock(targets.config.data_dir)
        self.llm = llm
        self._plans: dict[str, _Plan] = {}
        self._regrades: dict[str, tuple[str, float]] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop_watch: asyncio.Task[None] | None = None
        self._running: str | None = None
        self._playing: str | None = None
        #: Targets a test message is on its way to, or that are being changed: no run starts.
        self.probing: set[str] = set()
        self._stop_reason: str | None = None
        #: Runs whose latest try waited for traces and found none.
        self._no_traces: set[str] = set()
        self._orphans_marked = False
        self._orphan_sessions: list[tuple[str, dict[str, Any]]] = []

    def caller(self, key: str) -> Caller | None:
        return self.llm.caller(key) if self.llm else None

    def _mark_orphans(self) -> None:
        """A run left ``running`` by an earlier server process was interrupted, and an analysis it
        left going ended. Its open sessions are cancelled and closed on the next async entry
        (:meth:`recover`). While another process holds the run lock what it left running is still
        going: looked at again later."""
        if self._orphans_marked:
            return
        if not self.lock.acquire():
            return
        try:
            self._mark_stored_orphans()
        finally:
            self.lock.release()

    def _mark_stored_orphans(self) -> None:
        self._orphans_marked = True
        for run in self.store.all():
            if run["run"] == self._running:
                continue
            if run.get("status") == "running":
                run.update(
                    status="stopped",
                    stop_reason=SERVER_RESTARTED,
                    ended_at=time.time(),
                    current=None,
                )
                self._orphan_sessions += [(run["target"], s) for s in run.get("sessions") or []]
                run["sessions"] = []
            elif not run.get("analyzing"):
                continue
            run["analyzing"] = False
            self.store.write(run)

    async def recover(self) -> None:
        """Mark orphans, and stop and close the sessions they left open. Cheap once done."""
        if not self._orphans_marked:
            await asyncio.to_thread(self._mark_orphans)
        sessions, self._orphan_sessions = self._orphan_sessions, []
        for target, raw in sessions:
            with contextlib.suppress(LookupError):
                adapter = self.targets.adapter(target)
                await cancel_quietly(adapter, Session(**raw))
                await close_quietly(adapter, Session(**raw))

    def all(self, target: str | None = None, set_name: str | None = None) -> list[dict[str, Any]]:
        self._mark_orphans()
        return self.store.all(target, set_name)

    def load(self, run: str) -> dict[str, Any] | None:
        self._mark_orphans()
        return self.store.load(run)

    def set_rows(self, target: str) -> list[dict[str, Any]]:
        """Every set of *target* as a ``SetSummary``; a set that does not read is left out."""
        docs = self.targets.docs
        rows = []
        for slug in list_sets(docs, target):
            try:
                eval_set = load_set(docs, target, slug)
                count = len(load_cases(docs, target, slug))
            except CaseError:
                continue
            latest = self.all(target, slug)
            rows.append(
                {
                    "target": target,
                    "set": slug,
                    "name": eval_set.name,
                    "about": eval_set.about,
                    "pass_marks": dict(eval_set.pass_marks),
                    "cases": count,
                    "latest_run": summary(latest[0]) if latest else None,
                }
            )
        return rows

    def estimate(
        self, target: str, set_name: str, cases: Sequence[str] | None, tries: int | None
    ) -> dict[str, Any]:
        """The ``Estimate`` for a set or some of its cases, with the token that starts it.

        A range: *tries*, when given, plays exactly that many, so both ends are one figure. Without
        it, each case plays from its own ``tries`` (``tries`` and ``chats``: the low end, what they
        read before tries could grow) up to :data:`MOST_TRIES` (``tries_high``).

        :raises CaseError: An unknown set or case, or a bad case file.
        :raises LookupError: An unknown target.
        """
        docs = self.targets.docs
        load_set(docs, target, set_name)
        every = load_cases(docs, target, set_name)
        chosen = [load_case(docs, target, set_name, c) for c in cases] if cases else every
        found = isolated.footing(self.targets.config, target, self._declared(target))
        if (why := isolated.refusal(self.targets.config, target, found)) is not None:
            missing = (
                isolated.page_refusal(target, found)
                if page_map_of(self.targets.conf(target)) is not None
                else None
            )
            raise isolated.NotIsolated(why, missing)
        capabilities = isolated.capabilities_on(self._declared(target), found)
        # Through the app's page, when it has a page map: refusal above read it already.
        page = page_map_of(self.targets.conf(target))
        if page is not None:
            capabilities = page_capabilities(capabilities, page)
        refused = self._refused(target, chosen, capabilities, page=page is not None)
        chosen = [c for c in chosen if c.case not in refused]
        least = {c.case: tries or int(c.limits["tries"]) for c in chosen}
        most = {c.case: tries or MOST_TRIES for c in chosen}
        now = time.time()
        self._plans = {k: p for k, p in self._plans.items() if p.expires > now}
        token = secrets.token_urlsafe(18)
        scope = "all" if {c.case for c in every} <= set(least) else "some"
        self._plans[token] = _Plan(
            target, set_name, tuple(least), tries, scope, now + TOKEN_SECONDS
        )
        return {
            "token": token,
            "target": target,
            "set": set_name,
            "cases": len(chosen),
            "tries": sum(least.values()),
            "tries_high": sum(most.values()),
            "chats": sum(least[c.case] * messages(c) for c in chosen),
            # Tests this app can't serve, left out of the run.
            "refused": [{"case": case, "why": why} for case, why in refused.items()],
            "isolation_note": capabilities.isolation_note,
            # How its tries will be kept apart: ``proven`` plays on evalution's own copy.
            "isolation": found.run_label(),
            # How its tests play, what a try keeps and roughly how long one takes, in words.
            "plays": plays(
                target,
                chosen,
                self.all(target, set_name),
                page,
                only=page_only(self.targets.conf(target)),
            ),
        }

    def _checkout(self, target: str) -> Checkout:
        """The target's code folder as it stands; nothing when it has none, or it doesn't read."""
        code = self.targets.code(target)
        try:
            return code.checkout() if code is not None else Checkout()
        except OSError:
            return Checkout()

    async def preview(self, target: str) -> dict[str, Any]:
        """What a run of *target* would play, said before it starts (ADR 0011): the version, and
        whether the images on this computer look like it. Read-only: nothing is started, and a
        part that can't be read is left unvouched rather than refused."""
        checkout = await asyncio.to_thread(self._checkout, target)
        try:
            found = await asyncio.to_thread(
                isolated.footing, self.targets.config, target, self._declared(target)
            )
            said = await isolated.preview(self.targets.config, target, found, checkout)
        except Exception as exc:  # what the Run dialog says, never a reason not to run
            _logger.warning("evals: what %s would play not read: %s", target, exc)
            said = replace(
                unstarted(checkout), why_unverified="evalution couldn't tell what would play"
            )
        return {
            **{k: v for k, v in said.as_dict().items() if k != "played"},
            "said": described(said.as_dict(), now=True),
        }

    def _declared(self, target: str) -> Capabilities:
        """What *target*'s adapter declares, before any plan is laid over it."""
        return self.targets.adapter(target).capabilities

    def _refused(
        self,
        target: str,
        cases: Sequence[Case],
        capabilities: Capabilities | None = None,
        *,
        page: bool = False,
    ) -> dict[str, str]:
        """``test -> why it can't run on *target*``, as it plays with *capabilities* (by default
        what its adapter declares), through the app's page when *page*."""
        capabilities = capabilities or self._declared(target)
        traced = self.targets.source(target) is not None
        found = {c.case: why_not(c, capabilities, traced, page=page) for c in cases}
        return {case: why for case, why in found.items() if why}

    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def playing(self, target: str) -> bool:
        """Whether a run is playing on *target* now; its analysis, after, needs nothing of it."""
        return self.busy() and self._playing == target

    async def start(
        self, token: str, base: Mapping[str, Any] | None = None, env: str | None = None
    ) -> dict[str, Any]:
        """Start the run an estimate's *token* describes, graded with its set's pass marks; the
        token is spent. *base* is the run it will be compared with, which a case that moved from
        it plays on against; without one, the run a comparison would choose by default. *env*
        names where the app played (``staging``); the run is ``hosted`` when no folder of this
        computer is read, which is how a hosted server plays, else ``local``.

        :raises RunBusyError: A run or an analysis is going.
        :raises KeyError: An unknown or expired token.
        :raises CaseError: A case file that no longer reads.
        :raises ValueError: *env* isn't a name (:func:`~evalution.results.record.environment`).
        """
        place = environment("local" if self.targets.folders else "hosted", env)
        if self.busy() or self.probing or not self.lock.acquire():
            raise RunBusyError
        plan = self._plans.pop(token, None)
        if plan is None or plan.expires < time.time():
            self.lock.release()
            raise KeyError(token)
        docs = self.targets.docs
        try:
            eval_set = load_set(docs, plan.target, plan.set)
            cases = [load_case(docs, plan.target, plan.set, stem) for stem in plan.cases]
            if not cases:
                raise CaseError("none of these tests can run on this app")
        except CaseError:
            self.lock.release()
            raise
        started = time.time()
        stamp = datetime.fromtimestamp(started, UTC).strftime("%Y%m%d-%H%M%S")
        doc: dict[str, Any] = {
            "schema": SCHEMA,
            "rules": RULES_VERSION,
            "judge": self._judge(eval_set.grader).model_dump(mode="json"),
            "run": f"{stamp}-{secrets.token_hex(2)}",
            "target": plan.target,
            "set": plan.set,
            "environment": place,
            # How its tries are kept apart: set once the run knows what it stands on.
            "isolation": run_isolation(),
            # How it reaches the app: its API, its page beside it, or its page alone.
            "plays": _plays(self.targets.conf(plan.target)),
            "scope": plan.scope,
            "started_at": started,
            # How a benchmark was chosen to play, when a plan chose it.
            "plan": _plan_beside(docs, plan.target, plan.set),
            "ended_at": None,
            "status": "running",
            # Short hash of the system prompt the target's first model call was sent.
            "prompt": "",
            # The code it played on: the last commit, and the hash of every source file's hash.
            "commit": None,
            "code": None,
            # The version it played and whether evalution can vouch for it (ADR 0011), set once
            # it knows what it plays on: evalution's copy, or the app as it runs.
            "version": None,
            "pass_marks": dict(eval_set.pass_marks),
            "set_version": eval_set.version,
            "case_versions": {c.case: c.version for c in cases},
            "error": None,
            "stop_reason": None,
            "failure": None,
            # The adapter sessions open now, so a restart can close them.
            "sessions": [],
            "analyzing": False,
            "analysis": None,
            "analysis_error": None,
            "current": None,
            "cases": {
                c.case: {
                    "name": c.name,
                    "about": c.about,
                    "source": c.source,
                    "stale": None,
                    "planned": plan.tries or int(c.limits["tries"]),
                    "must_hold": c.must_hold,
                    # The test as it was played: a later change to its file doesn't change this.
                    "case": c.raw,
                    "turns": [
                        {"message": t.message, "files": list(t.files)}
                        if t.is_message
                        else {"break": "conversation"}
                        for t in c.turns
                        if t.is_message or t.new_conversation
                    ],
                    "expected": c.raw.get("tools") or {},
                    "reply_rubric": list(c.rubric),
                    "tries": [],
                }
                for c in cases
            },
        }
        doc["totals"] = totals(doc)
        # Reserved before the first await: a second start sees busy() and is refused.
        self._running = doc["run"]
        self._playing = plan.target
        self._stop_reason = None
        # Stored before it is answered: whoever reads the run next must find it.
        try:
            await self.store.save(doc)
        except BaseException:
            self._running = self._playing = None
            self.lock.release()
            raise
        self._go(self._play(doc, eval_set, cases, plan.tries, base))
        self._stop_watch = asyncio.get_running_loop().create_task(self._heed_stop(doc["run"]))
        return summary(doc)

    async def _heed_stop(self, run: str) -> None:
        """Stop *run* when asked from another process: a run started with ``evalution run`` is
        stopped from the page's server, which doesn't hold it."""
        task = self._task
        assert task is not None
        while not task.done():
            if self.store.stop_asked(run):
                self._stop_reason = STOPPED_BY_ADMIN
                task.cancel()
                break
            await asyncio.sleep(STOP_POLL_SECONDS)
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait([task])
        self.store.clear_stop(run)

    def _go(self, work: Coroutine[Any, Any, None]) -> None:
        """Run *work* in the background, holding the run lock until it ends."""
        self._task = asyncio.get_running_loop().create_task(work)
        self._task.add_done_callback(lambda _: self.lock.release())

    def _judge(self, key: str) -> JudgeStamp:
        """Who grades under model key *key*."""
        model = self.llm.model_id(key) if self.llm else None
        return JudgeStamp(key=key, model=model, prompt_hash=PROMPT_HASH)

    def estimate_regrade(self, run: str) -> dict[str, Any]:
        """How many tries grading *run* again from its evidence takes, with the token that does it.

        :raises ValueError: No such run, or one without evidence or a copy of its tests.
        """
        doc = self.load(run)
        if doc is None:
            raise ValueError(f"no run {run}")
        if self.caller(doc["judge"]["key"]) is None:
            raise ValueError("no model is set up to grade with")
        kept = self.store.evidence(run)
        if not kept:
            raise ValueError(f"run {run} kept no evidence to grade from")
        tries = [
            e for e in kept if (doc["cases"].get(e.case) or {}).get("case") and _to_grade(doc, e)
        ]
        if not tries:
            raise ValueError(f"run {run} has nothing to grade again")
        judge = labels.grader_of(self._judge(doc["judge"]["key"]))
        # The labelled examples of other runs another grader graded are graded again too.
        examples = labels.of(self.targets.docs, doc["target"], doc["set"])
        again = [e for e in examples if e["run"] != run and e["judge"] != judge]
        now = time.time()
        token = secrets.token_urlsafe(18)
        self._regrades[token] = (run, now + TOKEN_SECONDS)
        return {"token": token, "tries": len(tries) + len(again)}

    async def regrade(self, token: str) -> dict[str, int]:
        """Grade the replies of an estimate's run again from its evidence, on the model its set
        grades with now. Each try gets a new judgement; the code's checks stay as they were, and a
        try the grader failed on keeps what it had. ``{graded, failed}``: how many of each.

        :raises KeyError: An unknown or expired token.
        :raises RunBusyError: A run or an analysis is going.
        """
        run, expires = self._regrades.pop(token, ("", 0.0))
        if expires < time.time():
            raise KeyError(token)
        if self.busy() or not self.lock.acquire():
            raise RunBusyError
        try:
            doc = self.load(run)
            assert doc is not None
            key = doc["judge"]["key"]
            caller = self.caller(key)
            stamp = self._judge(key)
            found = {"graded": 0, "failed": 0}
            for evidence in self.store.evidence(run):
                graded = await self._regrade_try(doc, evidence, caller, stamp)
                if graded is not None:
                    found["graded" if graded else "failed"] += 1
            if found["graded"]:
                doc["judge"] = stamp.model_dump(mode="json")
                await self._trust(doc)
                doc["totals"] = totals(doc)
                await self.store.save(doc)
            return found
        finally:
            self.lock.release()

    async def _regrade_try(
        self, doc: dict[str, Any], evidence: TryEvidence, caller: Caller | None, stamp: JudgeStamp
    ) -> bool | None:
        """Whether its new grading stood; ``None`` for a try there was nothing to grade of."""
        entry = doc["cases"].get(evidence.case) or {}
        attempt = next((t for t in entry.get("tries") or [] if t["n"] == evidence.n), None)
        if attempt is None or not _to_grade(doc, evidence):
            return None
        before = current(attempt)
        code = [c for c in before.checks if c.decided_by is Decider.CODE]
        graded = await self._graded_again(doc, attempt, evidence, caller)
        if any(c.unjudged is WhyUnjudged.JUDGE_FAILED for c in graded.checks):
            return False
        add_judgement(attempt, judgement([*code, *graded.checks], stamp, before.route))
        if graded.retried:
            attempt.setdefault("retried", []).append({"what": "grader", "why": graded.retried})
        attempt["rubric"] = graded.lines
        attempt["reply"] = graded.score
        attempt["memory"] = with_fold(
            await grade_memory(caller, evidence.memory, evidence.model_calls, _last_turn(evidence)),
            attempt.get("context") or [],
        )
        return True

    async def _graded_again(
        self,
        doc: Mapping[str, Any],
        attempt: Mapping[str, Any],
        evidence: TryEvidence,
        caller: Caller | None,
    ) -> Graded:
        """The reply of a kept try graded again by *caller*, as the run's copy of its test asks."""
        entry = doc["cases"][evidence.case]
        case = parse_case(entry["case"], f"{doc['run']}/{evidence.case}", evidence.case)
        states = [s for s in attempt.get("steps") or [] if _is_state_step(s)]
        return await grade_reply(
            caller,
            evidence,
            case.reference,
            case.rubric,
            check_lines=[check_line(Step(**s)) for s in states],
            need=case.reply_pass,
            mark=pass_marks(doc)["reply"],
            decider=self.caller("strong") if is_benchmark(doc["set"]) else None,
        )

    async def _trust(self, doc: dict[str, Any]) -> None:
        """After *doc* was graded: its set's labelled examples as its grader says them, then *doc*
        and the set's other runs read again with what they say of the grader now
        (:mod:`evalution.runs.trust`). Never raises: the run stands without it."""
        try:
            await self._recheck_labels(doc, JudgeStamp.model_validate(doc["judge"]))
            trust.apply(self.targets.docs, doc)
            await trust.apply_stored(self.store, self.targets.docs, doc["target"], doc["set"])
        except Exception:
            _logger.warning("evals: run %s: labelled examples not read", doc["run"], exc_info=True)

    async def _recheck_labels(self, doc: Mapping[str, Any], stamp: JudgeStamp) -> None:
        """The set's labelled examples as the grader *stamp* says them: those of *doc* read from it,
        the others graded again from their run's evidence, as a regrade grades, when another model
        or other prompts graded them. One the grader can't decide keeps what it had."""
        docs, judge, caller = self.targets.docs, labels.grader_of(stamp), self.caller(stamp.key)
        kept: dict[str, tuple[dict[str, Any] | None, list[TryEvidence]]] = {}
        for example in labels.of(docs, doc["target"], doc["set"]):
            where = (example["case"], example["try"])
            if example["run"] == doc["run"]:
                found = current(trust.try_of(doc, *where))
                said, by = (
                    trust.said_on(found.checks, example["line"]),
                    labels.grader_of(found.judge),
                )
            elif example["judge"] != judge and caller is not None:
                if example["run"] not in kept:
                    run = example["run"]
                    kept[run] = (self.load(run), await asyncio.to_thread(self.store.evidence, run))
                other, evidence = kept[example["run"]]
                played = next((e for e in evidence if (e.case, e.n) == where), None)
                if other is None or played is None:
                    continue
                graded = await self._graded_again(
                    other, trust.try_of(other, *where), played, caller
                )
                said, by = trust.said_on(graded.checks, example["line"]), judge
            else:
                continue
            if said is not None and (said, by) != (example["graded"], example["judge"]):
                labels.regraded(docs, doc["target"], example, said, by)

    async def wait(self) -> None:
        """Until the running task (a run and its analysis) ends."""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(self._task)

    async def analyze(self, run: str) -> dict[str, Any] | None:
        """Analyse *run* again in the background; its summary, or ``None`` for no such run.

        :raises RunBusyError: A run or an analysis is going.
        """
        if self.busy():
            raise RunBusyError
        doc = await asyncio.to_thread(self.load, run)
        if doc is None:
            return None
        if self.busy() or doc["status"] == "running" or not self.lock.acquire():
            raise RunBusyError
        doc["analyzing"] = True
        self._go(self._analyze(doc))
        return summary(doc)

    async def _analyze(self, doc: dict[str, Any]) -> None:
        """Analyse *doc*'s failed tries and the run, into the run store. Never raises."""
        doc["analyzing"] = True
        doc["analysis"] = None
        doc["analysis_error"] = None
        doc["report"] = None
        for entry in doc["cases"].values():
            for attempt in entry["tries"]:
                attempt["analysis"] = None
        caller = self.caller(doc["judge"]["key"])
        try:
            await self.store.save(doc)
            kept = await asyncio.to_thread(self.store.evidence, doc["run"])
            earlier = await asyncio.to_thread(self.all, doc["target"], doc["set"])
            await asyncio.to_thread(_mark_divergences, self.store, doc, kept, earlier)
            by_try = {(e.case, e.n): index_of([e]) for e in kept}
            by_case = {stem: index_of(e for e in kept if e.case == stem) for stem in doc["cases"]}
            await analyze_run(caller, doc, by_try, index_of(kept))
            await self.store.save(doc)
            ran = [r for r in earlier if r["started_at"] < doc["started_at"]]
            await write_report(caller, doc, ran[0] if ran else None, by_case)
        except Exception:
            _logger.warning("evals: run %s not analysed", doc["run"], exc_info=True)
        finally:
            failed = _failed_tries(doc)
            if failed and caller is None:
                doc["analysis_error"] = "No model is set up yet: choose one in Settings, Model."
            elif failed and (doc["analysis"] is None or any(not t["analysis"] for t in failed)):
                doc["analysis_error"] = "Part of the analysis didn't finish. Try again."
            doc["analyzing"] = False
            await asyncio.shield(self.store.save(doc))

    async def stop(self, run: str) -> dict[str, Any] | None:
        """Stop *run* if it is the one running; its summary, or ``None`` for no such run."""
        if run == self._running and self.busy() and self._task is not None:
            self._stop_reason = STOPPED_BY_ADMIN
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            doc = self.load(run)
            return summary(doc) if doc else None
        doc = self.load(run)
        if doc and doc.get("status") == "running":
            # Not ours: it's another process's to stop. It looks for this while it plays.
            self.store.ask_stop(run)
        return summary(doc) if doc else None

    async def _save(self, doc: Mapping[str, Any]) -> None:
        """Into the run store, and once the run ended into the target's sink, if it has one (a
        sink's failure is logged by the sink, never raised)."""
        await self.store.save(doc)
        if doc["status"] != "running" and (sink := self.targets.sink(doc["target"])):
            await sink.save(doc)

    async def _play(
        self,
        doc: dict[str, Any],
        eval_set: EvalSet,
        cases: Sequence[Case],
        tries: int | None,
        base: Mapping[str, Any] | None,
    ) -> None:
        """Play every try of *cases*: one case after another, or up to the target's
        ``max_parallel`` cases at once when its adapter declares ``parallel``, each slot in its own
        session; a case's tries stay in order in one slot. *tries* of each, else until they say
        something against *base*.

        On a target with a proven isolation plan, each slot plays on evalution's own copy of the
        app, put back and checked before every try, or before each test's first as its test setup
        says, which also says how many copies play at once (:mod:`evalution.runs.isolated`)."""
        name = doc["target"]
        target = self.targets.adapter(name)
        source = self.targets.source(name)
        sessions: dict[int, Session] = {}
        # Each slot's adapter, when it plays on a copy of the app; else the target's own.
        on: dict[int, Target] = {}
        copies: isolated.Copies | None = None

        def pairs() -> list[tuple[Target, Session]]:
            return [(on.get(i, target), s) for i, s in sessions.items()]

        try:
            await self.recover()
            await self.store.save(doc)
            doc.update(await asyncio.to_thread(impact.record_run, self.targets, name))
            checkout = await asyncio.to_thread(self._checkout, name)
            await drift.check_quietly(self.targets, name)
            found = drift.read(self.targets, name)
            for case in cases:
                doc["cases"][case.case]["stale"] = drift.stale(case, found)
            bases = await asyncio.to_thread(self._bases, doc, base)
            parallel = target.capabilities.parallel
            slots = min(self.targets.conf(name).max_parallel, len(cases)) if parallel else 1
            footing = await self._footing(doc, name, target)
            doc["isolation"] = footing.run_label()
            if not footing.managed:
                # The app as it runs: whatever it was started from.
                doc["version"] = unstarted(checkout).as_dict()
            else:
                doc["current"] = {
                    "case": "",
                    "name": "Starting evalution's copy of the app",
                    "try": 1,
                }
                await self.store.save(doc)
                config = self.targets.config
                setup = await asyncio.to_thread(setup_for, config, name)
                # On copies, the test setup says how many play side by side.
                slots = min(setup.copies, len(cases)) if parallel else 1
                copies = isolated.Copies(config, name, footing, self.targets.store, setup)
                began = time.monotonic()
                await copies.start(slots)
                # How long starting the copies took: the Run dialog's rough figure next time.
                doc["copy_start_seconds"] = round(time.monotonic() - began, 1)
                # What the copies play: their images, or the working tree.
                version = await isolated.played(copies, checkout, doc.get("code"))
                doc["version"] = version.as_dict()
                doc["isolation"] = footing.run_label(copies.person)
                on = {s.index: s.adapter for s in copies.slots if s.adapter is not None}
                await self._check_page_map(doc, name, on.get(0))
            if self.targets.config.check_adapters:
                doc["current"] = {"case": "", "name": "Checking the connection", "try": 1}
                await self.store.save(doc)
                await self._check_first(name, on.get(0, target), source)
            grader = self.caller(eval_set.grader)
            filler = (
                filler_writer(self.caller(eval_set.filler))
                if any(t.filler for c in cases for t in c.turns)
                else None
            )
            # On a copy, a try opens its own session once the copy is put back: a session kept
            # from the try before would name what putting it back took away.
            reset = target.capabilities.reset and copies is None
            # What the turns send, written out where the adapter can be handed paths.
            set_dir = await asyncio.to_thread(
                unpack_files,
                self.targets.docs,
                name,
                eval_set.set,
                self.targets.config.data_dir / "sent" / name / eval_set.set,
            )
            queue = list(cases)
            # Trace values earlier tries played on: their spans aren't the next one's.
            reused: set[str] = set()
            adapter_failures = 0
            hangs = 0

            def opened() -> None:
                doc["sessions"] = [s.as_dict() for s in sessions.values()]

            async def slot(index: int) -> None:
                nonlocal adapter_failures, hangs
                key = doc["run"] if slots == 1 else f"{doc['run']}-{index + 1}"
                adapter = on.get(index, target)
                while queue:
                    case = queue.pop(0)
                    entry = doc["cases"][case.case]
                    n, more = 0, True
                    while more:
                        n += 1
                        doc["current"] = {"case": case.case, "name": case.name, "try": n}
                        label = footing.try_label()
                        if copies is not None and (n == 1 or copies.reset == "try"):
                            doc["current"]["stage"] = "restoring"
                            await self.store.save(doc)
                            label = await copies.restore(index)
                        elif copies is not None:
                            # Put back before the test's first try only, as the setup says.
                            label = copies.unrestored()
                        if isinstance(adapter, PageTarget):
                            # What the try keeps of the page: the test's own proof over the map's.
                            adapter.prove(case.page_proof)
                        if index not in sessions:
                            sessions[index] = await _in_time("open a session", adapter.open(key))
                            opened()
                        if reset:
                            await _in_time("reset", adapter.reset(sessions[index]))
                        await self.store.save(doc)
                        attempt, played = await self._try(
                            adapter,
                            sessions[index],
                            source,
                            grader,
                            filler,
                            doc,
                            set_dir,
                            case,
                            n,
                            reused,
                            unclean=label["problem"] if copies is not None else None,
                        )
                        attempt["isolation"] = label
                        entry["tries"].append(attempt)
                        doc["totals"] = totals(doc)
                        if tries:
                            more = n < tries
                        else:
                            least = int(case.limits["tries"])
                            more = one_more(verdict_of(entry), least, bases.get(case.case))
                        entry["planned"] = max(entry["planned"], n + 1) if more else n
                        if played.failure is Failure.REFUSED:
                            raise AppRefused(attempt["error"] or "")
                        if played.gone:
                            raise TargetGone(attempt["error"] or "")
                        if played.failure is Failure.PAGE_MAP:
                            await self._map_broke(name, played, (doc["run"], case.case, n))
                            raise PageMapBroken(attempt["error"] or "")
                        lost_adapter = played.failure is Failure.ADAPTER
                        adapter_failures = adapter_failures + 1 if lost_adapter else 0
                        if adapter_failures >= ADAPTER_FAILURES:
                            raise AdapterBroken(attempt["error"] or "")
                        hung = played.failure is Failure.TIMED_OUT and not played.turns
                        hangs = hangs + 1 if hung else 0
                        if hangs >= HANGS:
                            raise KeepsHanging(KEEPS_HANGING)
                        left_going = (
                            lost_adapter
                            or played.failure is Failure.TIMED_OUT
                            or (played.failure is Failure.TRANSIENT and played.heard)
                        )
                        if left_going and adapter.capabilities.background and not played.cancelled:
                            raise StillWorking(STILL_WORKING)
                        if not reset:
                            await close_quietly(adapter, sessions.pop(index))
                            opened()
                        await self.store.save(doc)

            playing = [asyncio.ensure_future(slot(i)) for i in range(slots)]
            try:
                await asyncio.gather(*playing)
            finally:
                for task in playing:
                    task.cancel()
                await asyncio.gather(*playing, return_exceptions=True)
            doc["status"] = "done"
        except asyncio.CancelledError:
            doc.update(status="stopped", stop_reason=self._stop_reason or SERVER_SHUTDOWN)
            await self._finish(pairs(), doc, copies)
            raise
        except (isolated.NotIsolated, isolated.CopyLost) as exc:
            doc.update(status="failed", failure=APP_NOT_READY, stop_reason=str(exc), error=str(exc))
        except AdapterBroken as exc:
            _logger.warning("evals: run %s: the adapter isn't reading the app", doc["run"])
            doc.update(
                status="failed", failure=ADAPTER_BROKEN, stop_reason=str(exc), error=str(exc)
            )
        except PageMapBroken as exc:
            _logger.warning("evals: run %s: the page map names what the page lacks", doc["run"])
            doc.update(
                status="failed", failure=PAGE_MAP_BROKEN, stop_reason=str(exc), error=str(exc)
            )
        except KeepsHanging as exc:
            doc.update(status="failed", failure=APP_NOT_READY, stop_reason=str(exc), error=str(exc))
        except StillWorking as exc:
            doc.update(status="failed", failure=OTHER_FAILURE, stop_reason=str(exc), error=str(exc))
        except AppRefused as exc:
            forget_setup(self.targets.config.data_dir, name)
            doc.update(status="failed", failure=APP_NOT_READY, stop_reason=str(exc), error=str(exc))
        except TargetGone as exc:
            _logger.warning("evals: run %s lost its target session", doc["run"])
            doc.update(
                status="failed",
                failure=APP_NOT_READY,
                stop_reason=TARGET_GONE,
                error=str(exc) or TARGET_GONE,
            )
        except Exception as exc:
            _logger.warning("evals: run %s failed", doc["run"], exc_info=True)
            message = net.said(exc)
            if failure_of(exc) is Failure.REFUSED:
                message = f"The app refused the credentials: {message}"
            failure = APP_NOT_READY if isinstance(exc, APP_NOT_READY_ERRORS) else OTHER_FAILURE
            doc.update(status="failed", failure=failure, error=message, stop_reason=message)
        await self._trust(doc)
        failed = bool(_failed_tries(doc))
        # Every run that ends on its own explains itself: its failed tries, and a report when a
        # model can write one. One stopped (by the admin or its cap) is left as it is. Set before
        # the run reads ended, so no reader sees it finished with analysis to come.
        played = any(c["tries"] for c in doc["cases"].values())
        doc["analyzing"] = doc["status"] in ("done", "failed") and (
            failed or (played and self.caller(doc["judge"]["key"]) is not None)
        )
        await self._finish(pairs(), doc, copies)
        if doc["analyzing"]:
            await self._analyze(doc)

    async def _footing(self, doc: dict[str, Any], name: str, target: Target) -> isolated.Footing:
        """What the run stands on: a proven plan, rehearsed again first when its evidence changed.

        :raises NotIsolated: It has none, and ``isolation.require_proven`` asks for one.
        """
        config = self.targets.config
        found = await asyncio.to_thread(isolated.footing, config, name, target.capabilities)
        # Refused before rehearsing too: a rehearsal can't make a refused run possible.
        if (why := isolated.refusal(config, name, found)) is not None:
            raise isolated.NotIsolated(why)
        if found.to_rehearse:
            doc["current"] = {"case": "", "name": "Rehearsing the isolation plan again", "try": 1}
            await self.store.save(doc)
            found = await isolated.rehearse_again(config, name, found)
        if (why := isolated.refusal(config, name, found)) is not None:
            raise isolated.NotIsolated(why)
        return found

    async def _check_page_map(self, doc: dict[str, Any], name: str, target: Target | None) -> None:
        """Before a run plays through the page, a map that a commit left out of date, that the last
        check found broken, or whose evidence changed, is checked on the copy: every element a
        turn needs is found, none is acted on, no message is sent. It is saved approved again, or
        broken.

        :raises PageMapBroken: The page lacks what the map names; the run ends ``page_map_broken``.
        """
        if not isinstance(target, PageTarget):
            return
        kept = await asyncio.to_thread(pagemaps.to_check, self.targets, name)
        if kept is None:
            return
        doc["current"] = {"case": "", "name": "Checking the page map again", "try": 1}
        await self.store.save(doc)
        found = await learn.check(target, name, self.targets.files)
        missing = found.missing
        await asyncio.to_thread(
            pagemaps.checked, self.targets, name, kept, missing, found.shot, found.at
        )
        if missing:
            raise PageMapBroken(
                f"The page map no longer matches {name}'s page: checked on evalution's copy "
                f"before playing, the page has no {', '.join(missing)}. Re-learn the page to "
                "propose a new map."
            )

    async def _map_broke(self, name: str, played: Played, at: tuple[str, str, int]) -> None:
        """A try found the page without what its map names: the map in use is marked broken,
        saying what wasn't there, with the try's last screenshot of the page, so Home says so
        and the next run checks it first."""
        error = played.error or ""
        missing = error.split("the page has no ", 1)[1] if "the page has no " in error else error
        page = played.page
        shots = [t.shot for t in page.turns if t.shot] if page is not None else []
        shot = shots[-1] if shots else None
        where = {"run": at[0], "case": at[1], "n": at[2]} if shot else None
        with contextlib.suppress(OSError, ValueError):
            await asyncio.to_thread(
                pagemaps.found_broken, self.targets, name, [missing], shot, where
            )

    def _bases(self, doc: Mapping[str, Any], base: Mapping[str, Any] | None) -> dict[str, Verdict]:
        """Each case's result in the run *doc* will be compared with: *base*, else the one a
        comparison would choose (:func:`default_base`), as far as *doc* is known when it starts."""
        if base is None:
            others = self.store.all(doc["target"], doc["set"])
            chosen = default_base(doc, others)
            base = next((r for r in others if r["run"] == chosen), None)
        if base is None:
            return {}
        return {
            stem: verdict_of(base["cases"][stem]) for stem in doc["cases"] if stem in base["cases"]
        }

    async def _check_first(self, name: str, target: Target, source: TraceSource | None) -> None:
        """One real turn before the first run on this adapter and setup, so a broken one fails in
        seconds and not after a run's worth of waiting.

        :raises AdapterBroken: It doesn't work.
        :raises AppRefused: The app turned the credentials down.
        :raises TargetGone: The app wasn't there to show either way.
        """
        key = setup_key(target, self.targets.config.targets[name])
        passed = passed_setups(self.targets.config.data_dir)
        if passed.get(name) == key:
            return
        traces = self.targets.config.traces
        found = await check_adapter(target, source, traces.settle_seconds, traces.stuck_seconds)
        if found.adapter_failed:
            # As in a run: it takes two in a row.
            found = await check_adapter(target, source, traces.settle_seconds, traces.stuck_seconds)
        if found.page_map:
            raise PageMapBroken("; ".join(found.problems))
        if found.refused:
            raise AppRefused("; ".join(found.problems))
        if found.app_down:
            raise TargetGone("; ".join(found.problems))
        if not found.ok:
            raise AdapterBroken("; ".join(found.problems))
        remember_setup(self.targets.config.data_dir, name, key)

    async def _finish(
        self,
        sessions: Sequence[tuple[Target, Session]],
        doc: dict[str, Any],
        copies: isolated.Copies | None = None,
    ) -> None:
        """End the run: its sessions closed, each by its own adapter, then the copies of the app
        it played on put back and taken down, then the run saved as it ended."""
        doc["totals"] = totals(doc)
        doc["current"] = None
        self._running = self._playing = None
        doc["ended_at"] = time.time()
        for target, session in sessions:
            # Stopped or failed mid-try: a side-by-side session may still have work going.
            if doc["status"] != "done":
                await asyncio.shield(cancel_quietly(target, session))
            await asyncio.shield(close_quietly(target, session))
        doc["sessions"] = []
        if copies is not None:
            left = await asyncio.shield(copies.close())
            if left:
                _logger.warning("evals: run %s left behind: %s", doc["run"], ", ".join(left))
        await asyncio.shield(self._save(doc))

    async def _drop_passed_proof(self, run: str, evidence: TryEvidence, outcome: Outcome) -> None:
        """Delete a passed try's video, and its screenshots, when its proof keeps them only when
        a try fails. Its evidence still names the files; the try view says why there are none."""
        page, files = evidence.page, self.store.files
        if page is None or files is None or outcome is not Outcome.PASSED:
            return
        names = [
            name
            for name in page.files
            if (name == VIDEO and page.video_policy == "failed")
            or (is_shot(name) and page.screenshot_policy == "failed")
        ]
        for name in names:
            try:
                await asyncio.to_thread(files.delete, run, evidence.case, evidence.n, name)
            except (OSError, ValueError):
                _logger.warning(
                    "evals: %s of a passed try of %s not deleted", name, run, exc_info=True
                )

    async def _try(
        self,
        target: Target,
        session: Session,
        source: TraceSource | None,
        grader: Caller | None,
        filler: FillerWriter | None,
        doc: dict[str, Any],
        set_dir: Path,
        case: Case,
        n: int,
        reused: set[str],
        unclean: str | None = None,
    ) -> tuple[dict[str, Any], Played]:
        async def on_turn(sent: int, planned: int) -> None:
            playing = doc.get("current")
            if playing and playing.get("case") == case.case:
                playing.update(stage="playing")
                if planned > 1:
                    playing.update(turn=sent, turns=planned)
                await self.store.save(doc)

        async def on_stage(stage: str) -> None:
            playing = doc.get("current")
            if playing and playing.get("case") == case.case:
                playing["stage"] = stage
                await self.store.save(doc)

        played = await play_with_retry(
            target,
            session,
            case,
            set_dir,
            filler=filler,
            source=source,
            n=n,
            reused=reused,
            settle=self.targets.config.traces.settle_seconds,
            stuck=self.targets.config.traces.stuck_seconds,
            trace_wait=NO_TRACES_WAIT if doc["run"] in self._no_traces else None,
            on_turn=on_turn,
            on_stage=on_stage,
            unclean=unclean,
            page_files=(
                PageFiles(self.store.files, doc["run"], case.case, n)
                if self.store.files is not None
                else None
            ),
        )
        if played.evidence is not None and played.evidence.trace is TraceStatus.EMPTY:
            self._no_traces.add(doc["run"])
        else:
            self._no_traces.discard(doc["run"])
        evidence = _keepable(played)
        await self.store.keep_evidence(doc["run"], evidence)
        view = played.view
        if not doc["prompt"] and view.context and view.context[0].get("system"):
            doc["prompt"] = hashlib.sha256(view.context[0]["system"].encode()).hexdigest()[:8]
        conf = self.targets.config.targets[doc["target"]]
        automatic = conf.options.get("automatic_tools") or ()
        chosen = chosen_calls(view.calls, automatic, case.tools)
        tool_steps, tools_score = grade_tools(case.tools, chosen)
        spans = timeline(played, tool_steps)
        marks = pass_marks(doc)
        code = code_checks(case, evidence, tool_steps, tools_score, marks)
        if played.failure is not None:
            graded = Graded(score=None)
        elif not case.rubric and not case.reference:
            graded = Graded(score=1.0)
        else:
            await on_stage("grading")
            graded = await grade_reply(
                grader,
                evidence,
                case.reference,
                case.rubric,
                check_lines=[check_line(s) for s in code.states],
                need=case.reply_pass,
                mark=marks["reply"],
                decider=self.caller("strong") if is_benchmark(doc["set"]) else None,
            )
        memory = await grade_memory(
            grader, evidence.memory, evidence.model_calls, _last_turn(evidence)
        )
        checks = [*code.checks, *graded.checks]
        graded_by = (
            JudgeStamp.model_validate(doc["judge"])
            if any(c.decided_by is Decider.MODEL for c in checks)
            else None
        )
        found = judgement(checks, graded_by, code.route)
        await self._drop_passed_proof(doc["run"], evidence, found.outcome)
        attempt = {
            "n": n,
            "seconds": played.seconds,
            "tokens": view.tokens,
            "tools": tools_score,
            "reply": graded.score,
            "memory": with_fold(memory, view.context),
            "judgements": [found.model_dump(mode="json")],
            "steps": [s.as_dict() for s in [*tool_steps, *code.states]],
            # What drafting a case from this try reads; not part of the API's ``Try``.
            "calls": [
                {
                    "id": c.id,
                    "name": c.name,
                    "args": dict(c.args),
                    "start": c.start,
                    "end": c.end,
                    "helper": c.helper,
                }
                for c in view.calls
            ],
            "trace_ids": played.trace_ids or view.trace_ids,
            "rubric": graded.lines,
            "reply_text": played.reply_text,
            "trace": evidence.trace.value,
            "trace_note": evidence.trace_note,
            # Calls the app made by itself, which weren't scored.
            "automatic": [c.id for c in view.calls if c not in chosen],
            "limits_broken": [
                c.why
                for c in code.checks
                if c.kind is CheckKind.LIMIT and c.outcome is Outcome.FAILED
            ],
            "timeline": spans,
            "trace_url": played.trace_url,
            "error": evidence.error or (f"grader: {graded.error}" if graded.error else None),
            "called": evidence.called,
            "retried": [
                *[{**r, "why": net.no_login(str(r.get("why") or ""))} for r in played.retried],
                *([{"what": "grader", "why": graded.retried}] if graded.retried else []),
            ],
            "analysis": None,
            "messages": len(played.turns) + len(played.filler),
            "context": [
                {k: call[k] for k in ("n", "span", "model", "tokens_in", "sections")}
                for call in view.context
            ],
            "trajectory": view.trajectory,
            "models": sorted({call["model"] for call in view.context if call.get("model")}),
        }
        return attempt, played


async def _in_time[T](what: str, work: Coroutine[Any, Any, T]) -> T:
    """*work*, which an adapter doesn't bound by itself; an app that never answers ends the run.

    :raises TargetGone: It didn't end in :data:`SETUP_SECONDS`.
    """
    try:
        return await asyncio.wait_for(work, SETUP_SECONDS)
    except TimeoutError as exc:
        raise TargetGone(f"the app didn't {what} in {SETUP_SECONDS:.0f} s") from exc


def _keepable(played: Played) -> TryEvidence:
    """*played*'s evidence with no login or query in its errors: an adapter's exception may quote
    the URL it called, and this goes into the stored run and to the model."""
    evidence = played.evidence
    assert evidence is not None
    return evidence.model_copy(
        update={
            "error": net.no_login(evidence.error) if evidence.error else None,
            "turns": tuple(
                t.model_copy(update={"error": net.no_login(t.error)}) if t.error else t
                for t in evidence.turns
            ),
        }
    )


def _is_state_step(step: Mapping[str, Any]) -> bool:
    return str(step.get("expected") or "").startswith("state:")


def _to_grade(doc: Mapping[str, Any], evidence: TryEvidence) -> bool:
    """Whether a kept try has a reply a grader could read: one that played through, of a test with
    a rubric or a reference."""
    raw = ((doc["cases"].get(evidence.case) or {}).get("case") or {}).get("reply") or {}
    return evidence.failure is None and bool(raw.get("rubric") or raw.get("reference"))


def _mark_divergences(
    store: RunStore,
    doc: dict[str, Any],
    kept: Sequence[TryEvidence],
    others: Sequence[Mapping[str, Any]],
) -> None:
    """Write each test's first difference between a passing and a failing try on *doc*, before
    its analysis reads it; the passing try may be from the run a comparison would choose."""
    chosen = default_base(doc, others)
    base = next((r for r in others if r["run"] == chosen), None)
    base_kept = store.evidence(base["run"]) if base else []
    for stem, found in divergence.of_run(doc, kept, base, base_kept).items():
        doc["cases"][stem]["divergence"] = found


def _failed_tries(doc: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The tries a run has something to explain about: never the ones no one could judge."""
    return [
        t for c in doc["cases"].values() for t in c["tries"] if current(t).outcome is Outcome.FAILED
    ]
