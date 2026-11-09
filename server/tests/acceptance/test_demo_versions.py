"""``evalution demo versions --no-open``, played to the end as a person would run it: in its own
scratch folder, a real server and runner, real git and the made-up app's copies started in
evalution's worktrees. Run 1 played ``main``, run 2 ``feature-x``, each vouched for as that
commit's tree; run 2 is compared by default with run 1, and one test got worse; the repository's
own checkout is still on ``main``, untouched; and Ctrl-C stops it all and deletes the folder, the
worktrees with it. Spends nothing, needs no browser.
"""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

FOLDER = re.compile(r"demo data in (\S+)")
URL = re.compile(r"feature-x compared with main: (\S+)")
TARGET, SET = "demo-assistant", "versions-demo"


def _until(process: subprocess.Popen, lines: list[str], pattern: re.Pattern, seconds: float):
    """Read the demo's output until *pattern* matches a line; its match."""
    assert process.stdout is not None
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        line = process.stdout.readline()
        if not line:
            raise AssertionError("the demo ended early:\n" + "".join(lines))
        lines.append(line)
        if found := pattern.search(line):
            return found
    raise AssertionError("the demo didn't say it in time:\n" + "".join(lines))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_the_versions_demo_compares_feature_x_with_main_and_cleans_up(tmp_path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>evalution</title>")
    config = tmp_path / "evalution.yaml"
    config.write_text(f"web_dir: {web}\n")
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    began = time.monotonic()
    demo = subprocess.Popen(
        [
            sys.executable, "-m", "evalution.cli", "--config", str(config),
            "demo", "versions", "--no-open",
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )  # fmt: skip
    lines: list[str] = []
    try:
        folder = Path(_until(demo, lines, FOLDER, 30).group(1)).resolve()
        url = _until(demo, lines, URL, 300).group(1)
        played = time.monotonic() - began
        said = "".join(lines)
        assert "feature-x vs main: 1 test got worse: Remembers the dog's name" in said, said
        assert "still on main, untouched" in said and "didn't come out" not in said, said
        assert played < 180, f"the demo took {played:.0f} s to show its result"
        for kept in ("isolation", "builds", "versions"):
            assert not (tmp_path / "data" / kept).exists(), "the person's own data isn't used"

        repo = folder / "app"
        commits = {ref: _git(repo, "rev-parse", ref) for ref in ("main", "feature-x")}
        base = url.split("/?", 1)[0]
        with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            found = client.get("/v1/evals/runs", params={"target": TARGET, "set": SET}).json()
            second, first = (r["run"] for r in found["data"])
            assert f"run={second}" in url
            docs = {r: client.get(f"/v1/evals/runs/{r}").json() for r in (first, second)}
            compared = client.get(f"/v1/evals/runs/{second}/comparison").json()

        # Each run played its branch, checked out by evalution and vouched for as its tree.
        for run, ref in ((first, "main"), (second, "feature-x")):
            version = docs[run]["version"]
            assert docs[run]["status"] == "done"
            assert version["ref"] == ref and version["branch"] == ref
            assert version["commit"] == commits[ref] and docs[run]["commit"] == commits[ref]
            assert version["verified"] is True and version["dirty"] is False
            assert version["played"]["kind"] == "tree"
            assert version["played"]["code"] == f"commit:{commits[ref]}"
            worktree = folder / "data" / "versions" / TARGET / commits[ref][:12]
            assert Path(version["played"]["folder"]) == worktree
        assert docs[first]["totals"]["passed"] == 3 and docs[second]["totals"]["passed"] == 2

        # The branch is compared with main by default, and one test got worse.
        assert compared["base"] == first
        assert compared["base_branch"] == {"name": "main", "run": first, "missing": None}
        assert compared["versions"]["heading"] == "feature-x vs main"
        worse = [t["case"] for t in compared["tests"] if t["change"] == "worse"]
        assert worse == ["remembers-the-dog"] and compared["headline"] == "1 test got worse"
        assert compared["code"] is None
        code = {
            "what": "Code",
            "before": f"main @ {commits['main'][:7]}",
            "now": f"feature-x @ {commits['feature-x'][:7]}",
        }
        assert code in compared["changed"], "each run's commit is its version's"

        # The repository's own checkout: on main, clean, its branches where they were.
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
        assert _git(repo, "rev-parse", "HEAD") == commits["main"]
        assert _git(repo, "status", "--porcelain", "-uall") == ""
        assert "made_up.remembers = True" in (repo / "assistant.py").read_text()
        heads = _git(repo, "for-each-ref", "--format=%(refname)", "refs/heads")
        assert heads.split() == ["refs/heads/feature-x", "refs/heads/main"]
    finally:
        demo.send_signal(signal.SIGINT)
        out, _ = demo.communicate(timeout=60)
        lines.append(out)
    assert demo.returncode == 0, "".join(lines)
    assert "its folder deleted" in out
    assert not folder.exists(), "Ctrl-C deletes the demo's folder, worktrees and all"
