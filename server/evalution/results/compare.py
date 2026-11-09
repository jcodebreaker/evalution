"""Did this change make it worse? One run against another: what changed in the setup, which tests
moved and whether that could just be an agent varying. All from what is stored: no model call.

A test edited or judged another way between the two is still shown, moved or not, and says so: the
owner can weigh it, where leaving it out would hide what they most want to see.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evalution.core.judgement import Change, WhyUnjudged
from evalution.core.rules import change_of, could_be_chance
from evalution.core.version import DISK, NOT_RECORDED, described, same_played, short
from evalution.results.record import (
    agent_models,
    environment_of,
    judge_model,
    pass_marks,
    unjudged_reasons,
    verdict_of,
)

SHORT_COMMIT = 7


def _setup(run: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two runs of an app different setups, besides their code (:func:`same_code`)."""
    return (run.get("target"), agent_models(run), run.get("prompt"))


def same_code(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Whether two runs played the same code: by what played, when both kept it (ADR 0011);
    else by the commit each recorded."""
    same = same_played(a.get("version"), b.get("version"))
    return same if same is not None else a.get("commit") == b.get("commit")


def branch_of(run: Mapping[str, Any]) -> str | None:
    """The branch *run*'s version is of (ADR 0011): the branch it names, else its ref when that
    isn't the disk; ``None`` for an old run that didn't record its version, or a detached one."""
    version = run.get("version")
    if not version or version.get("why_unverified") == NOT_RECORDED:
        return None
    ref = version.get("ref") or DISK
    return version.get("branch") or (None if ref == DISK else str(ref))


def on_branch(run: Mapping[str, Any], branch: str) -> bool:
    """Whether *run* played *branch*, as the person names it or as ``origin`` does."""
    found = branch_of(run)
    return found is not None and found in (branch, f"origin/{branch}")


def of_another_branch(run: Mapping[str, Any], base_branch: str | None) -> bool:
    """Whether *run* played a branch other than the base branch: what is compared with the base
    branch by default."""
    found = branch_of(run)
    return bool(base_branch and found and not on_branch(run, base_branch))


def _comparable(run: Mapping[str, Any], others: Sequence[Mapping[str, Any]]) -> list[Any]:
    """The other runs *run* can be compared with, newest first: those that played anything, where
    it played; an app on staging and on someone's computer differ in more than the setup says."""
    here = environment_of(run)
    found = [
        r
        for r in others
        if r["run"] != run["run"]
        and environment_of(r) == here
        and any(c.get("tries") for c in r["cases"].values())
    ]
    return sorted(found, key=lambda r: -float(r["started_at"]))


def base_branch_run(
    run: Mapping[str, Any], others: Sequence[Mapping[str, Any]], base_branch: str
) -> str | None:
    """The latest run of *others* (the same target and set) on *base_branch*: an earlier one,
    else a later one, as when the person ran the base branch after seeing there was none."""
    found = [r for r in _comparable(run, others) if on_branch(r, base_branch)]
    began = float(run["started_at"])
    earlier = next((r for r in found if float(r["started_at"]) < began), None)
    chosen = earlier or (found[0] if found else None)
    return chosen["run"] if chosen else None


def default_base(
    run: Mapping[str, Any],
    others: Sequence[Mapping[str, Any]],
    base_branch: str | None = None,
) -> str | None:
    """What *run* is best compared with. A run of a branch other than *base_branch* (the
    repository's ``main``) is compared with the latest run of the same tests on the base branch,
    when there is one (:func:`base_branch_run`). Else the newest earlier run on another setup,
    else the one before it. Only runs that played anything, where it played, count."""
    if of_another_branch(run, base_branch):
        found = base_branch_run(run, others, str(base_branch))
        if found is not None:
            return found
    earlier = [
        r for r in _comparable(run, others) if float(r["started_at"]) < float(run["started_at"])
    ]
    other = next((r for r in earlier if _setup(r) != _setup(run) or not same_code(r, run)), None)
    found = other or (earlier[0] if earlier else None)
    return found["run"] if found else None


def version_name(run: Mapping[str, Any]) -> str:
    """A run's version in a word or two, as a comparison's heading says it: its branch
    ("feature-x"), else its commit, else "not recorded"."""
    commit = (run.get("version") or {}).get("commit") or run.get("commit")
    return branch_of(run) or short(commit) or "not recorded"


def versions(run: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """Both runs' versions, named and described, and "feature-x vs main" when they differ."""
    now, before = version_name(run), version_name(base)
    return {
        "now": {"name": now, "said": described(run.get("version"))},
        "before": {"name": before, "said": described(base.get("version"))},
        "heading": f"{now} vs {before}" if now != before else None,
    }


def base_branch_said(
    run: Mapping[str, Any], others: Sequence[Mapping[str, Any]], base_branch: str | None
) -> dict[str, Any] | None:
    """For a run of another branch than *base_branch*: the base branch's name and its latest run
    of the same tests, with the line said when there is none ("No run of these tests on main
    yet"). ``None`` for a run on the base branch, of the disk off any branch, or an old one."""
    if not of_another_branch(run, base_branch):
        return None
    found = base_branch_run(run, others, str(base_branch))
    return {
        "name": base_branch,
        "run": found,
        "missing": None if found else f"No run of these tests on {base_branch} yet",
    }


def _short(commit: Any) -> str:
    return str(commit)[:SHORT_COMMIT] if commit else "not recorded"


def _label(run: Mapping[str, Any]) -> str:
    """The run's code, as a chip says it: "page-testing @ e535c4d", or its commit alone."""
    branch = (run.get("version") or {}).get("branch")
    commit = _short(run.get("commit"))
    return f"{branch} @ {commit}" if branch and run.get("commit") else commit


_PLAYED = {"images": "images", "tree": "files"}


def code_said(run: Mapping[str, Any], base: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Whether the code changed between *base* and *run*, and a line when what played says
    otherwise than the commits do: the same images under two commits are the same code, other
    images under one commit aren't."""
    same = same_played(base.get("version"), run.get("version"))
    was, now = base.get("commit"), run.get("commit")
    moved = bool(was and now and was != now)
    kind = _PLAYED.get(((run.get("version") or {}).get("played") or {}).get("kind"), "code")
    if same is None:
        return moved, None
    if same and moved:
        return False, (
            f"Same code played: the same {kind} ran in both, "
            f"though the checkout moved from {_short(was)} to {_short(now)}"
        )
    if not same and not moved:
        tail = f", though both runs were of {_short(now)}" if now and was else ""
        return True, f"Different code played: other {kind} ran{tail}"
    return not same, None


def _changed(run: Mapping[str, Any], base: Mapping[str, Any]) -> list[dict[str, str]]:
    """What differs between the two setups, the agent's before the way it was judged."""
    rows = []

    def differs(what: str, was: Any, now: Any) -> None:
        if was and now and was != now:
            rows.append({"what": what, "before": str(was), "now": str(now)})

    differs("App", base.get("target"), run.get("target"))
    differs("Model", ", ".join(agent_models(base)), ", ".join(agent_models(run)))
    if code_said(run, base)[0]:
        rows.append({"what": "Code", "before": _label(base), "now": _label(run)})
    differs("System prompt", base.get("prompt"), run.get("prompt"))
    differs("Rules", base.get("rules"), run.get("rules"))
    differs("Grader", judge_model(base), judge_model(run))
    differs("Pass marks", pass_marks(base), pass_marks(run))
    return rows


def _caveats(run: Mapping[str, Any], base: Mapping[str, Any], stem: str) -> list[str]:
    """Why this test's two results aren't quite like for like."""
    found = []
    was = (base.get("case_versions") or {}).get(stem)
    now = (run.get("case_versions") or {}).get(stem)
    if was and now and was != now:
        found.append("the test was edited")
    if run.get("rules") != base.get("rules"):
        found.append("the rules changed")
    if judge_model(run) != judge_model(base):
        found.append("the grader changed")
    entries = [r["cases"][stem] for r in (run, base) if stem in r["cases"]]
    if any(WhyUnjudged.GRADER_UNRELIABLE.value in unjudged_reasons(e) for e in entries):
        found.append("grader unreliable here")
    return found


def _counts(entry: Mapping[str, Any]) -> dict[str, Any]:
    said = verdict_of(entry)
    return {"verdict": said.kind.value, "passed": said.passed, "judged": said.judged}


def _test(run: Mapping[str, Any], base: Mapping[str, Any], stem: str) -> dict[str, Any]:
    now_entry, was_entry = run["cases"].get(stem), base["cases"].get(stem)
    entry = now_entry or was_entry
    row: dict[str, Any] = {
        "case": stem,
        "name": entry.get("name") or stem,
        "now": _counts(now_entry) if now_entry else None,
        "before": _counts(was_entry) if was_entry else None,
        "chance": False,
        "caveats": [],
    }
    if now_entry is None or was_entry is None:
        return {**row, "change": Change.ONLY_ONE.value}
    now, before = verdict_of(now_entry), verdict_of(was_entry)
    change = change_of(before, now)
    moved = change in (Change.BETTER, Change.WORSE)
    return {
        **row,
        "change": change.value,
        "chance": moved and could_be_chance(before, now),
        "caveats": _caveats(run, base, stem),
    }


def _plural(n: int) -> str:
    return f"{n} test" if n == 1 else f"{n} tests"


def _headline(worse: list[dict[str, Any]], better: list[dict[str, Any]], tests: int) -> str:
    """The answer, in words. "Looks" when every move could be an agent varying."""
    moved = [*worse, *better]
    looks = bool(moved) and all(t["chance"] for t in moved)
    if worse and better:
        said = f"{_plural(len(worse))} got worse and {_plural(len(better))} got better"
    elif worse:
        said = f"{_plural(len(worse))} got worse"
    elif better:
        said = f"{_plural(len(better))} got better"
    else:
        return "No change" if tests else "Nothing to compare"
    return f"It looks like {said}" if looks else said[0].upper() + said[1:]


def comparison(run: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """*run* against *base*."""
    stems = list(dict.fromkeys([*run["cases"], *base["cases"]]))
    tests = [_test(run, base, stem) for stem in stems]
    worse = [t for t in tests if t["change"] == Change.WORSE.value]
    better = [t for t in tests if t["change"] == Change.BETTER.value]
    judged = [t for t in tests if t["change"] != Change.UNKNOWN.value]
    moved = [*worse, *better]
    answer = (
        "worse" if worse and not better else "mixed" if worse else "better" if better else "same"
    )
    a, b = run.get("totals") or {}, base.get("totals") or {}
    return {
        "run": run["run"],
        "base": base["run"],
        "answer": answer,
        "headline": _headline(worse, better, len(judged)),
        "could_be_chance": bool(moved) and all(t["chance"] for t in moved),
        "changed": _changed(run, base),
        # When what played says otherwise than the commits (ADR 0011), in a line.
        "code": code_said(run, base)[1],
        "versions": versions(run, base),
        "tests": sorted(tests, key=lambda t: _ORDER.get(t["change"], len(_ORDER))),
        "seconds": {"before": b.get("median_seconds"), "now": a.get("median_seconds")},
        "tokens": {"before": b.get("tokens"), "now": a.get("tokens")},
    }


_ORDER = {
    Change.WORSE.value: 0,
    Change.BETTER.value: 1,
    Change.UNKNOWN.value: 2,
    Change.ONLY_ONE.value: 3,
}
