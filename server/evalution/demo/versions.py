"""``evalution demo versions``: a branch tested against ``main`` before its pull request, seen
working in a minute (ADR 0011, docs/versions.md).

In a scratch folder of its own (a new temporary one, deleted when it stops: never the person's own
targets, runs or repositories), it makes a small git repository, ``app/``, holding a small
assistant: one file, ``assistant.py``, that runs the made-up app (:mod:`evalution.demo.app`) with
one setting of its own. Two branches:

* ``main``: it remembers what it's asked to.
* ``feature-x``: one line changed, and it no longer does (``remembers = False``).

The repository is checked out on ``main``. It sets up a target ``demo-assistant`` whose code is that
repository and whose copy is a process started in it (``python assistant.py PORT``, the Python
evalution runs on), from a reset plan saved as proven, with a ``process`` build recipe saved as
proven (nothing to set up: no lockfile), as the UI demo saves its plan. Three tests, every one
decided by code:

* **Answers a sum** and **Says it noted a message**: the same on both branches.
* **Remembers the dog's name**: passes on ``main``, fails on ``feature-x``.

It plays them on ``main``, then on ``feature-x``, through the real runner in an ``evalution
serve`` of the scratch folder on a free port. Each version is checked out by evalution as a
worktree in the demo's data folder and its copy started there; the repository's own checkout is
never touched. The second run is compared, by default, with the first: the latest run of the same
tests on the base branch. It prints what that comparison says ("feature-x vs main: 1 test got
worse: Remembers the dog's name") and opens it in the browser (not with ``--no-open``). Ctrl-C
stops the server and deletes the folder, the worktrees with it.

It spends nothing: no model is set up, the made-up app answers by itself, and every check is code.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import yaml

from evalution.core.version import described
from evalution.demo import ui
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.isolation.recipe import RecipeStore, propose

TARGET = "demo-assistant"
SET = "versions-demo"
BASE, BRANCH = "main", "feature-x"
#: The test that moves between the two branches, and the two that don't.
FORGETS = "remembers-the-dog"
SUM, NOTED = "answers-a-sum", "says-it-noted"
#: Tries per test: four each way is the fewest that can tell 4/4 to 0/4 from an agent varying.
TRIES = 4
ASSISTANT = "assistant.py"
WEB_BUILD = ui.WEB_BUILD

#: The small assistant, as each branch has it: the made-up app with one setting of its own.
_ASSISTANT = '''"""A small assistant: evalution's made-up app, run from this repository.

