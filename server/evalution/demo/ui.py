"""``evalution demo ui``: UI testing, seen working in a minute (docs/ui-testing.md).

In a scratch folder of its own (a new temporary one, deleted when it stops: never the person's own
targets or runs), it sets up a target ``demo-chat`` that plays through the made-up app's chat page
(:mod:`evalution.demo.app`) on evalution's own copy of it, started from a plan saved as proven, as
the acceptance tests set one up. Its set has two tests, both decided by code and the page:

* **Answer shown**: asks a sum, and the page shows the answer. It passes.
* **Remembers the dog's name**: the app remembers and answers, but the page drops that one reply
  (the made-up app's ``DROP_REPLY_TO`` switch, on for that question only). Answered ✓, shown ✕.

It plays both through the real runner, in an ``evalution serve`` of the scratch folder on a free
port, then prints the failing test's result page and opens it in the browser (not with
``--no-open``). Ctrl-C stops the server, and with it the copies, and deletes the folder.

It spends nothing: the made-up gateway is the app's model, no model is set up for evalution (so
the failed try has no written analysis), and every check is decided by code.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import yaml

from evalution.config import ALLOWED_HOSTS_ENV
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.results.markdown import finding_url
from evalution.runs.page.target import PAGE_SETUP, page_parts

TARGET = "demo-chat"
SET = "ui-demo"
PASSES = "answer-shown"
FAILS = "reply-dropped"
#: The question whose reply the page drops: the app still answers it.
DROPPED = "What did I tell you?"
#: The made-up app's first test account, as its module names them.
EMAIL, PASSWORD = "person0@made-up.test", "made-up-0"
PASSWORD_ENV = "DEMO_PASSWORD_0"  # noqa: S105 (a name, not a value)
#: What the demo's server and the copies it starts must not take from the person's environment:
#: their own database, config, secret key and allowed names.
NOT_PASSED = ("DATABASE_URL", "EVALUTION_CONFIG", "EVALUTION_SECRET_KEY", ALLOWED_HOSTS_ENV)
#: Seconds the server gets to start, the run to play, and the server to stop.
START_SECONDS, PLAY_SECONDS, STOP_SECONDS = 60.0, 300.0, 20.0
WEB_BUILD = "npm ci --prefix web && npm run build --prefix web"

#: The made-up app's page, as its module names what is on it (the same map the acceptance tests
#: play): signed in with the slot's test account, on the copy's address only.
PAGE: dict[str, Any] = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "sign_in": {
        "form": {
            "url": "{copy_page}/login",
            "fill": [
                {"role": "textbox", "name": "Email", "value": "{account.email}"},
                {"role": "textbox", "name": "Password", "value": "{account.password}"},
            ],
            "submit": {"role": "button", "name": "Sign in"},
            "done": {"url_matches": "/chat"},
        }
    },
    "accounts": "seed",
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "new_chat": {"click": {"role": "button", "name": "New chat"}},
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {
        "in": {"role": "log", "name": "Messages"},
        "item": {"role": "article"},
        "mine": {"attr": "data-role", "equals": "assistant"},
    },
    "busy": [{"role": "button", "name": "Stop"}, {"attr": "aria-busy", "equals": "true"}],
    "catch_up_seconds": 10,
    # Every message's screenshot; the video only of a try that fails.
    "proof": {"screenshots": "always", "video": "failed"},
}

#: The two tests. The page's checks decide, so a reply that never showed fails its test.
CASES: dict[str, dict[str, Any]] = {
    PASSES: {
        "name": "Answer shown",
        "about": "Asks a sum through the page; the page shows the answer.",
        "turns": [{"message": "What is 6 times 7?"}],
        "reply": {"contains": ["42"]},
        "page": {"decides": True},
        "limits": {"tries": 1, "seconds": 60},
    },
    FAILS: {
        "name": "Remembers the dog's name",
        "about": (
            "The app remembers and answers, but the made-up app's page drops this reply "
            "(a switch, on for this question only): answered, not shown."
        ),
        "turns": [
            {"message": "Please remember that my dog is called Biscuit."},
            {"message": DROPPED},
        ],
        "reply": {"contains": ["Biscuit"]},
        "page": {"decides": True},
        "limits": {"tries": 1, "seconds": 90},
    },
}


def refusal(web_dir: Path) -> str | None:
    """Why the demo can't play here, in one sentence with the one command; ``None`` when it can."""
    parts = page_parts()
    if not (parts["playwright"] and parts["browser"]):
        return f"The UI demo needs the page extra and a browser. Set them up with: {PAGE_SETUP}"
    if not (web_dir / "index.html").is_file():
        return (
            f"The UI demo shows its result on evalution's page, which isn't built. Run: {WEB_BUILD}"
        )
    return None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _target(port: int) -> dict[str, Any]:
    """``demo-chat``: the made-up app's API under its page. The API's addresses are written
    against ``{url}``, so they reach the copy the run starts; ``url`` itself is never called."""

    def api(*route: str) -> str:
        return "{url}/api/" + "/".join(route)

    return {
        "adapter": "http",
        "url": f"http://127.0.0.1:{port}",
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
        "page": PAGE,
        "page_accounts": [{"email": EMAIL, "password_env": PASSWORD_ENV}],
    }


