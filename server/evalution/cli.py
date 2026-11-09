"""``evalution serve | run | accept | sets | learn | profile | hooks | migrate | regrade |
backup | upgrade | restore | adapter | demo`` (docs/architecture.md, "Layout").

* ``serve``: the API, the page and the trace intake, on 127.0.0.1:8080. There is no login: it
  answers only this computer, unless ``--host`` names another address on purpose (it then warns:
  anyone who can reach that address can use it).
* ``run --target T --set S [--case C] [--tries N] [--yes] [--sets [DIR]]``: a run in this
  process. ``--sets`` first imports T's tests from DIR (default the config's ``sets_dir``), so a
  fresh database in CI plays the repo's tests. Prints the estimate and asks before anything
  plays, unless ``--yes``; then one row per case, the totals and each failed try's analysis.
  ``--tries`` plays exactly N of each case; without it each plays until its tries say something
  (:mod:`evalution.runs.sampling`), against ``--against`` when given. Ctrl-C stops the run.
  Exits 1 when a case did not pass, which a test no one could judge didn't.
* ``run ... --server URL [--token TOKEN] [--env NAME]``: play an app where it runs, on the tests
  of the workspace at URL, and upload the run there after (:mod:`evalution.remote`). The token is
  a workspace API token, else ``$EVALUTION_TOKEN``; ``--env`` names where the app runs.
* ``run ... --against FILE [--summary MD] [--page URL]``: compare with a baseline (``run --save``)
  instead; ``--summary`` also writes that comparison as markdown, for a pull request, each test
  linked to the run on the page at URL when there is one (:mod:`evalution.results.markdown`).
* ``accept TEST --baseline FILE [--run RUN]``: take TEST's result from RUN (default: the newest
  that played it) into the baseline, leaving every other test's (:mod:`evalution.results.baseline`).
* ``sets export|import [DIR] [--target T]``: the tests as files, in DIR (default the config's
  ``sets_dir``): ``<target>/<set>/set.json``, ``*.case.json`` and the set's files. Import
  replaces each set of the same name whole (:mod:`evalution.suite.store`).
* ``migrate``: rewrite the stored runs as today's schema.
* ``backup``, ``upgrade``, ``restore BACKUP``: a private copy of what is kept; that copy, then the
  stored runs rewritten and shown to still read; and putting a copy back (:mod:`evalution.upgrade`).
* ``adapter test NAME --target T [--yes]``: an adapter's contract over a real target: says how many
  messages that spends and asks first. ``adapter new NAME``: a starter in the adapters' folder
  (:mod:`evalution.adapters.contract`).
* ``regrade RUN --rules | --judge [--yes]``: judge a stored run again. ``--rules`` reads its checks
  under today's rules: free and offline. ``--judge`` grades its replies again from its evidence:
  new model calls, so it says how many tries and asks first, and a run without evidence is refused.
* ``learn --report T [--json]``: the measures of T's last learn, from its sessions: per stage
  (code map, read, modules, areas, overview, other) its seconds, model calls, tokens and
  characters in and out (:mod:`evalution.writer.usage`). Free: the baseline a change to the
  learn is checked against.
* ``profile --target T``: the digest of the target's recent traces (no model call).
* ``stores FOLDER [--target T [--port P] [--yes]]``: where the app in FOLDER keeps state (a
  prototype, :mod:`evalution.isolation`). Alone, a read of the folder: free. With ``--target``,
  one probe message carrying a canary goes through T's adapter while the process listening on P
  (default the target's port) is watched, and what it wrote is lined up with what the code names.
* ``isolate --target T [--keep] [--yes] [--slot N]``: start evalution's own copy of T's app,
  find its stores with one probe, draft a reset plan and rehearse it on the copy with a second
  (:mod:`evalution.isolation.isolate`). Shows the copy and asks before any message is sent. Prints
  the rehearsal's account and where the plan is kept; exits 1 when it isn't proven. The copy is
  taken down after, unless ``--keep``.
* ``isolation down [PROJECT]``: take down a copy (only ``eval-*``); without PROJECT, list them.
  ``isolation show --target T``: the target's saved plan.
* ``demo ui [--no-open]``: two tests played through the made-up app's page on a copy, in a scratch
  folder of their own, then the failing one's result opened in the browser; Ctrl-C stops it and
  deletes the folder. Spends nothing (:mod:`evalution.demo.ui`, docs/ui-testing.md).
* ``demo versions [--no-open]``: a small git repository with ``main`` and ``feature-x``, three
  tests played on each through the real runner (each version checked out into evalution's own
  folder), and ``feature-x vs main`` printed and opened; Ctrl-C stops it and deletes the folder.
  Spends nothing (:mod:`evalution.demo.versions`, docs/versions.md).
* ``hooks install|uninstall|status --target T [--url U]``: git hooks in the target's repo that tell
  a running ``serve`` about a change (:mod:`evalution.project.hooks`). Nothing else ever installs
  them.
* ``versions [--target T] [--fetch REMOTE/BRANCH] [--remove COMMIT]``: the target's branches and
  tags, and the versions evalution checked out on its own, with their sizes
  (:mod:`evalution.project.versions`). ``--fetch`` fetches a remote branch into evalution's own
  refs, never the person's; ``--remove`` removes a version evalution keeps.

The config is ``--config``, else ``$EVALUTION_CONFIG``, else ``./evalution.yaml``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pkgutil
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI

from evalution import adapters, doctor, remote, service, upgrade
from evalution import config as config_file
from evalution.adapters import contract
from evalution.adapters.base import Session
from evalution.api.app import create_app
from evalution.api.deps import ALLOWED_HOSTS_ENV, answers_to
from evalution.api.traces import intake_router
from evalution.benchmarks import longmemeval as benchmarks
from evalution.benchmarks import planning
from evalution.code import CodeError
from evalution.config import Config, ConfigError
from evalution.connect.connect import check as check_parts
from evalution.core.version import described
from evalution.demo import ui as demo_ui
from evalution.demo import versions as demo_versions
from evalution.isolation import containers as store_boxes
from evalution.isolation import discover as store_scan
from evalution.isolation import isolate as isolation
from evalution.isolation import launch as app_copies
from evalution.isolation import observe as store_watch
from evalution.isolation.plan import PlanStore
from evalution.llm import LLM
from evalution.project import building
from evalution.project import hooks as git_hooks
from evalution.project import versions as code_versions
from evalution.results import baseline
from evalution.results.compare import comparison, default_base
from evalution.results.markdown import comparison_markdown
from evalution.results.record import environment, pass_marks, regrade_rules
from evalution.results.store import RunLock, RunStore
from evalution.results.views import detail
from evalution.runs.preflight import check_adapter, check_isolation, remember_setup, setup_key
from evalution.runs.runner import RunBusyError, Runs
from evalution.storage import Database
from evalution.suite.cases import CaseError
from evalution.suite.store import export_sets, import_sets
from evalution.targets import Targets
from evalution.traces.base import is_ai
from evalution.traces.profile import digest, recent_sessions, render
from evalution.writer import usage as writer_usage
from evalution.writer.sessions import SessionStore

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


_TRY_MARK = {"passed": "P", "failed": "F", "unjudged": "?"}


def _num(value: Any, spec: str = ".2f") -> str:
    return "-" if value is None else format(value, spec)


def table(run: dict[str, Any]) -> str:
    """One row per case, then the totals and the comparison with the base run."""
    lines = [
        f"{'case':<28} {'verdict':<8} {'tries':<6} {'steps':>5} {'reply':>5} "
        f"{'secs':>6} {'tokens':>8}"
    ]
    unplayed = {t["case"]: t["reason"] for t in run["tests"] if t.get("reason")}
    for row in run["cases"]:
        tries = "".join(_TRY_MARK[t] for t in row["tries"]) or "-"
        lines.append(
            f"{row['case'][:28]:<28} {row['verdict'] or '-':<8} {tries:<6} "
            f"{_num(row['tools']):>5} {_num(row['reply']):>5} {_num(row['seconds'], '.0f'):>6} "
            f"{_num(row['tokens'], '.0f'):>8}"
            + (f"  stale: {row['stale']}" if row.get("stale") else "")
            + (f"  ({', '.join(row['unjudged'])})" if row.get("unjudged") else "")
            + (f"  {row['note']}" if row.get("note") else "")
            + (f"  was {row['originally']}; rules changed" if row.get("originally") else "")
            + (f"  not run: {unplayed[row['case']]}" if row["case"] in unplayed else "")
        )
    t, scores = run["totals"], pass_marks(run)
    lines.append(
        f"passed {t['passed']}/{t['cases']} ({t['passed_checked']} checked, "
        f"{t['passed_model_only']} by a model only, {t['unjudged']} couldn't judge)  "
        f"steps {_num(t['tools'])}  reply {_num(t['reply'])}  "
        f"median {t['median_seconds']:.0f}s  {t['tokens']} tokens  "
        f"(pass: steps >= {scores['tools']}, reply >= {scores['reply']})"
    )
    if run.get("stop_reason"):
        lines.append(f"{run['status']}: {run['stop_reason']}")
    delta, base = run.get("delta"), run.get("base")
    if delta and base:
        lines.append(
            f"vs {base['run']}: {delta['better']} better, {delta['worse']} worse, "
            f"{delta['changed']} where the test or rules changed, "
            f"steps {delta['tools']:+.2f}, reply {delta['reply']:+.2f}, "
            f"time {delta['median_seconds_pct']:+.0f}%"
        )
    return "\n".join(lines)


def analysis(doc: dict[str, Any]) -> str:
    """Each failed try's analysis (summary and fix), then the run's summary and patterns."""
    lines = []
    for stem, entry in doc["cases"].items():
        for attempt in entry["tries"]:
            found = attempt.get("analysis")
            if not found:
                continue
            lines.append(
                f"{stem} try {attempt['n']} [{found['suspected']}?]: {found['summary']['text']}"
            )
            lines.append(f"  fix: {found['fix']}")
    found = doc.get("analysis")
    if found:
        lines.append(f"run: {found['summary']['text']}")
        for pattern in found["patterns"]:
            lines.append(
                f"  [{pattern['suspected']}?] {pattern['title']}: {pattern['detail']['text']} "
                f"({', '.join(pattern['cases'])})"
            )
    return "\n".join(lines)


async def run(config: Config, args: argparse.Namespace) -> int:
    """Play the run; with ``--server``, on that workspace's tests, and upload it there after,
    however it came out. An upload that fails is exit 2: CI must not pass on a run no one sees."""
    if not args.server:
        return (await _play(config, args))[0]
    docs = Database.open(config.data_dir).docs()
    with remote.connect(args.server, args.token or os.environ.get(remote.TOKEN_ENV)) as server:
        found = await asyncio.to_thread(remote.fetch_sets, server, docs, args.target, args.set)
        print(f"evalution: took {len(found)} set(s) from {args.server}")
        code, played = await _play(config, args)
        if played is not None:
            await asyncio.to_thread(remote.upload, server, RunStore(docs), played)
            print(f"evalution: uploaded run {played} to {args.server}")
    return code


async def _play(config: Config, args: argparse.Namespace) -> tuple[int, str | None]:
    """The exit code, and the run when one started."""
    targets = Targets(config)
    if args.sets is not None:
        folder = Path(args.sets) if args.sets else config.sets_dir
        found = import_sets(targets.docs, folder, [args.target])
        print(f"evalution: imported {len(found)} set(s) from {folder}")
    llm = LLM(config.model) if config.model else None
    runs = Runs(targets, llm)
    intake = await _take_traces(targets, args.traces_port)
    try:
        await runs.recover()
        estimate = runs.estimate(
            args.target, args.set, [args.case] if args.case else None, args.tries, args.ref
        )
        tries = f"{estimate['tries']}"
        if estimate["tries_high"] > estimate["tries"]:
            tries += f" to {estimate['tries_high']}"
        print(
            f"evalution: {estimate['cases']} case(s), {tries} tries, {estimate['chats']} messages"
        )
        _print_version(estimate.get("version") or await runs.preview(args.target))
        refused = estimate["refused"]
        for test in refused:
            print(f"evalution: left out {test['case']}: {test['why']}", file=sys.stderr)
        if not estimate["cases"]:
            print("evalution: nothing to run", file=sys.stderr)
            return 1, None
        if not args.yes and not await _confirmed():
            print("evalution: not started")
            return 0, None
        base = baseline.read(args.against) if args.against else None
        # A version's build, line by line, as it prints them.
        runs.on_build_line = lambda line: print(f"  {line}")
        started = await runs.start(estimate["token"], base, args.env)
        print(f"evalution: run {started['run']}")
        try:
            await runs.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            await runs.stop(started["run"])
        doc = runs.load(started["run"]) or {}
        if doc.get("version"):
            _print_version({**doc["version"], "said": described(doc["version"])})
        others = runs.all(args.target, args.set)
        found = detail(doc, others, None)
        print(table(found))
        if notes := analysis(doc):
            print(notes)
        if args.save:
            baseline.write(args.save, doc)
        if base is not None:
            said = comparison(doc, base)
            _print_comparison(said)
            if args.summary:
                args.summary.write_text(comparison_markdown(said, doc, args.page))
            return (1 if said["answer"] in ("worse", "mixed") else 0), started["run"]
        passed = not refused and all(c["verdict"] == "pass" for c in found["cases"])
        return (0 if passed else 1), started["run"]
    finally:
        if intake is not None:
            server, task = intake
            server.should_exit = True
            await task
        await targets.close()
        if llm is not None:
            await llm.close()


async def _take_traces(targets: Targets, port: int) -> tuple[uvicorn.Server, asyncio.Task] | None:
    """With no server up, nothing would hear the spans an app sends: listen for them while the run
    plays. If the port is taken, a server is likely there already."""
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return None
    app = FastAPI()
    app.include_router(intake_router(targets), prefix="/v1")
    settings = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(settings)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():  # noqa: ASYNC110 - uvicorn only has the flag
        await asyncio.sleep(0.05)
    return server, task


async def regrade(config: Config, args: argparse.Namespace) -> int:
    targets = Targets(config)
    if args.rules:
        runs = Runs(targets, None)
        if not runs.lock.acquire():
            print("evalution: a run is going", file=sys.stderr)
            return 2
        try:
            doc = runs.store.load(args.run)
            if doc is None:
                print(f"evalution: no run {args.run}", file=sys.stderr)
                return 2
            added = regrade_rules(doc)
            runs.store.write(doc)
        finally:
            runs.lock.release()
        print(f"evalution: {added} tries judged again under today's rules")
        return 0
    llm = LLM(config.model) if config.model else None
    runs = Runs(targets, llm)
    try:
        try:
            estimate = runs.estimate_regrade(args.run)
        except ValueError as exc:
            print(f"evalution: {exc}", file=sys.stderr)
            return 2
        print(f"evalution: {estimate['tries']} tries to grade again")
        if not args.yes and not await _confirmed():
            print("evalution: not started")
            return 0
        found = await runs.regrade(estimate["token"])
        print(f"evalution: {found['graded']} tries graded again")
        if found["failed"]:
            left = "it was" if found["failed"] == 1 else "they were"
            print(
                f"evalution: {found['failed']} left as {left}: the grader failed", file=sys.stderr
            )
            return 1
        return 0
    finally:
        await targets.close()
        if llm is not None:
            await llm.close()


def compare_runs(config: Config, args: argparse.Namespace) -> int:
    """Print *args.run* against its base; 1 when a test got worse, 2 when it can't be told."""
    store = RunStore(Targets(config).docs)
    found = store.all(args.target, args.set)
    doc = found[0] if args.run == "latest" and found else store.load(args.run)
    if doc is None:
        print(f"No run {args.run}", file=sys.stderr)
        return 2
    others = store.all(doc["target"], doc["set"])
    try:
        code = Targets(config).code(doc["target"])
    except LookupError:
        code = None
    main = code.base_branch() if code is not None else None
    chosen = args.base or default_base(doc, others, main)
    base = None
    if chosen:
        base = baseline.read(Path(chosen)) if Path(chosen).is_file() else store.load(chosen)
    if base is None:
        print("Nothing to compare it with", file=sys.stderr)
        return 2
    said = comparison(doc, base)
    if args.json:
        print(json.dumps(said, indent=2))
    else:
        _print_comparison(said)
    return 1 if said["answer"] in ("worse", "mixed") else 0


