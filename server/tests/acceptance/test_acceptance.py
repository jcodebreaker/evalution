"""What "works out of the box" means, played for real: a real server process, a made-up app and
gateway over real sockets, a dead proxy in the environment, no code for the app, nothing simulated.
Slow on purpose, so it isn't in the default test run: ``pytest tests/acceptance``.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

from evalution.results.store import RunStore
from evalution.storage import Database

HERE = Path(__file__).parent
DEAD_PROXY = {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _ask(port: int, *route: str) -> str:
    return f"http://127.0.0.1:{port}/api/{'/'.join(route)}"


def _target(app: int, **extra) -> dict:
    """The whole of what this app takes to connect: settings, no code."""
    return {
        "adapter": "http",
        "automatic_tools": ["recall"],
        "http": {
            "open": {"url": _ask(app, "sessions"), "id": "$.session.id"},
            "send": {
                "url": _ask(app, "sessions", "{session}", "messages"),
                "body": {"message": "{message}"},
            },
            "cancel": {"url": _ask(app, "sessions", "{session}", "cancel")},
            "reply": "$",
            "background": True,
            "done": {
                "poll": {
                    "url": _ask(app, "sessions", "{session}"),
                    "until": "$.work.status == 'done'",
                    "reply": "$.items[?data.role=='assistant'][-1].data.content",
                    "interval": 0.2,
                }
            },
            **extra,
        },
    }


CASES = {
    "exact-answer": {
        "name": "Exact answer",
        "turns": [{"message": "What is 6 times 7?"}],
        "reply": {"contains": ["42"], "lacks": ["43"]},
        "tools": {"extras": False},
        "limits": {"tries": 2, "seconds": 30},
    },
    "delegated-file": {
        "name": "Delegated file",
        "turns": [{"message": "Please make the report."}],
        "reply": {"contains": ["report.pdf"], "rubric": ["Says the file is ready"]},
        "tools": {"results": [{"name": "write_file", "path": "pages", "want": 2}]},
        "limits": {"tries": 2, "seconds": 60},
    },
    "wrong-answer": {
        "name": "Wrong answer",
        "turns": [{"message": "What is 6 times 7?"}],
        "reply": {"contains": ["43"]},
        "limits": {"tries": 1, "seconds": 30},
    },
    "long-memory": {
        "name": "Long memory",
        "turns": [
            {"message": "Please remember that Rowan leads the northern accounts."},
            *[{"message": f"Small talk, number {n}."} for n in range(20)],
            {"message": "What did I tell you?"},
        ],
        "reply": {"contains": ["Rowan"]},
        "limits": {"tries": 1, "seconds": 120},
    },
    "needs-its-own-memory": {
        "name": "Needs its own memory",
        "turns": [{"message": "What did I tell you?"}],
        "reply": {"contains": ["No."]},
        "needs_clean": ["memory"],
    },
}


class World:
    def __init__(self, root: Path, shared_memory: bool = False) -> None:
        self.root, self.app_port, self.port = root, _port(), _port()
        self.server: subprocess.Popen | None = None
        self.config = root / "evalution.yaml"
        self.config.write_text(
            yaml.safe_dump(
                {
                    "web_dir": "no-page",
                    "traces": {"settle_seconds": 1, "stuck_seconds": 60},
                    "model": {
                        "kind": "openai",
                        "base_url": f"http://127.0.0.1:{self.app_port}/llm/v1",
                        "models": {"strong": "made-up-grader", "light": "made-up-grader"},
                    },
                    "targets": {"bot": _target(self.app_port)},
                }
            )
        )
        cases = root / "sets" / "bot" / "main"
        cases.mkdir(parents=True)
        (cases / "set.json").write_text(json.dumps({"name": "Main"}))
        for stem, case in CASES.items():
            (cases / f"{stem}.case.json").write_text(json.dumps(case))
        self.import_sets()
        env = {
            **os.environ,
            "TRACES": f"http://127.0.0.1:{self.port}/v1/traces",
            "SHARED_MEMORY": "1" if shared_memory else "0",
        }
        self.app = subprocess.Popen(
            [sys.executable, str(HERE / "made_up_app.py"), str(self.app_port)],
            env=env,
            stdout=subprocess.DEVNULL,
        )
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{self.port}", timeout=30, trust_env=False
        )
        self.direct = httpx.Client(timeout=10, trust_env=False)
        self._wait(f"http://127.0.0.1:{self.app_port}/admin")
        self.start()

    def _wait(self, url: str) -> None:
        for _ in range(120):
            try:
                if self.direct.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise AssertionError(f"{url} never came up")

    def import_sets(self) -> None:
        """The tests in ``sets/`` into evalution, as a repo's are."""
        command = [sys.executable, "-m", "evalution.cli", "--config", str(self.config)]
        subprocess.run([*command, "sets", "import"], cwd=self.root, check=True)

    def start(self) -> None:
        # Through a proxy that doesn't exist: anything local that used it would fail.
        env = {**os.environ, **DEAD_PROXY, "NO_PROXY": ""}
        self.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "evalution.cli",
                "--config",
                str(self.config),
                "serve",
                "--port",
                str(self.port),
            ],
            env=env,
            cwd=self.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait(f"http://127.0.0.1:{self.port}/v1/health")

    def kill(self) -> None:
        assert self.server is not None
        self.server.send_signal(signal.SIGKILL)
        self.server.wait()

    def close(self) -> None:
        for process in (self.server, self.app):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()

    def begin(self, cases: list[str], tries: int | None = None) -> str:
        asked = {"target": "bot", "set": "main", "cases": cases}
        if tries is not None:
            asked["tries"] = tries
        found = self.client.post("/v1/evals/runs/estimate", json=asked).json()
        return self.client.post("/v1/evals/runs", json={"token": found["token"]}).json()["run"]

    def wait(self, run: str, seconds: float = 180) -> dict:
        stop = time.time() + seconds
        while time.time() < stop:
            found = self.client.get(f"/v1/evals/runs/{run}").json()
            if found["status"] != "running" and not found.get("analyzing"):
                return found
            time.sleep(0.5)
        raise AssertionError(f"{run} still running")

    def stored(self, run: str) -> dict:
        """The run as the database holds it, read beside the server, as another process would."""
        found = RunStore(Database.open(self.root / "data").docs()).load(run)
        assert found is not None, f"no run {run} stored"
        return found

    def admin(self) -> dict:
        return self.direct.get(f"http://127.0.0.1:{self.app_port}/admin").json()

    def gateway(self, down: bool) -> None:
        self.direct.post(f"http://127.0.0.1:{self.app_port}/admin/gateway", json={"down": down})

    def verdict(self, run: str, case: str) -> dict:
        tests = self.client.get(f"/v1/evals/runs/{run}").json()["tests"]
        return next(t for t in tests if t["case"] == case)


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    try:
        yield made
    finally:
        made.close()