def prepare(folder: Path, web_dir: Path, port: int) -> Path:
    """The demo in *folder*, for a server on *port*: its config (``evalution.yaml``, the data in
    ``data/``, the page from *web_dir*), its two tests as files in ``sets/``, and the plan its copy
    starts from, saved as proven. The config's path. Writes nothing outside *folder*."""
    config = folder / "evalution.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "data_dir": "data",
                "sets_dir": "sets",
                "web_dir": str(web_dir),
                "traces": {"settle_seconds": 1, "stuck_seconds": 60},
                # Its port is never listened on: the run plays on the copy only.
                "targets": {TARGET: _target(free_port())},
            },
            sort_keys=False,
        )
    )
    cases = folder / "sets" / TARGET / SET
    cases.mkdir(parents=True)
    about = "Two tests played through the made-up app's page by `evalution demo ui`."
    (cases / "set.json").write_text(json.dumps({"name": "UI demo", "about": about}))
    for stem, case in CASES.items():
        (cases / f"{stem}.case.json").write_text(json.dumps(case, indent=2, ensure_ascii=False))
    kept = folder / "copy-data"
    kept.mkdir()
    plan = Plan(
        target=TARGET,
        stores=[PlannedStore("scratch", "dir", str(kept), strategy="snapshot")],
        launch={
            "kind": "process",
            "command": [sys.executable, "-m", "evalution.demo.app", "{port}"],
            "cwd": str(folder),
            "env": {"TRACES": f"http://127.0.0.1:{port}/v1/traces", "DROP_REPLY_TO": DROPPED},
            "roots": [str(kept)],
            "health": "/admin",
        },
        status="proven",
    )
    PlanStore(folder / "data").save(plan)
    return config


def environment() -> dict[str, str]:
    """The demo's processes' environment: the person's, without what would reach their own data,
    and with the test account's password."""
    env = {k: v for k, v in os.environ.items() if k not in NOT_PASSED}
    return {**env, PASSWORD_ENV: PASSWORD}


