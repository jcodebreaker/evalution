"""A baseline: a run kept as a file in the app's repo (``run --save``), which ``run --against``
compares later runs with. It lives in git, not in the database, because CI starts from an empty
database and because a change to it should be read in the pull request like any other.

Accepting a test, like updating a snapshot, copies that one test's result from a newer run into
the file and leaves every other test's as it was, so the diff shows exactly what was accepted.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evalution.results.migrate import upcast
from evalution.results.views import totals


def read(path: Path) -> dict[str, Any]:
    """The run kept at *path*, read as today's runs are."""
    return upcast(json.loads(path.read_text()))


def write(path: Path, run: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, indent=1) + "\n")


def accept(kept: Mapping[str, Any], stem: str, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """*kept* with test *stem* as the newest of *runs* that played it left it. Other tests, and
    the setup the baseline records, stay as they were. LookupError for a test neither knows, or
    one no run played."""
    if stem not in kept["cases"] and not any(stem in r["cases"] for r in runs):
        raise LookupError(f"no test {stem} in {kept['target']}/{kept['set']}")
    played = next((r for r in runs if (r["cases"].get(stem) or {}).get("tries")), None)
    if played is None:
        raise LookupError(f"no run of {stem} to accept: run it first, or name one with --run")
    done = copy.deepcopy(dict(kept))
    done["cases"][stem] = copy.deepcopy(played["cases"][stem])
    version = (played.get("case_versions") or {}).get(stem)
    if version:
        done.setdefault("case_versions", {})[stem] = version
    if "totals" in done:
        done["totals"] = totals(done)
    return done