def accept(config: Config, args: argparse.Namespace) -> int:
    """Take one test's newer result into the baseline file, to be committed with the change."""
    if not args.baseline.is_file():
        raise LookupError(f"no baseline at {args.baseline}: make one with run --save")
    kept = baseline.read(args.baseline)
    store = RunStore(Targets(config).docs)
    if args.run is None:
        runs = store.all(kept["target"], kept["set"])
    else:
        found = baseline.read(Path(args.run)) if Path(args.run).is_file() else store.load(args.run)
        if found is None:
            raise LookupError(f"no run {args.run}")
        runs = [found]
    baseline.write(args.baseline, baseline.accept(kept, args.test, runs))
    print(f"evalution: {args.test} accepted into {args.baseline}; commit it with the change")
    return 0


def _print_version(version: dict[str, Any]) -> None:
    """The code a run plays or played, and why evalution can't vouch for it when it can't."""
    print(f"evalution: code: {version['said']}")
    if version.get("cost"):
        print(f"evalution: {version['cost']}")
    if version.get("verified") is False:
        print(f"evalution: warning: {version['why_unverified']}", file=sys.stderr)
    for note in version.get("notes") or []:
        print(f"evalution: {note}")


def _print_comparison(said: dict[str, Any]) -> None:
    print(f"{said['headline']} ({said['run']} against {said['base']})")
    for row in said["changed"]:
        print(f"  {row['what']}: {row['before']} -> {row['now']}")
    if said.get("code"):
        print(f"  {said['code']}")
    for test in said["tests"]:
        was, now = test["before"], test["now"]
        counts = f"{_tries(was)} -> {_tries(now)}"
        notes = (["could be chance"] if test["chance"] else []) + test["caveats"]
        tail = f"  ({'; '.join(notes)})" if notes else ""
        print(f"  {test['change']:>8}  {test['name']}: {counts}{tail}")


