"""``evalution serve | run | accept | sets | profile | hooks | migrate | regrade | backup |
upgrade | restore | adapter`` (docs/architecture.md, "Layout").

* ``serve``: the API and the page, on 127.0.0.1:8080, and evalution's own Phoenix when
  ``traces.managed`` (:mod:`evalution.traces.managed`). There is no login: it answers only this
  computer, unless ``--host`` names another address on purpose (it then warns: anyone who can
  reach that address can use it).
* ``run --target T --set S [--case C] [--tries N] [--yes] [--sets [DIR]]``: a run in this
  process. ``--sets`` first imports T's tests from DIR (default the config's ``sets_dir``), so a
  fresh database in CI plays the repo's tests. Prints the estimate and asks before anything
  spends, unless ``--yes``; then one row per case, the totals and each failed try's analysis.
  ``--tries`` plays exactly N of each case; without it each plays until its tries say something
  (:mod:`evalution.runs.sampling`), against ``--against`` when given. Ctrl-C stops the run.
  Exits 1 when a case did not pass, which a test no one could judge didn't.
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
  new spend, so it prints the estimate and asks first, and a run without evidence is refused.
* ``profile --target T``: the digest of the target's recent traces (no model call).
* ``hooks install|uninstall|status --target T [--url U]``: git hooks in the target's repo that tell
  a running ``serve`` about a change (:mod:`evalution.project.hooks`). Nothing else ever installs
  them.

The config is ``--config``, else ``$EVALUTION_CONFIG``, else ``./evalution.yaml``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pkgutil
import socket
import sys
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from evalution import adapters, doctor, service, upgrade
from evalution import config as config_file
from evalution.adapters import contract
from evalution.adapters.base import Session
from evalution.api.app import create_app
from evalution.api.deps import ALLOWED_HOSTS_ENV, answers_to
from evalution.api.traces import traces_router
from evalution.benchmarks import longmemeval as benchmarks
from evalution.benchmarks import planning
from evalution.code import CodeError
from evalution.config import Config, ConfigError
from evalution.connect.connect import check as check_parts
from evalution.llm import LLM
from evalution.project import hooks as git_hooks
from evalution.results import baseline
from evalution.results.compare import comparison, default_base
from evalution.results.markdown import comparison_markdown
from evalution.results.record import pass_marks, regrade_rules
from evalution.results.store import RunLock, RunStore
from evalution.results.views import detail
from evalution.runs.preflight import check_adapter, check_isolation, remember_setup, setup_key
from evalution.runs.runner import RunBusyError, Runs
from evalution.storage import Database
from evalution.suite.cases import CaseError
from evalution.suite.store import export_sets, import_sets
from evalution.targets import Targets
from evalution.traces.base import is_ai
from evalution.traces.managed import INSTALL, Supervisor, installed, running_url
from evalution.traces.profile import digest, recent_sessions, render

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


_TRY_MARK = {"passed": "P", "failed": "F", "unjudged": "?"}


def _num(value: Any, spec: str = ".2f") -> str:
    return "-" if value is None else format(value, spec)


def table(run: dict[str, Any]) -> str:
    """One row per case, then the totals and the comparison with the base run."""
    lines = [
        f"{'case':<28} {'verdict':<8} {'tries':<6} {'steps':>5} {'reply':>5} "
        f"{'secs':>6} {'tokens':>8} {'usd':>6}"
    ]
    unplayed = {t["case"]: t["reason"] for t in run["tests"] if t.get("reason")}
    for row in run["cases"]:
        tries = "".join(_TRY_MARK[t] for t in row["tries"]) or "-"
        lines.append(
            f"{row['case'][:28]:<28} {row['verdict'] or '-':<8} {tries:<6} "
            f"{_num(row['tools']):>5} {_num(row['reply']):>5} {_num(row['seconds'], '.0f'):>6} "
            f"{_num(row['tokens'], '.0f'):>8} {_num(row['usd']):>6}"
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
        f"median {t['median_seconds']:.0f}s  ${t['usd']:.2f}  {t['tokens']} tokens  "
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
            f"time {delta['median_seconds_pct']:+.0f}%, cost {delta['usd_pct']:+.0f}%"
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


def _targets(config: Config) -> Targets:
    """The targets, reading traces from the Phoenix a running ``serve`` keeps, if any (the
    built-in source needs no ``serve`` to be read)."""
    managed = running_url(config.data_dir) if config.traces.phoenix else None
    return Targets(config, managed)


async def run(config: Config, args: argparse.Namespace) -> int:
    targets = _targets(config)
    if args.sets is not None:
        folder = Path(args.sets) if args.sets else config.sets_dir
        found = import_sets(targets.docs, folder, [args.target])
        print(f"evalution: imported {len(found)} set(s) from {folder}")
    llm = LLM(config.model, docs=targets.docs) if config.model else None
    runs = Runs(targets, llm)
    intake = await _take_traces(targets, args.traces_port) if config.traces.builtin else None
    try:
        await runs.recover()
        estimate = runs.estimate(
            args.target, args.set, [args.case] if args.case else None, args.tries
        )
        tries, usd = f"{estimate['tries']}", f"${estimate['usd']:.2f}"
        if estimate["tries_high"] > estimate["tries"]:
            tries += f" to {estimate['tries_high']}"
            usd += f" to ${estimate['usd_high']:.2f}"
        print(
            f"evalution: {estimate['cases']} case(s), {tries} tries, "
            f"{estimate['chats']} messages, about {usd}"
        )
        refused = estimate["refused"]
        for test in refused:
            print(f"evalution: left out {test['case']}: {test['why']}", file=sys.stderr)
        if not estimate["cases"]:
            print("evalution: nothing to run", file=sys.stderr)
            return 1
        if not args.yes and not await _confirmed():
            print("evalution: not started")
            return 0
        base = baseline.read(args.against) if args.against else None
        started = await runs.start(estimate["token"], base)
        print(f"evalution: run {started['run']}")
        try:
            await runs.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            await runs.stop(started["run"])
        doc = runs.load(started["run"]) or {}
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
            return 1 if said["answer"] in ("worse", "mixed") else 0
        return 0 if not refused and all(c["verdict"] == "pass" for c in found["cases"]) else 1
    finally:
        if intake is not None:
            server, task = intake
            server.should_exit = True
            await task
        await targets.close()
        if llm is not None:
            await llm.close()


async def _take_traces(targets: Targets, port: int) -> tuple[uvicorn.Server, asyncio.Task] | None:
    """With no server up, nothing would hear the spans an app sends to built-in traces: listen for
    them while the run plays. If the port is taken, a server is likely there already."""
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return None
    app = FastAPI()
    app.include_router(traces_router(targets), prefix="/v1")
    settings = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(settings)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():  # noqa: ASYNC110 - uvicorn only has the flag
        await asyncio.sleep(0.05)
    return server, task


async def regrade(config: Config, args: argparse.Namespace) -> int:
    targets = _targets(config)
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
    llm = LLM(config.model, docs=targets.docs) if config.model else None
    runs = Runs(targets, llm)
    try:
        try:
            estimate = runs.estimate_regrade(args.run)
        except ValueError as exc:
            print(f"evalution: {exc}", file=sys.stderr)
            return 2
        print(f"evalution: {estimate['tries']} tries to grade again, about ${estimate['usd']:.2f}")
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
    store = RunStore(_targets(config).docs)
    found = store.all(args.target, args.set)
    doc = found[0] if args.run == "latest" and found else store.load(args.run)
    if doc is None:
        print(f"No run {args.run}", file=sys.stderr)
        return 2
    others = store.all(doc["target"], doc["set"])
    chosen = args.base or default_base(doc, others)
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
    store = RunStore(_targets(config).docs)
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


def _print_comparison(said: dict[str, Any]) -> None:
    print(f"{said['headline']} ({said['run']} against {said['base']})")
    for row in said["changed"]:
        print(f"  {row['what']}: {row['before']} -> {row['now']}")
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
    found = await doctor.examine(config, _targets(config))
    for item in found:
        print(f"{'ok ' if item.ok else 'NO '} {item.what}")
        if item.fix:
            print(f"    {item.fix}")
    return 0 if all(item.ok for item in found) else 1


async def check_target(config: Config, name: str, isolation: bool = False) -> int:
    """One real turn through *name*'s adapter; 0 when it works."""
    targets = _targets(config)
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