class Demo:
    """The demo's folder, server and run, from :meth:`start` to :meth:`stop`."""

    def __init__(self, web_dir: Path) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="evalution-demo-"))
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.config = prepare(self.folder, web_dir, self.port)
        self.server: subprocess.Popen[bytes] | None = None
        self.client = httpx.Client(base_url=self.url, timeout=30, trust_env=False)

    def _cli(self, *args: str) -> list[str]:
        return [sys.executable, "-m", "evalution.cli", "--config", str(self.config), *args]

    def start(self) -> None:
        """Import the tests and start ``evalution serve`` on the scratch folder."""
        env = environment()
        subprocess.run(  # noqa: S603 (evalution's own CLI)
            self._cli("sets", "import"),
            cwd=self.folder,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        log = (self.folder / "serve.log").open("wb")
        self.server = subprocess.Popen(  # noqa: S603 (evalution's own CLI)
            self._cli("serve", "--port", str(self.port)),
            cwd=self.folder,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        stop = time.monotonic() + START_SECONDS
        while time.monotonic() < stop:
            if self.server.poll() is not None:
                raise RuntimeError(f"the demo's server stopped: {self._log_tail()}")
            try:
                if self.client.get("/v1/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError(f"the demo's server didn't start: {self._log_tail()}")

    def _log_tail(self) -> str:
        found = (self.folder / "serve.log").read_text(errors="replace").strip().splitlines()
        return " / ".join(found[-5:]) or "nothing in its log"

    def play(self) -> dict[str, Any]:
        """Both tests through the real runner, once each; the run as the API shows it."""
        asked = {"target": TARGET, "set": SET, "tries": 1}
        estimate = self.client.post("/v1/evals/runs/estimate", json=asked).json()
        if "token" not in estimate:
            raise RuntimeError(f"the run wasn't estimated: {estimate}")
        started = self.client.post("/v1/evals/runs", json={"token": estimate["token"]}).json()
        run = started["run"]
        stop = time.monotonic() + PLAY_SECONDS
        while time.monotonic() < stop:
            found = self.client.get(f"/v1/evals/runs/{run}").json()
            if found["status"] != "running" and not found.get("analyzing"):
                return found
            time.sleep(0.5)
        raise RuntimeError(f"run {run} was still playing after {PLAY_SECONDS:.0f} s")

    def tries(self, run: str) -> dict[str, dict[str, Any]]:
        """Each test's one try, as the result page reads it."""
        found = {}
        for stem in CASES:
            case = self.client.get(f"/v1/evals/runs/{run}/cases/{stem}").json()
            found[stem] = case["tries"][0] if case.get("tries") else {}
        return found

    def stop(self) -> None:
        """The server, then the folder. Stopping the server takes down any copy it started, and
        a copy it couldn't stop stops itself: the made-up app exits once its starter is gone."""
        self.client.close()
        server, self.server = self.server, None
        if server is not None and server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(STOP_SECONDS)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        shutil.rmtree(self.folder, ignore_errors=True)


def _marks(layers: dict[str, str]) -> str:
    sign = {"pass": "✓", "fail": "✕", "unjudged": "?"}
    return ", ".join(f"{layer} {sign[said]}" for layer, said in layers.items() if said in sign)


def run(
    web_dir: Path, *, open_browser: bool = True, say: Callable[[str], None] | None = None
) -> int:
    """``evalution demo ui``: 0 when both tests came out as they should, once stopped."""
    tell = say or (lambda line: print(line, flush=True))
    if why := refusal(web_dir):
        print(f"evalution: {why}", file=sys.stderr)
        return 1
    if not page_parts()["ffmpeg"]:
        tell(
            "evalution: no video in this demo: Playwright's ffmpeg isn't installed "
            "(uv run --project server playwright install ffmpeg). Screenshots are kept."
        )
    demo = Demo(web_dir)
    tell(f"evalution: demo data in {demo.folder} (a scratch folder, deleted when you stop it)")
    ok = False
    try:
        demo.start()
        tell(
            f"evalution: playing 2 tests through {TARGET}'s page, on evalution's copy of the "
            "made-up app. Nothing is spent: its own gateway answers."
        )
        began = time.monotonic()
        doc = demo.play()
        tries = demo.tries(doc["run"])
        tell(f"evalution: run {doc['run']} {doc['status']} in {time.monotonic() - began:.0f} s")
        for stem, attempt in tries.items():
            outcome = attempt.get("outcome", "not played")
            tell(f"  {outcome:<9} {CASES[stem]['name']:<26} {_marks(attempt.get('layers') or {})}")
        ok = (
            tries[PASSES].get("outcome") == "passed"
            and tries[FAILS].get("outcome") == "failed"
            and (tries[FAILS].get("layers") or {}).get("shown") == "fail"
        )
        if not ok:
            tell(f"evalution: the demo didn't come out as it should: {doc.get('error') or doc}")
        url = finding_url(demo.url, doc, FAILS)
        tell(f"evalution: the failing test's result: {url}")
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