def _tries(side: dict[str, Any] | None) -> str:
    return f"{side['passed']}/{side['judged']}" if side else "not run"


async def run_doctor(config: Config) -> int:
    """What a first run needs; 0 when nothing is wrong."""
    found = await doctor.examine(config, Targets(config))
    for item in found:
        print(f"{'ok ' if item.ok else 'NO '} {item.what}")
        if item.fix:
            print(f"    {item.fix}")
    return 0 if all(item.ok for item in found) else 1


async def check_target(config: Config, name: str, isolation: bool = False) -> int:
    """One real turn through *name*'s adapter; 0 when it works."""
    targets = Targets(config)
    target = targets.adapter(name)
    traces = config.traces
    found = await check_adapter(
        target, targets.source(name), traces.settle_seconds, traces.stuck_seconds
    )
    print(f"{found.seconds:.0f} s · reply: {found.reply[:80]!r} · traces: {found.traces}")
    print(f"{found.spans} spans · {found.tools} tool calls")
    for problem in found.problems:
        print(f"Problem: {problem}", file=sys.stderr)
    for warning in found.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if found.ok:
        remember_setup(config.data_dir, name, setup_key(target, config.targets[name]))
        print("The adapter works.")
    if found.ok and isolation:
        try:
            leak = await check_isolation(target)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Problem: the isolation check didn't finish: {exc}", file=sys.stderr)
            return 1
        print(f"Problem: {leak}" if leak else "A new session didn't show what an earlier one said.")
        return 1 if leak else 0
    return 0 if found.ok else 1