def _checks(attempt: dict) -> dict[str, str]:
    return {c["id"]: c["outcome"] for c in attempt["judgements"][-1]["checks"]}


def test_a_clean_install_connects_without_code_and_gives_a_trustworthy_result(world) -> None:
    run = world.begin(["exact-answer", "delegated-file", "long-memory"])
    assert world.wait(run)["status"] == "done"
    doc = world.stored(run)

    exact = world.verdict(run, "exact-answer")
    assert exact["verdict"] == "pass" and exact["basis"] == "checked"

    # Delegated: graded on the outcome rather than "Queued!", with the child session's evidence.
    filed = doc["cases"]["delegated-file"]["tries"][0]
    assert filed["reply_text"] == "It's ready: report.pdf"
    assert _checks(filed)["result:0:write_file"] == "passed"
    assert world.verdict(run, "delegated-file")["basis"] == "checked"
    assert world.admin()["on_running"] == 0, "no try started on top of the last one's work"

    long = world.verdict(run, "long-memory")
    assert long["verdict"] == "pass"
    assert doc["cases"]["long-memory"]["tries"][0]["messages"] == 22

    # Automatic tools: in the evidence, out of the score, on a test with no room for extras.
    answered = doc["cases"]["exact-answer"]["tries"][0]
    assert answered["automatic"], "the app's own recall call is on record"
    assert _checks(answered)["steps"] == "passed"
    assert answered["trace"] == "complete"

    # A new try reads as a new result, and nothing of a real person's was touched.
    again = world.begin(["exact-answer"])
    assert world.wait(again)["status"] == "done"
    admin = world.admin()
    assert admin["deleted"] == []
    assert admin["real"] == [{"data": {"role": "user", "content": "Something of my own"}}]