def migrate(config: Config) -> int:
    changed, unreadable = RunStore(_targets(config).docs).upcast_all()
    print(f"evalution: {len(changed)} runs rewritten")
    if unreadable:
        print(
            f"evalution: these don't read and were left: {', '.join(unreadable)}", file=sys.stderr
        )
        return 1
    return 0


def keeping(found: upgrade.Outcome) -> int:
    """Print what ``backup``, ``upgrade`` or ``restore`` did; 1 when something didn't check out."""
    print("\n".join(found.lines))
    return 0 if found.ok else 1


_STEP_MARK = {"ok": "ok ", "failed": "NO ", "skipped": "-- "}


async def benchmark_plan(config: Config, args: argparse.Namespace) -> int:
    targets = _targets(config)
    llm = LLM(config.model, docs=targets.docs) if config.model else None
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
        found = planning.approve(_targets(config), args.target)
        print(f"evalution: {found['set']} has {found['questions']} questions")
        return 0
    if args.action == "install":
        docs = Database.open(config.data_dir).docs()
        slug, count = benchmarks.install(args.size, args.target, docs, config.data_dir)
        print(f"evalution: {slug} has {count} questions, for target {args.target}")
        print(f"evalution: run it with: evalution run --target {args.target} --set {slug}")
        return 0
    doc = RunStore(_targets(config).docs).load(args.run)
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
    targets = _targets(config)
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
            kind = type(exc).__name__
            print(
                f"evalution: the {args.name} adapter doesn't load: {kind}: {exc}", file=sys.stderr
            )
            return 2
        said = (await check_parts(conf, {}, targets.managed_url, targets.store))["adapter"]
        if not said["ok"]:
            print(f"Setup problem: {said.get('detail')}", file=sys.stderr)
            if said.get("fix"):
                print(said["fix"], file=sys.stderr)
            return 1
        source = targets.source(args.target)

        async def spans(session: Session, start: float, end: float) -> int:
            assert source is not None
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
            spans=spans if source is not None else None,
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
    targets = _targets(config)
    try:
        phoenix = targets.source(args.target)
        if phoenix is None:
            print(f"evalution: {args.target} has no Phoenix project", file=sys.stderr)
            return 2
        groups = await recent_sessions(phoenix, targets.trace_attribute(args.target), args.sessions)
        print(render(digest(groups, targets.price)), end="")
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