def find_stores(config: Config, args: argparse.Namespace) -> int:
    """Print the stores FOLDER names, and with ``--target`` what one probe showed."""
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        raise LookupError(f"not a folder: {folder}")
    stores = store_scan.scan(folder)
    if args.target:
        url = str(config.targets[args.target].options.get("url") or "")
        port = args.port or urlsplit(url).port
        pid = store_watch.listening(port) if port else None
        if pid is None:
            print(
                f"evalution: nothing on this computer listens on port {port}; only the folder "
                "is watched (pass --port)",
                file=sys.stderr,
            )
        if not args.yes and input("Send one probe message to the app? [y/N] ").lower() != "y":
            return 1
        seen, boxes, inside = asyncio.run(
            _probe_stores(config, args.target, folder, stores, pid, port)
        )
        stores = store_watch.reconcile(folder, stores, seen)
        stores = store_boxes.reconcile(stores, boxes, inside)
        print(f"Watched: {', '.join(seen.roots)}")
        print(f"Written during the probe: {len(seen.written)} files, {len(seen.canary)} hold it")
        if boxes:
            print(f"Containers searched: {', '.join(b.name for b in boxes)}")
            print(f"Found inside them: {len(inside)} places hold it")
    if args.json:
        print(json.dumps([vars(s) for s in stores], indent=2))
        return 0
    for s in stores:
        mark = "seen" if s.seen else ("named" if not args.target else "NOT SEEN")
        print(f"[{mark:>8}] {s.kind:<8} {s.locator}")
        for line in s.evidence[:3]:
            print(f"             named at {line}")
        for line in s.seen[:3]:
            print(f"             {line}")
    return 0


async def _probe_stores(
    config: Config,
    name: str,
    folder: Path,
    stores: list[store_scan.Store],
    pid: int | None,
    port: int | None,
) -> tuple[store_watch.Observation, list[store_boxes.Container], list[store_boxes.Found]]:
    """One message carrying a canary through *name*'s adapter, with the app watched, on this
    computer and in the containers behind its port."""
    target = Targets(config).adapter(name)
    canary = f"eval-canary-{secrets.token_hex(6)}"
    boxes = await store_boxes.project_of(port) if port else []

    async def act() -> None:
        session = await target.open("isolation-probe")
        try:
            await target.send(
                session,
                f"Please remember this for later: my code word is {canary}.",
                deadline=time.time() + 120,
            )
        finally:
            await target.close(session)

    seen = await store_watch.watch(folder, stores, act, canary, pid=pid)
    return seen, boxes, await store_boxes.search(boxes, canary)


async def isolate(config: Config, args: argparse.Namespace) -> int:
    """Find, plan and rehearse T's stores on a copy; 0 when the plan is proven."""

    async def ask(summary: str) -> bool:
        print(summary)
        print(
            "Two messages go to the copy (not to your app): one to find where it keeps what it's "
            "told, one to rehearse resetting that."
        )
        try:
            answer = await asyncio.to_thread(input, "Send them? [y/N] ")
        except EOFError:
            answer = ""
        return answer.strip().lower() in ("y", "yes")

    try:
        plan = await isolation.isolate(
            config, args.target, slot=args.slot, yes=args.yes, ask=ask, keep=args.keep
        )
    except isolation.Declined:
        print("evalution: nothing sent; the copy is taken down", file=sys.stderr)
        return 1
    except app_copies.LaunchError as exc:
        print(f"evalution: {exc}", file=sys.stderr)
        return 2
    print(plan.report)
    print(f"Plan ({plan.status}): {isolation.plan_path(config, args.target)}")
    if args.keep:
        name = app_copies.project_name(args.target, args.slot)
        print(f"The copy is still running: evalution isolation down {name}")
    return 0 if plan.status == "proven" else 1


