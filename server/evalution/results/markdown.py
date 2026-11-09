"""A comparison (:func:`evalution.results.compare.comparison`) as short markdown, for a pull
request's comment and the CI job's summary: what got worse, what got better and what could be
chance, one line a test. Each test links to its finding when the run's page can be reached.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from evalution.core.judgement import Change

_MOVED = (Change.WORSE.value, Change.BETTER.value)


def finding_url(page: str, run: Mapping[str, Any], stem: str) -> str:
    """Where the page shows *stem*'s tries in *run*, with what was found."""
    place = {"target": run["target"], "set": run["set"], "view": "runs", "run": run["run"]}
    return f"{page.rstrip('/')}/?{urlencode({**place, 'case': stem})}"


def _tries(side: Mapping[str, Any] | None) -> str:
    return f"{side['passed']}/{side['judged']}" if side else "not run"


def _line(test: Mapping[str, Any], run: Mapping[str, Any], page: str | None) -> str:
    name = test["name"].replace("[", "\\[").replace("]", "\\]")
    named = f"[{name}]({finding_url(page, run, test['case'])})" if page else name
    notes = [test["change"]] if test["chance"] else []
    tail = f" ({'; '.join([*notes, *test['caveats']])})" if notes or test["caveats"] else ""
    return f"- {named} `{test['case']}`: {_tries(test['before'])} → {_tries(test['now'])}{tail}"


def comparison_markdown(
    said: Mapping[str, Any], run: Mapping[str, Any], page: str | None = None
) -> str:
    """*said*, the comparison of *run* with its baseline. *page* is where the run can be opened,
    if anywhere: without it each test is only named."""
    moved = [t for t in said["tests"] if t["change"] in _MOVED]
    sections = (
        ("Worse", [t for t in moved if t["change"] == Change.WORSE.value and not t["chance"]]),
        ("Better", [t for t in moved if t["change"] == Change.BETTER.value and not t["chance"]]),
        ("Could be chance", [t for t in moved if t["chance"]]),
    )
    lines = [f"### {said['headline']}", "", f"`{said['run']}` against `{said['base']}`"]
    for title, tests in sections:
        if tests:
            lines += ["", f"**{title}**", *(_line(t, run, page) for t in tests)]
    if said["changed"]:
        lines += ["", "**Setup**"]
        lines += [f"- {row['what']}: {row['before']} → {row['now']}" for row in said["changed"]]
    if said.get("code"):
        lines += ["", said["code"]]
    return "\n".join(lines) + "\n"