def _supervisor(config: Config) -> Supervisor | None:
    """evalution's own Phoenix, when the config wants it and it is installed."""
    if not config.traces.phoenix:
        return None
    if not installed():
        print(
            f"evalution: to keep traces here, install Phoenix: {INSTALL}. "
            "Until then each target's own Phoenix is used.",
            file=sys.stderr,
        )
        return None
    traces = config.traces
    return Supervisor(config.data_dir / "phoenix", traces.port, traces.grpc)


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
    play.add_argument(
        "--sets",
        nargs="?",
        const="",
        metavar="DIR",
        help="import the target's tests from this folder first (default: the config's sets_dir)",
    )
    play.add_argument(
        "--traces-port",
        type=int,
        default=8080,
        help="with built-in traces, where the app sends them while this runs (default 8080)",
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
    how.add_argument("--judge", action="store_true", help="grade again from its evidence: spends")
    again.add_argument("--yes", action="store_true", help="start without asking")
    prof = sub.add_parser("profile", help="a digest of the target's recent traces")
    prof.add_argument("--target", required=True)
    prof.add_argument("--sessions", type=int, default=50)
    hook = sub.add_parser("hooks", help="git hooks in the target's repo, opt-in")
    hook.add_argument("action", choices=("install", "uninstall", "status"))
    hook.add_argument("--target", required=True)
    hook.add_argument("--url", default="http://127.0.0.1:8080", help="where serve listens")
    args = parser.parse_args(argv)
    if args.command == "run" and args.summary and not args.against:
        parser.error("--summary needs --against")
    try:
        config = config_file.load(args.config)
        if args.command == "serve":
            remote = args.host not in LOCAL_HOSTS
            if remote:
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
                remote=remote,
                phoenix=_supervisor(config),
                url=f"http://{reached}:{args.port}",
                bind=args.host,
            )
            uvicorn.run(app, host=args.host, port=args.port)
            return 0
        if args.command == "run":
            return asyncio.run(run(config, args))
        if args.command == "sets":
            return carry_sets(config, args)
        if args.command == "benchmark":
            return benchmark(config, args)
        if args.command == "hooks":
            return hooks(config, args)
        if args.command == "check":
            return asyncio.run(check_target(config, args.target, args.isolation))
        if args.command == "compare":
            return compare_runs(config, args)
        if args.command == "accept":
            return accept(config, args)
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
    except (ConfigError, CaseError, LookupError, upgrade.UpgradeError) as exc:
        print(f"evalution: {exc}", file=sys.stderr)
        return 2
    except RunBusyError:
        print("evalution: a run is already going on this data folder", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