Run it with: TRACES=<where its spans go> python assistant.py PORT
"""

import sys

from evalution.demo import app as made_up

{line}

if __name__ == "__main__":
    made_up.serve(int(sys.argv[1]))
'''
REMEMBERS = "#: Whether it keeps what it's asked to remember.\nmade_up.remembers = True"
FORGETS_LINE = (
    "#: Whether it keeps what it's asked to remember. Memory moves to a new store, not wired up "
    "yet.\nmade_up.remembers = False"
)

CASES: dict[str, dict[str, Any]] = {
    SUM: {
        "name": "Answers a sum",
        "about": "Asks a sum; the reply has the answer.",
        "turns": [{"message": "What is 6 times 7?"}],
        "reply": {"contains": ["42"]},
        "limits": {"tries": TRIES, "seconds": 30},
    },
    NOTED: {
        "name": "Says it noted a message",
        "about": "Tells it something in passing; it says it noted it.",
        "turns": [{"message": "I'm planning a trip in May."}],
        "reply": {"contains": ["Noted"]},
        "limits": {"tries": TRIES, "seconds": 30},
    },
    FORGETS: {
        "name": "Remembers the dog's name",
        "about": "Asks it to remember the dog's name, then what it was told.",
        "turns": [
            {"message": "Please remember that my dog is called Biscuit."},
            {"message": "What did I tell you?"},
        ],
        "reply": {"contains": ["Biscuit"]},
        "limits": {"tries": TRIES, "seconds": 30},
    },
}

#: git with nothing of the person's: no hooks, no config of theirs, a made-up author.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "evalution demo",
    "GIT_AUTHOR_EMAIL": "demo@made-up.test",
    "GIT_COMMITTER_NAME": "evalution demo",
    "GIT_COMMITTER_EMAIL": "demo@made-up.test",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(repo: Path, *args: str) -> str:
    """git in *repo*, with hooks and the person's config off; what it printed.

    :raises subprocess.CalledProcessError: It failed.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(  # noqa: S603
        ["git", "-c", f"core.hooksPath={os.devnull}", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        env={**env, **GIT_ENV},
        check=True,
    )
    return done.stdout


def make_repo(repo: Path) -> dict[str, str]:
    """The small assistant's repository in *repo*: ``main``, and ``feature-x`` one commit on
    with its one line changed, checked out on ``main``. Each branch's commit."""
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", BASE)
    (repo / ASSISTANT).write_text(_ASSISTANT.format(line=REMEMBERS))
    (repo / ".gitignore").write_text("__pycache__/\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "A small assistant that remembers")
    git(repo, "checkout", "-q", "-b", BRANCH)
    (repo / ASSISTANT).write_text(_ASSISTANT.format(line=FORGETS_LINE))
    git(repo, "commit", "-q", "-am", "Move memory to a new store")
    git(repo, "checkout", "-q", BASE)
    return {ref: git(repo, "rev-parse", ref).strip() for ref in (BASE, BRANCH)}


def checkout_of(repo: Path) -> tuple[str, ...]:
    """The repository's own checkout, as a person would see it changed: its branch, last commit,
    files and status, and its branches and tags."""
    return (
        git(repo, "symbolic-ref", "HEAD"),
        git(repo, "rev-parse", "HEAD"),
        git(repo, "--no-optional-locks", "status", "--porcelain", "-uall"),
        git(repo, "for-each-ref", "refs/heads", "refs/tags"),
        (repo / ASSISTANT).read_text(),
    )


def _target(repo: Path, port: int) -> dict[str, Any]:
    """``demo-assistant``: the made-up app's API, its code the repository. The API's addresses are
    written against ``{url}``, so they reach the copy the run starts; ``url`` is never called."""

    def api(*route: str) -> str:
        return "{url}/api/" + "/".join(route)

    return {
        "adapter": "http",
        "url": f"http://127.0.0.1:{port}",
        "code": {"path": str(repo)},
        "automatic_tools": ["recall"],
        "http": {
            "open": {"url": api("sessions"), "id": "$.session.id"},
            "send": {
                "url": api("sessions", "{session}", "messages"),
                "body": {"message": "{message}"},
            },
            "cancel": {"url": api("sessions", "{session}", "cancel")},
            "reply": "$",
            "background": True,
            "done": {
                "poll": {
                    "url": api("sessions", "{session}"),
                    "until": "$.work.status == 'done'",
                    "reply": "$.items[?data.role=='assistant'][-1].data.content",
                    "interval": 0.2,
                }
            },
        },
    }


def prepare(folder: Path, web_dir: Path, port: int) -> Path:
    """The demo in *folder*, for a server on *port*: the repository (``app/``), the config
    (``evalution.yaml``, the data in ``data/``, the page from *web_dir*), the three tests in
    ``sets/``, and the reset plan and build recipe the versions' copies start from, both saved as
    proven. The config's path. Writes nothing outside *folder*."""
    folder = folder.resolve()
    repo = folder / "app"
    make_repo(repo)
    config = folder / "evalution.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "data_dir": "data",
                "sets_dir": "sets",
                "web_dir": str(web_dir),
                "traces": {"settle_seconds": 1, "stuck_seconds": 60},
                # Its port is never listened on: the runs play on copies only.
                "targets": {TARGET: _target(repo, ui.free_port())},
            },
            sort_keys=False,
        )
    )
    cases = folder / "sets" / TARGET / SET
    cases.mkdir(parents=True)
    about = f"Three tests played on {BASE} and on {BRANCH} by `evalution demo versions`."
    (cases / "set.json").write_text(json.dumps({"name": "Versions demo", "about": about}))
    for stem, case in CASES.items():
        (cases / f"{stem}.case.json").write_text(json.dumps(case, indent=2, ensure_ascii=False))
    kept = folder / "copy-data"
    kept.mkdir()
    launch = {
        "kind": "process",
        # Started in the repository: a version's copy starts in the same place in its worktree.
        "command": [sys.executable, str(repo / ASSISTANT), "{port}"],
        "cwd": str(repo),
        "env": {"TRACES": f"http://127.0.0.1:{port}/v1/traces"},
        "roots": [str(kept)],
        "health": "/admin",
    }
    plan = Plan(
        target=TARGET,
        stores=[PlannedStore("scratch", "dir", str(kept), strategy="snapshot")],
        launch=launch,
        status="proven",
    )
    data = folder / "data"
    PlanStore(data).save(plan)
    recipe = propose(repo, TARGET, launch)
    recipe.status = "proven"
    RecipeStore(data).save(recipe)
    return config


def refusal(web_dir: Path) -> str | None:
    """Why the demo can't play here, in one sentence with the one command; ``None`` when it can."""
    if shutil.which("git") is None:
        return "The versions demo needs git, to make the small repository it tests. Install git."
    if not (web_dir / "index.html").is_file():
        return (
            "The versions demo shows its result on evalution's page, which isn't built. "
            f"Run: {WEB_BUILD}"
        )
    return None


def run_url(page: str, run: dict[str, Any]) -> str:
    """Where the page shows *run*, its comparison first."""
    place = {"target": run["target"], "set": run["set"], "view": "runs", "run": run["run"]}
    return f"{page.rstrip('/')}/?{urlencode(place)}"