async def isolation_copies(config: Config, args: argparse.Namespace) -> int:
    """``isolation down [PROJECT]`` and ``isolation show --target T``."""
    if args.action == "show":
        if not args.target:
            raise LookupError("isolation show needs --target")
        plan = PlanStore(config.data_dir).load(args.target)
        if plan is None:
            print(f"evalution: {args.target} has no isolation plan yet", file=sys.stderr)
            return 1
        print(f"{args.target}: {plan.status}, {len(plan.stores)} store(s)")
        print(plan.report)
        print(f"Plan: {isolation.plan_path(config, args.target)}")
        return 0
    if not args.project:
        found = await app_copies.copies()
        print("\n".join(found) if found else "No copies of evalution's are on this computer.")
        return 0
    try:
        app_copies.guard(args.project)
        await app_copies.down(args.project)
        left = await isolation.clear_extras(args.project)
    except app_copies.LaunchError as exc:
        print(f"evalution: {exc}", file=sys.stderr)
        return 2
    print(f"Took down {args.project}" + (f", and {', '.join(left)}" if left else ""))
    return 0


def migrate(config: Config) -> int:
    changed, unreadable = RunStore(Targets(config).docs).upcast_all()
    print(f"evalution: {len(changed)} runs rewritten")
    if unreadable:
        print(
            f"evalution: these don't read and were left: {', '.join(unreadable)}", file=sys.stderr
        )
        return 1
    return 0


_STAGE_COLUMNS = ("seconds", *writer_usage.STAGE_COUNTS)


def learn_report(config: Config, args: argparse.Namespace) -> int:
    """Print the last learn of ``--report``'s target, stage by stage; as JSON with ``--json``.

    :raises LookupError: It has no learn measured by stage yet.
    """
    turns = SessionStore(Database.open(config.data_dir).docs()).usage(args.report)
    found = writer_usage.last_learn(turns)
    if found is None:
        raise LookupError(f"no learn of {args.report} measured yet: learn it, then ask again")
    if args.json:
        print(json.dumps(found, indent=2))
        return 0
    at = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(found.get("at") or 0)))
    print(
        f"evalution: the last learn of {args.report}, {at}: {found.get('seconds', 0):,.0f} s, "
        f"{found.get('calls', 0):,} model calls, {found.get('areas_run', 0):,} of "
        f"{found.get('areas_total', 0):,} areas learned, {found.get('slice_files', 0):,} files "
        "read in bulk"
    )
    print(f"  {'stage':<14}" + "".join(f"{c.replace('_', ' '):>12}" for c in _STAGE_COLUMNS))
    for stage in found["stages"]:
        cells = [f"{stage.get('seconds', 0):>12,.1f}"]
        cells += [f"{stage.get(c, 0):>12,}" for c in writer_usage.STAGE_COUNTS]
        print(f"  {stage['stage']:<14}" + "".join(cells))
    return 0


def keeping(found: upgrade.Outcome) -> int:
    """Print what ``backup``, ``upgrade`` or ``restore`` did; 1 when something didn't check out."""
    print("\n".join(found.lines))
    return 0 if found.ok else 1


_STEP_MARK = {"ok": "ok ", "failed": "NO ", "skipped": "-- "}


async def benchmark_plan(config: Config, args: argparse.Namespace) -> int:
    targets = Targets(config)
    llm = LLM(config.model) if config.model else None
    try:
        if llm is None:
            print("evalution: planning needs a model", file=sys.stderr)
            return 1
        plan = await planning.plan_for(
            targets,
            args.target,
            targets.adapter(args.target),
            llm.caller("strong"),
            config.data_dir,
        )
        print(json.dumps(plan, indent=1))
        return 0
    finally:
        await targets.close()
        if llm is not None:
            await llm.close()


def benchmark(config: Config, args: argparse.Namespace) -> int:
    if args.action == "plan":
        return asyncio.run(benchmark_plan(config, args))
    if args.action == "approve":
        found = planning.approve(Targets(config), args.target)
        print(f"evalution: {found['set']} has {found['questions']} questions")
        return 0
    if args.action == "install":
        docs = Database.open(config.data_dir).docs()
        slug, count = benchmarks.install(args.size, args.target, docs, config.data_dir)
        print(f"evalution: {slug} has {count} questions, for target {args.target}")
        print(f"evalution: run it with: evalution run --target {args.target} --set {slug}")
        return 0
    doc = RunStore(Targets(config).docs).load(args.run)
    if doc is None:
        print(f"evalution: no run {args.run}", file=sys.stderr)
        return 1
    found = benchmarks.report(doc)

    def share(value: float | None) -> str:
        return "-" if value is None else f"{value:.0%}"

    print(f"answer accuracy  {share(found['accuracy'])}  {found['right']} of {found['judged']}")
    print(f"task average     {share(found['task_average'])}")
    saved = found["memory"]
    print(
        f"write recall     {share(found['write_recall'])}  "
        f"{saved['committed']} of {saved['measured']} in committed memory, "
        f"{saved['written']} written"
    )
    for kind, tally in found["kinds"].items():
        print(f"  {kind:<26} {tally['right']} of {tally['judged']}")
    print(
        f"abstention       {share(found['abstention'])}  "
        f"{found['admitted']} of {found['unanswerable']}"
    )
    if found["unjudged"]:
        print(f"couldn't judge   {found['unjudged']}")
    return 0


def _print_step(step: contract.Step) -> None:
    print(f"{_STEP_MARK[step.status]} {step.name}" + (f": {step.detail}" if step.detail else ""))