def test_control_a_wrong_answer_on_the_same_app_reads_as_a_failed_check(world) -> None:
    run = world.begin(["wrong-answer"])
    assert world.wait(run)["status"] == "done"
    found = world.verdict(run, "wrong-answer")
    assert found["verdict"] == "fail" and found["label"] == "Failed check"


def test_a_test_that_needs_its_own_memory_is_refused_up_front(world) -> None:
    found = world.client.post(
        "/v1/evals/runs/estimate",
        json={"target": "bot", "set": "main", "cases": ["needs-its-own-memory", "exact-answer"]},
    ).json()
    assert found["cases"] == 1
    assert "no earlier memory" in found["refused"][0]["why"]


def test_a_restart_mid_run_is_said_as_that_and_leaves_things_working(world) -> None:
    slow = world.root / "sets" / "bot" / "main" / "slow.case.json"
    slow.write_text(
        json.dumps(
            {
                "name": "Slow",
                "turns": [{"message": "Please take your time."}],
                "reply": {"contains": ["slowly"]},
                "limits": {"tries": 1, "seconds": 60},
            }
        )
    )
    world.import_sets()
    # Past the check, into the test.
    assert world.wait(world.begin(["exact-answer"]))["status"] == "done"
    run = world.begin(["slow"])
    time.sleep(4)
    world.kill()
    world.start()
    found = world.wait(run, 30)
    assert found["status"] == "stopped" and found["interrupted"]
    assert found["tests"][0]["verdict"] in (None, "unjudged"), "says nothing against the app"
    # Its open session was told to stop, and a new run starts.
    stop = time.time() + 15
    while time.time() < stop and not world.admin()["cancelled"]:
        time.sleep(0.5)
    assert world.admin()["cancelled"]
    assert world.wait(world.begin(["exact-answer"]))["status"] == "done"


def test_a_gateway_outage_is_not_held_against_the_app(world) -> None:
    assert world.wait(world.begin(["exact-answer"]))["status"] == "done"
    world.gateway(True)
    run = world.begin(["delegated-file"])
    found = world.wait(run)
    test = next(t for t in found["tests"] if t["case"] == "delegated-file")
    assert test["verdict"] == "unjudged"
    assert test["label"] == "Grader unavailable"
    assert not found["totals"].get("failed")
    # The code's checks still stand, and it recovers.
    attempt = world.stored(run)["cases"]["delegated-file"]["tries"][0]
    assert _checks(attempt)["result:0:write_file"] == "passed"
    world.gateway(False)
    again = world.begin(["delegated-file"])
    world.wait(again)
    assert world.verdict(again, "delegated-file")["verdict"] == "pass"


