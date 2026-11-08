"""``evalution demo ui --no-open``, played to the end as a person would run it: in its own scratch
folder, a real server and runner, the made-up app's copy and a real browser. Two tries: one passed
with its reply shown, one failed with it not shown (answered ✓, shown ✕); their screenshots on
disk; the server answering on the address it printed; and Ctrl-C stopping it all and deleting the
folder. Spends nothing.

Skipped without the page extra or a browser, as ``test_page_runs.py`` is.
"""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright", reason="the UI demo needs the page extra: uv sync --extra page")

from test_page_runs import _a_browser

if not _a_browser():
    pytest.skip("no browser for the UI demo", allow_module_level=True)

FOLDER = re.compile(r"demo data in (\S+)")
URL = re.compile(r"the failing test's result: (\S+)")


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


def test_the_ui_demo_shows_a_shown_and_a_dropped_reply_and_cleans_up(tmp_path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>evalution</title>")
    config = tmp_path / "evalution.yaml"
    config.write_text(f"web_dir: {web}\n")
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    began = time.monotonic()
    demo = subprocess.Popen(
        [sys.executable, "-m", "evalution.cli", "--config", str(config), "demo", "ui", "--no-open"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    try:
        folder = Path(_until(demo, lines, FOLDER, 30).group(1))
        url = _until(demo, lines, URL, 300).group(1)
        played = time.monotonic() - began
        said = "".join(lines)
        assert re.search(r"passed\s+Answer shown\s+answered ✓, shown ✓", said), said
        assert re.search(r"failed\s+Remembers the dog's name\s+answered ✓, shown ✕", said), said
        assert played < 120, f"the demo took {played:.0f} s to show its result"

        # The person's own data folder is untouched: the demo's is its own.
        assert folder.is_dir() and not folder.is_relative_to(tmp_path)
        assert not (tmp_path / "data" / "isolation").exists()

        # The two tries, as the result page reads them.
        base, query = url.split("/?", 1)
        run = re.search(r"run=([\w-]+)", query).group(1)
        with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            page = client.get(url)
            assert page.status_code == 200 and "<title>evalution</title>" in page.text
            tries = {}
            for case in ("answer-shown", "reply-dropped"):
                (attempt,) = client.get(f"/v1/evals/runs/{run}/cases/{case}").json()["tries"]
                tries[case] = attempt
        assert tries["answer-shown"]["outcome"] == "passed"
        assert tries["answer-shown"]["layers"]["shown"] == "pass"
        assert tries["reply-dropped"]["outcome"] == "failed"
        assert tries["reply-dropped"]["layers"] == {
            "stored": "absent",
            "answered": "pass",
            "shown": "fail",
        }

        # Screenshots of every turn, on disk.
        kept = folder / "data" / "files" / run
        for shot in ("answer-shown.1/turn-1.png", "reply-dropped.1/turn-2.png"):
            assert (kept / shot).read_bytes()[:4] == b"\x89PNG", shot
    finally:
        demo.send_signal(signal.SIGINT)
        out, _ = demo.communicate(timeout=60)
        lines.append(out)
    assert demo.returncode == 0, "".join(lines)
    assert "its folder deleted" in out
    assert not folder.exists(), "Ctrl-C deletes the demo's folder"
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"{base}/v1/health", timeout=2, trust_env=False)
