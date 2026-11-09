"""Installing what UI testing lacks from the page (ADR 0010; docs/plans/ui-testing-ease.md, 2): the
same pieces ``./install.sh`` installs, on the computer the server runs on, one job at a time.

What runs is fixed, never taken from a request: each step's command is built here from this
server's own Python (``sys.executable``) and, for the page extra, its own ``uv``:

* ``playwright``: ``uv sync --project <server> --extra page --inexact``, only when this evalution
  runs from its own checkout under uv (its ``.venv`` is the Python running now); ``--inexact``
  leaves every other package as it is. Installed any other way, :func:`plan` says how instead.
* ``browser``: ``<python> -m playwright install chromium``.
* ``ffmpeg``: ``<python> -m playwright install ffmpeg``, which a try's video needs.

Only what :func:`~evalution.runs.page.target.page_parts` finds missing is installed, so with
everything there a job downloads nothing and says it's ready. A job keeps its steps as the page
follows them (``waiting``, ``running``, ``done``, ``failed``, with the seconds each took) and the
last lines its commands printed.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evalution.runs.page import target as page_target

_logger = logging.getLogger(__name__)

#: Lines of what the commands printed that a job keeps, the newest last, and how much of each.
LINES_KEPT = 40
LINE_CHARS = 300
#: Seconds one step may take: a download on a slow line, not a hang.
STEP_SECONDS = 20 * 60
#: What each piece's step is called on the page.
LABELS = {
    "playwright": "Add Playwright to evalution",
    "browser": "Download Chromium",
    "ffmpeg": "Download ffmpeg for video",
}
#: Why the page extra can't be added from here: evalution wasn't installed from its checkout.
NOT_A_CHECKOUT = (
    "evalution isn't running from its own checkout under uv, so Playwright can't be added from "
    "here: install evalution with its page extra (for example pip install 'evalution[page]') "
    "and start it again."
)
NO_UV = (
    "evalution runs from its checkout, but uv isn't found, so Playwright can't be added from "
    "here: install uv (https://docs.astral.sh/uv/) and run ./install.sh."
)
_BREAKS = re.compile(r"[\r\n]+")


@dataclass(frozen=True)
class Step:
    """One piece to install, and the fixed command that installs it."""

    part: str
    command: tuple[str, ...]

    @property
    def label(self) -> str:
        return LABELS[self.part]


def server_folder() -> Path | None:
    """This evalution's server folder when it runs from its checkout under uv: the folder has
    its ``pyproject.toml`` and the Python running now is its ``.venv``'s. ``None`` otherwise."""
    here = Path(__file__).resolve().parents[3]
    if not (here / "pyproject.toml").is_file():
        return None
    venv = Path(os.environ.get("UV_PROJECT_ENVIRONMENT") or here / ".venv")
    if not venv.is_absolute():
        venv = here / venv
    try:
        same = Path(sys.prefix).resolve() == venv.resolve()
    except OSError:
        return None
    return here if same else None


def uv_path() -> str | None:
    """The ``uv`` that runs this server (``uv run`` says it in ``UV``), else the one on the
    path."""
    found = os.environ.get("UV")
    if found and Path(found).is_file():
        return found
    return shutil.which("uv")


def plan(parts: dict[str, bool] | None = None) -> tuple[list[Step], str | None]:
    """The steps that install what *parts* (default :func:`page_parts`) lacks, in order, and why
    the page can't install them, when it can't (``None`` when it can, or nothing is missing)."""
    parts = page_target.page_parts() if parts is None else parts
    python = sys.executable
    steps: list[Step] = []
    if not parts["playwright"]:
        folder = server_folder()
        if folder is None:
            return [], NOT_A_CHECKOUT
        uv = uv_path()
        if uv is None:
            return [], NO_UV
        steps.append(
            Step(
                "playwright",
                (uv, "sync", "--project", str(folder), "--extra", "page", "--inexact"),
            )
        )
    if not parts["browser"]:
        steps.append(Step("browser", (python, "-m", "playwright", "install", "chromium")))
    if not parts["ffmpeg"]:
        steps.append(Step("ffmpeg", (python, "-m", "playwright", "install", "ffmpeg")))
    return steps, None


def _step(step: Step) -> dict[str, Any]:
    return {
        "key": step.part,
        "label": step.label,
        "state": "waiting",
        "seconds": None,
        "detail": None,
    }


@dataclass
class Job:
    """One install as the page follows it: a step per piece, and what the commands printed."""

    steps: list[Step]
    started: float = field(default_factory=time.time)
    #: ``running``, ``done`` (everything is there now) or ``failed``.
    state: str = "running"
    shown: list[dict[str, Any]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    ended: float | None = None

    def __post_init__(self) -> None:
        if not self.shown:
            self.shown = [_step(step) for step in self.steps]

    def said(self, text: str) -> None:
        for line in _BREAKS.split(text):
            if line := line.strip():
                self.lines.append(line[:LINE_CHARS])
        del self.lines[:-LINES_KEPT]

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started": self.started,
            "ended": self.ended,
            "steps": [dict(s) for s in self.shown],
            "lines": list(self.lines),
            "error": self.error,
        }


async def run_command(command: tuple[str, ...], said: Callable[[str], None]) -> int:
    """Run *command* (fixed, never a shell), passing on what it prints as it prints it; its exit
    code."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    try:
        async with asyncio.timeout(STEP_SECONDS):
            while chunk := await process.stdout.read(4096):
                said(chunk.decode("utf-8", errors="replace"))
            return await process.wait()
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await asyncio.shield(process.wait())
        raise


class Installer:
    """The page's installing of what UI testing lacks: one job at a time, in the background."""

    def __init__(self) -> None:
        self.job: Job | None = None
        self._task: asyncio.Task[None] | None = None

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> Job | None:
        """Install what's missing now; ``None`` when nothing is.

        :raises RuntimeError: A job is going, or what's missing can't be installed from here
            (the message says how instead).
        """
        if self.running():
            raise RuntimeError("UI testing is being installed already.")
        steps, why_not = plan()
        if why_not is not None:
            raise RuntimeError(why_not)
        if not steps:
            return None
        self.job = job = Job(steps)
        self._task = asyncio.create_task(self._go(job))
        return job

    async def _go(self, job: Job) -> None:
        current: dict[str, Any] | None = None
        try:
            for step, shown in zip(job.steps, job.shown, strict=True):
                current = shown
                shown["state"] = "running"
                began = time.monotonic()
                job.said(f"$ {' '.join(step.command)}")
                code = await run_command(step.command, job.said)
                shown["seconds"] = round(time.monotonic() - began, 1)
                if code != 0:
                    shown["state"] = "failed"
                    shown["detail"] = f"It stopped with exit code {code}: its last lines are below."
                    job.state, job.error = "failed", f"{step.label} didn't finish."
                    return
                shown["state"] = "done"
                # A package added to this Python's site-packages is found without a restart.
                importlib.invalidate_caches()
            lacking = [part for part, there in page_target.page_parts().items() if not there]
            if lacking:
                job.state = "failed"
                job.error = f"Installed, but UI testing still lacks {', '.join(lacking)}."
            else:
                job.state = "done"
        except asyncio.CancelledError:
            job.state, job.error = "failed", "Stopped before it finished."
            if current is not None and current["state"] == "running":
                current["state"] = "failed"
            raise
        except Exception as exc:
            _logger.warning("ui testing: install failed", exc_info=True)
            job.state, job.error = "failed", str(exc) or type(exc).__name__
            if current is not None and current["state"] == "running":
                current["state"] = "failed"
        finally:
            job.ended = time.time()

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