async def adapter_test(config: Config, args: argparse.Namespace) -> int:
    """The contract kit over target ``args.target``: the free setup check, then, once asked, the
    messages. 0 when the adapter keeps its contract."""
    targets = Targets(config)
    lock = RunLock(config.data_dir)
    try:
        conf = targets.conf(args.target)
        if conf.adapter != args.name:
            print(f"evalution: {args.target} uses the {conf.adapter} adapter", file=sys.stderr)
            return 2
        if not lock.acquire():
            print("evalution: a run is going on this data folder", file=sys.stderr)
            return 2
        try:
            target = targets.adapter(args.target)
        except Exception as exc:  # someone's own module can fail in any way at all
            # The module's own error, not the ConfigError that already wraps it.
            cause = exc.__cause__ if isinstance(exc, ConfigError) and exc.__cause__ else exc
            print(
                f"evalution: the {args.name} adapter doesn't load: {type(cause).__name__}: {cause}",
                file=sys.stderr,
            )
            return 2
        said = (await check_parts(conf, {}, targets.store))["adapter"]
        if not said["ok"]:
            print(f"Setup problem: {said.get('detail')}", file=sys.stderr)
            if said.get("fix"):
                print(said["fix"], file=sys.stderr)
            return 1
        source = targets.source(args.target)
        # An app that never sent evalution a span can't show its trace value finds them.
        traced = conf.phoenix is not None or await source.project_exists()

        async def spans(session: Session, start: float, end: float) -> int:
            read = await source.session(session.trace_attribute, session.trace_value, start, end)
            return sum(1 for span in read.spans if is_ai(span))

        planned = contract.messages_needed(target.capabilities)
        print(f"evalution: this sends {planned} messages to {args.target}, which you pay for")
        if not args.yes and not await _confirmed():
            print("evalution: not started")
            return 0
        report = await contract.run(
            target,
            adapters.adapter_info(args.name),
            spans=spans if traced else None,
            on_step=_print_step,
            settle=config.traces.settle_seconds * 5,
        )
        failed = sum(1 for step in report.steps if step.status == "failed")
        words = f"{failed} problems" if failed else "The adapter keeps its contract"
        print(f"evalution: {report.messages} messages sent. {words}")
        return 1 if failed else 0
    finally:
        lock.release()
        await targets.close()


def adapter_new(config: Config, name: str) -> int:
    folder = config.adapters_dir or config.data_dir / "adapters"
    adapters.add_folder(folder)
    modules = tuple(found.name for found in pkgutil.iter_modules(adapters.__path__))
    try:
        path = contract.new_adapter(name, folder, modules)
    except FileExistsError:
        print(f"evalution: {folder / f'{name}.py'} is already there: left alone", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"evalution: {exc}", file=sys.stderr)
        return 2
    print(f"evalution: wrote {path}. Change what it marks CHANGE, then give a target")
    print(f"`adapter: {name}` and try: evalution adapter test {name} --target <target>")
    return 0