def test_it_says_what_a_change_did_and_whether_it_could_be_chance(world) -> None:
    before = world.begin(["exact-answer"])
    assert world.wait(before)["status"] == "done"
    # Someone changes the app's model, and its answers get worse.
    world.direct.post(
        f"http://127.0.0.1:{world.app_port}/admin/setup",
        json={"model": "cheaper-model", "answer": "It's about 40."},
    )
    # Two tries, fixed: too few to rule out an agent varying.
    after = world.begin(["exact-answer"], tries=2)
    assert world.wait(after)["status"] == "done"

    found = world.client.get(f"/v1/evals/runs/{after}/comparison").json()
    assert found["base"] == before and found["answer"] == "worse"
    assert {"what": "Model", "before": "made-up-model", "now": "cheaper-model"} in found["changed"]
    (test,) = [t for t in found["tests"] if t["case"] == "exact-answer"]
    assert (
        test["change"] == "worse" and test["before"]["passed"] == 2 and test["now"]["passed"] == 0
    )
    # Two tries each can't rule out an agent varying, and it says so rather than sounding sure.
    assert found["could_be_chance"] and found["headline"].startswith("It looks like")

    # The same answer on the command line, for a check on a pull request: 1 when it got worse.
    said = subprocess.run(
        [sys.executable, "-m", "evalution.cli", "--config", str(world.config), "compare", after],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=world.root,
    )
    assert said.returncode == 1, said.stderr
    assert "Model: made-up-model -> cheaper-model" in said.stdout

    # On a pull request: a saved run is the baseline, and only getting worse than it fails.
    kept = world.root / "baseline.json"
    kept.write_text(json.dumps(world.stored(before)))

    def in_ci(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "evalution.cli",
                "--config",
                str(world.config),
                "run",
                "--target",
                "bot",
                "--set",
                "main",
                "--case",
                "exact-answer",
                "--yes",
                "--traces-port",
                str(world.port),
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=90,
            cwd=world.root,
        )

    world.kill()  # one run at a time: this one's its own process, as in CI
    worse = in_ci("--against", str(kept))
    assert worse.returncode == 1, worse.stdout + worse.stderr
    assert "got worse" in worse.stdout and "cheaper-model" in worse.stdout
    # Control: with no baseline any failing test fails; against itself, nothing got worse.
    alone = in_ci("--save", str(world.root / "bad.json"))
    assert alone.returncode == 1
    same = in_ci("--against", str(world.root / "bad.json"))
    assert same.returncode == 0, same.stdout + same.stderr
    assert "got worse" not in same.stdout
    world.start()

    # More tries, as the page offers, settle it: the answer stops saying it could be chance.
    found = world.client.post(
        "/v1/evals/runs/estimate",
        json={"target": "bot", "set": "main", "cases": ["exact-answer"], "tries": 5},
    ).json()
    assert found["tries"] == 5
    settled = world.client.post("/v1/evals/runs", json={"token": found["token"]}).json()["run"]
    assert world.wait(settled)["status"] == "done"
    final = world.client.get(f"/v1/evals/runs/{settled}/comparison?base={before}").json()
    assert final["answer"] == "worse" and final["tests"][0]["now"]["judged"] == 5

    # Control: put back, and nothing is held against it.
    world.direct.post(
        f"http://127.0.0.1:{world.app_port}/admin/setup",
        json={"model": "made-up-model", "answer": "It's 42."},
    )
    again = world.begin(["exact-answer"])
    assert world.wait(again)["status"] == "done"
    same = world.client.get(f"/v1/evals/runs/{again}/comparison?base={before}").json()
    assert same["answer"] == "same" and same["headline"] == "No change" and not same["changed"]


@pytest.mark.parametrize("shared", [False, True])
def test_a_leak_between_sessions_shows_where_a_clean_start_is_promised(tmp_path, shared) -> None:
    made = World(tmp_path, shared_memory=shared)
    try:
        said = subprocess.run(
            [
                sys.executable,
                "-m",
                "evalution.cli",
                "--config",
                str(made.config),
                "check",
                "--target",
                "bot",
                "--isolation",
            ],
            env={**os.environ, **DEAD_PROXY, "NO_PROXY": ""},
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp_path,
        )
        out = said.stdout + said.stderr
        assert said.returncode == (1 if shared else 0), out
        assert ("knew a word" in out) == shared
    finally:
        made.close()