def _passed(doc: dict[str, Any]) -> str:
    totals = doc.get("totals") or {}
    return f"{totals.get('passed', 0)} of {totals.get('cases', len(CASES))} tests passed"


def summary(compared: dict[str, Any]) -> str:
    """The comparison in a line: "feature-x vs main: 1 test got worse: Remembers the dog's
    name"."""
    heading = (compared.get("versions") or {}).get("heading") or f"{BRANCH} vs {BASE}"
    worse = [t["name"] for t in compared.get("tests") or [] if t["change"] == "worse"]
    said = f"{heading}: {compared.get('headline', 'nothing to compare')}"
    return f"{said}: {', '.join(worse)}" if worse else said


def came_out(runs: dict[str, dict[str, Any]], compared: dict[str, Any]) -> str | None:
    """What didn't come out as it should, in words; ``None`` when all did."""
    main, branch = runs[BASE], runs[BRANCH]
    if main.get("status") != "done" or branch.get("status") != "done":
        return "a run didn't finish"
    if (main.get("totals") or {}).get("passed") != len(CASES):
        return f"not every test passed on {BASE}"
    if compared.get("base") != main["run"]:
        return f"{BRANCH}'s run wasn't compared with {BASE}'s"
    worse = [t["case"] for t in compared.get("tests") or [] if t["change"] == "worse"]
    if worse != [FORGETS]:
        return f"the tests that got worse were {worse or 'none'}, not {FORGETS}"
    return None


def _stops_on_signals() -> None:
    """Ctrl-C, and a plain ``kill``, stop the demo and delete its folder, even when it was started
    in the background, where a shell starts it with Ctrl-C ignored (so would its server be)."""

    def interrupted(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, interrupted)


def run(
    web_dir: Path, *, open_browser: bool = True, say: Callable[[str], None] | None = None
) -> int:
    """``evalution demo versions``: 0 when both runs came out as they should, once stopped."""
    tell = say or (lambda line: print(line, flush=True))
    if why := refusal(web_dir):
        print(f"evalution: {why}", file=sys.stderr)
        return 1
    _stops_on_signals()
    try:
        demo = ui.Demo(web_dir, set_up=prepare, prefix="evalution-demo-versions-")
    except subprocess.CalledProcessError as exc:
        print(f"evalution: the demo's repository wasn't made: {exc.stderr}", file=sys.stderr)
        return 1
    repo = demo.folder.resolve() / "app"
    tell(f"evalution: demo data in {demo.folder} (a scratch folder, deleted when you stop it)")
    tell(
        f"evalution: a small assistant's repository in {repo}, on {BASE}; its branch {BRANCH} "
        "changes one line, and it no longer remembers what it's told"
    )
    ok = False
    try:
        before = checkout_of(repo)
        demo.start()
        tell(
            f"evalution: playing {len(CASES)} tests, {TRIES} tries each, on {BASE} and then on "
            f"{BRANCH}, each checked out into evalution's own folder. Nothing is spent."
        )
        began = time.monotonic()
        runs: dict[str, dict[str, Any]] = {}
        for ref in (BASE, BRANCH):
            started = time.monotonic()
            asked = {"target": TARGET, "set": SET, "tries": TRIES, "ref": ref}
            doc = demo.play(asked)
            runs[ref] = doc
            tell(
                f"evalution: run {doc['run']} of {described(doc.get('version'))}: "
                f"{doc['status']} in {time.monotonic() - started:.0f} s, {_passed(doc)}"
            )
        compared = demo.client.get(f"/v1/evals/runs/{runs[BRANCH]['run']}/comparison").json()
        tell(f"evalution: {summary(compared)}")
        untouched = checkout_of(repo) == before
        tell(
            f"evalution: the repository's own checkout is still on {BASE}, untouched"
            if untouched
            else "evalution: the repository's own checkout changed, which it never should"
        )
        why = came_out(runs, compared) or (None if untouched else "the checkout changed")
        ok = why is None
        if why:
            tell(f"evalution: the demo didn't come out as it should: {why}")
        tell(f"evalution: played in {time.monotonic() - began:.0f} s")
        url = run_url(demo.url, runs[BRANCH])
        tell(f"evalution: {BRANCH} compared with {BASE}: {url}")
        if open_browser:
            webbrowser.open(url)
        tell("evalution: Ctrl-C stops the demo and deletes its folder.")
        assert demo.server is not None
        demo.server.wait()
        tell("evalution: the demo's server stopped")
        return 1
    except KeyboardInterrupt:
        return 0 if ok else 1
    except (RuntimeError, httpx.HTTPError, subprocess.CalledProcessError) as exc:
        print(f"evalution: the demo stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        demo.stop()
        tell("evalution: the demo is stopped, and its folder deleted")