async def _confirmed() -> bool:
    try:
        answer = await asyncio.to_thread(input, "Start? [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


async def profile(config: Config, args: argparse.Namespace) -> int:
    targets = Targets(config)
    try:
        source = targets.source(args.target)
        groups = await recent_sessions(source, targets.trace_attribute(args.target), args.sessions)
        print(render(digest(groups)), end="")
        return 0
    finally:
        await targets.close()


def hooks(config: Config, args: argparse.Namespace) -> int:
    code = Targets(config).code(args.target)
    try:
        if args.action == "install":
            found = git_hooks.install(code, args.target, args.url)
        elif args.action == "uninstall":
            found = git_hooks.uninstall(code, args.target)
        else:
            found = git_hooks.status(code, args.target)
    except (CodeError, ValueError) as exc:
        print(f"evalution: not available: {exc}", file=sys.stderr)
        return 2
    if not found["available"]:
        print(f"not available: {found['reason']}")
        return 2 if args.action != "status" else 0
    for hook, state in found["hooks"].items():
        print(f"{hook}: {state}")
    return 0


def _when(seconds: float | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(seconds)) if seconds else ""


def versions(config: Config, args: argparse.Namespace) -> int:
    """``evalution versions``: list, fetch a remote branch, or remove a kept version."""
    targets = Targets(config)
    name = args.target
    if name is None:
        names = targets.names()
        if len(names) != 1:
            print("evalution: --target: which app (there is more than one)", file=sys.stderr)
            return 2
        name = names[0]
    try:
        found = building.versions_of(targets, name)
        if args.fetch:
            remote, _, branch = args.fetch.partition("/")
            commit = found.fetch(remote, branch)
            print(f"fetched {args.fetch} @ {commit[:7]} into evalution's own refs")
            return 0
        if args.remove:
            gone = found.remove(args.remove)
            print(f"removed {gone.commit[:7]} ({code_versions.said(gone.size)}): {gone.path}")
            return 0
        view = found.view()
    except code_versions.VersionError as exc:
        print(f"evalution: {exc}", file=sys.stderr)
        return 2
    here = view["checked_out"]
    at = (here["commit"] or "")[:7]
    print(f"{view['repo']}: checked out {here['branch'] or 'no branch'} @ {at}")
    for ref in view["refs"]:
        marks = ("*" if ref["checked_out"] else " ") + ("v" if ref["worktree"] else " ")
        print(
            f"{marks} {ref['kind']:<7} {ref['name']:<40} {ref['commit'][:7]} "
            f"{_when(ref['date'])}  {ref['subject'][:60]}"
        )
    print(
        f"versions kept: {code_versions.said(view['size'])} of "
        f"{code_versions.said(view['limit'])}; {code_versions.said(view['free'])} free on this disk"
    )
    for kept in view["kept"]:
        print(f"  {kept['commit'][:7]}  {code_versions.said(kept['size']):>8}  {kept['path']}")
    return 0


def carry_sets(config: Config, args: argparse.Namespace) -> int:
    docs = Database.open(config.data_dir).docs()
    folder = args.dir or config.sets_dir
    chosen = [args.target] if args.target else None
    if args.action == "export":
        done = export_sets(docs, folder, chosen)
        print(f"evalution: exported {len(done)} set(s) to {folder}")
    else:
        done = import_sets(docs, folder, chosen)
        print(f"evalution: imported {len(done)} set(s) from {folder}")
    for name in done:
        print(f"  {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evalution", description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, help="the config file (default evalution.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="the API and the page")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    play = sub.add_parser("run", help="run a set, or one case, and print the result")
    play.add_argument("--target", required=True)
    play.add_argument("--set", required=True)
    play.add_argument("--case", help="only this case (its file stem)")
    play.add_argument(
        "--tries",
        type=int,
        help="exactly this many tries per case; without it, each plays until its tries agree",
    )
    play.add_argument("--yes", action="store_true", help="start without asking")
    play.add_argument(
        "--against",
        type=Path,
        help="a saved run: fail only when a test got worse than it, not on any test that fails",
    )
    play.add_argument(
        "--save", type=Path, help="keep this run as a file, to compare later ones with"
    )
    play.add_argument(
        "--summary", type=Path, help="with --against, also write the comparison here as markdown"
    )
    play.add_argument("--page", help="with --summary, where the page is, to link each test")
    tests_from = play.add_mutually_exclusive_group()
    tests_from.add_argument(
        "--sets",
        nargs="?",
        const="",
        metavar="DIR",
        help="import the target's tests from this folder first (default: the config's sets_dir)",
    )
    tests_from.add_argument(
        "--server",
        metavar="URL",
        help="take the tests from this evalution's workspace, and upload the run to it after",
    )
    play.add_argument(
        "--token", help=f"with --server, a workspace API token (default ${remote.TOKEN_ENV})"
    )
    play.add_argument("--env", metavar="NAME", help="where the app runs, e.g. staging")
    play.add_argument(
        "--ref",
        default="disk",
        help="the version to play: a branch, origin/branch, a tag or a commit, checked out and "
        "built by evalution without touching your checkout (default: disk, what's on disk now)",
    )
    play.add_argument(
        "--traces-port",
        type=int,
        default=8080,
        help="where the app sends traces while this runs, with no server up (default 8080)",
    )
    take = sub.add_parser("accept", help="take a test's newer result into the baseline file")
    take.add_argument("test", help="the test (its file stem)")
    take.add_argument("--baseline", type=Path, required=True, help="the file run --against reads")
    take.add_argument("--run", help="the run, or a run file, to take it from; default: the newest")
    carry = sub.add_parser("sets", help="the tests as files: export them, or import a folder")
    carry.add_argument("action", choices=("export", "import"))
    carry.add_argument(
        "dir", nargs="?", type=Path, help="the folder (default: the config's sets_dir)"
    )
    carry.add_argument("--target", help="only this target's sets")
    check = sub.add_parser("check", help="one real turn, to show the adapter works: spends one")
    check.add_argument("--target", required=True)
    check.add_argument(
        "--isolation",
        action="store_true",
        help="also see whether a new session knows what an earlier one said: spends two more",
    )
    versus = sub.add_parser(
        "compare", help="did this change make it worse? 1 when a test got worse; spends nothing"
    )
    versus.add_argument("run", help="a run, or 'latest' with --target and --set")
    versus.add_argument("--base", help="the run to compare with; default: the last other setup")
    versus.add_argument("--target")
    versus.add_argument("--set")
    versus.add_argument("--json", action="store_true", help="the whole comparison, as JSON")
    bench = sub.add_parser("benchmark", help="ready-made benchmarks: install one, read a run's")
    bench_what = bench.add_subparsers(dest="action", required=True)
    put = bench_what.add_parser("install", help="LongMemEval as a set: downloads its data once")
    put.add_argument("--target", required=True)
    put.add_argument("--size", choices=sorted(benchmarks.SIZES), default="quick")
    worked = bench_what.add_parser("plan", help="work out how it plays on this app: sends a few")
    worked.add_argument("--target", required=True)
    agreed = bench_what.add_parser("approve", help="write the planned set, ready to run")
    agreed.add_argument("--target", required=True)
    read = bench_what.add_parser("report", help="a run's benchmark numbers, and how they add up")
    read.add_argument("run")
    sub.add_parser("doctor", help="everything a first run needs, and what to do about it: free")
    service_cmd = sub.add_parser("service", help="keep the page running, from login on")
    service_cmd.add_argument("action", choices=("install", "uninstall", "status"))
    service_cmd.add_argument("--port", type=int, default=8080)
    sub.add_parser("migrate", help="rewrite stored runs as today's schema")
    keep = sub.add_parser("backup", help="a private copy of data, tests and settings")
    keep.add_argument("--to", type=Path, help="the folder to put it in (default backups/)")
    move = sub.add_parser("upgrade", help="back up, rewrite stored runs, check they all still read")
    move.add_argument("--to", type=Path, help="the folder for the backup (default backups/)")
    back = sub.add_parser("restore", help="put a backup back, after backing up what is here now")
    back.add_argument("backup", type=Path)
    adapter = sub.add_parser("adapter", help="write your own adapter, and check one")
    what = adapter.add_subparsers(dest="action", required=True)
    tried = what.add_parser("test", help="its contract over a real target: spends a few messages")
    tried.add_argument("name")
    tried.add_argument("--target", required=True)
    tried.add_argument("--yes", action="store_true", help="start without asking")
    fresh = what.add_parser("new", help="a commented starter in the adapters' folder")
    fresh.add_argument("name")
    again = sub.add_parser("regrade", help="judge a stored run again")
    again.add_argument("run")
    how = again.add_mutually_exclusive_group(required=True)
    how.add_argument("--rules", action="store_true", help="today's rules over its checks: free")
    how.add_argument(
        "--judge", action="store_true", help="grade again from its evidence: calls the model"
    )
    again.add_argument("--yes", action="store_true", help="start without asking")
    taught = sub.add_parser("learn", help="a learn's measures, stage by stage: free")
    taught.add_argument(
        "--report",
        required=True,
        metavar="TARGET",
        help="print the last learn of TARGET from its sessions: each stage's seconds, calls, "
        "tokens and characters",
    )
    taught.add_argument("--json", action="store_true", help="the whole measure, as JSON")
    prof = sub.add_parser("profile", help="a digest of the target's recent traces")
    prof.add_argument("--target", required=True)
    prof.add_argument("--sessions", type=int, default=50)
    found = sub.add_parser("stores", help="where an app keeps state: a read, or one probe")
    found.add_argument("folder")
    found.add_argument("--target", help="send one probe through this target and watch it")
    found.add_argument("--port", type=int, help="the port the app's process listens on")
    found.add_argument("--yes", action="store_true")
    found.add_argument("--json", action="store_true")
    iso = sub.add_parser(
        "isolate", help="find, plan and rehearse a target's resets on a copy: sends two messages"
    )
    iso.add_argument("--target", required=True)
    iso.add_argument("--slot", type=int, default=0, help="which copy: eval-<target>-<slot>")
    iso.add_argument("--keep", action="store_true", help="leave the copy running after")
    iso.add_argument("--yes", action="store_true", help="send without asking")
    copy_cmd = sub.add_parser("isolation", help="evalution's copies of apps, and saved plans")
    copy_cmd.add_argument("action", choices=("down", "show"))
    copy_cmd.add_argument("project", nargs="?", help="down: the copy (eval-*); none lists them")
    copy_cmd.add_argument("--target", help="show: whose plan")
    show = sub.add_parser("demo", help="see a part of evalution working on a made-up app: free")
    show.add_argument(
        "what",
        choices=("ui", "versions"),
        help="ui: two tests played through a chat page; versions: a branch tested against main",
    )
    show.add_argument("--no-open", action="store_true", help="print the result's address only")
    hook = sub.add_parser("hooks", help="git hooks in the target's repo, opt-in")
    hook.add_argument("action", choices=("install", "uninstall", "status"))
    hook.add_argument("--target", required=True)
    hook.add_argument("--url", default="http://127.0.0.1:8080", help="where serve listens")
    kept = sub.add_parser("versions", help="branches, tags and the versions evalution checked out")
    kept.add_argument("--target", help="which app (default: the only one)")
    acted = kept.add_mutually_exclusive_group()
    acted.add_argument(
        "--fetch", metavar="REMOTE/BRANCH", help="fetch a remote branch into evalution's own refs"
    )
    acted.add_argument("--remove", metavar="COMMIT", help="remove the version of this commit")
    args = parser.parse_args(argv)
    if args.command == "run" and args.summary and not args.against:
        parser.error("--summary needs --against")
    if args.command == "run" and args.token and not args.server:
        parser.error("--token needs --server")
    if args.command == "run":
        try:
            environment("local", args.env)
        except ValueError as exc:
            parser.error(f"--env: {exc}")
    try:
        config = config_file.load(args.config)
        if args.command == "serve":
            open_to_others = args.host not in LOCAL_HOSTS
            if open_to_others:
                names = ", ".join(sorted(answers_to(args.host, config.allowed_hosts)))
                print(
                    f"evalution: warning: listening on {args.host} with no login. Anyone who can "
                    "reach this address can read your targets and start runs. It answers only "
                    f"under these names: {names or 'none yet'}. To reach it under another, set "
                    f"{ALLOWED_HOSTS_ENV} (comma-separated) or allowed_hosts in the config.",
                    file=sys.stderr,
                )
            # An app on this computer reaches an all-addresses server on loopback.
            reached = "127.0.0.1" if args.host == "0.0.0.0" else args.host  # noqa: S104
            app = create_app(
                config,
                remote=open_to_others,
                url=f"http://{reached}:{args.port}",
                bind=args.host,
            )
            for name, why in adapters.broken().items():
                print(
                    f"evalution: adapter {name} doesn't load ({why}); its apps can't run. "
                    "See: evalution doctor",
                    file=sys.stderr,
                )
            uvicorn.run(app, host=args.host, port=args.port)
            return 0
        if args.command == "run":
            return asyncio.run(run(config, args))
        if args.command == "sets":
            return carry_sets(config, args)
        if args.command == "benchmark":
            return benchmark(config, args)
        if args.command == "versions":
            return versions(config, args)
        if args.command == "hooks":
            return hooks(config, args)
        if args.command == "demo":
            shown = demo_versions if args.what == "versions" else demo_ui
            return shown.run(config.web_dir, open_browser=not args.no_open)
        if args.command == "stores":
            return find_stores(config, args)
        if args.command == "isolate":
            return asyncio.run(isolate(config, args))
        if args.command == "isolation":
            return asyncio.run(isolation_copies(config, args))
        if args.command == "check":
            return asyncio.run(check_target(config, args.target, args.isolation))
        if args.command == "compare":
            return compare_runs(config, args)
        if args.command == "accept":
            return accept(config, args)
        if args.command == "learn":
            return learn_report(config, args)
        if args.command == "doctor":
            return asyncio.run(run_doctor(config))
        if args.command == "service":
            print(service.act(args.action, config.file, args.port))
            return 0
        if args.command == "migrate":
            return migrate(config)
        if args.command == "regrade":
            return asyncio.run(regrade(config, args))
        if args.command == "backup":
            return keeping(upgrade.backup(config, args.to))
        if args.command == "upgrade":
            return keeping(upgrade.upgrade(config, args.to))
        if args.command == "restore":
            return keeping(upgrade.restore(config, args.backup))
        if args.command == "adapter" and args.action == "new":
            return adapter_new(config, args.name)
        if args.command == "adapter":
            return asyncio.run(adapter_test(config, args))
        return asyncio.run(profile(config, args))
    except (ConfigError, CaseError, LookupError, upgrade.UpgradeError, remote.RemoteError) as exc:
        print(f"evalution: {exc}", file=sys.stderr)
        return 2
    except RunBusyError:
        print("evalution: a run is already going on this data folder", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
