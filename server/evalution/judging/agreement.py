"""How often the grader agrees with a person, rubric line by rubric line (docs/judging.md, "When a
person disagrees"). A person's answer on one line of one try is a labelled example
(:mod:`evalution.judging.labels`): whether the line really holds there. Against it stands what the
grader says of that line now, which a regrade moves. Pure: no model, no files.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from evalution.core.judgement import CheckResult, Outcome, WhyUnjudged

#: Labelled tries a line needs before its agreement can call the grader unreliable on it. One or
#: two disagreements are a person's call on a close line, not yet a pattern.
MIN_LABELS = 3
#: The share of a line's labelled tries the grader must match the person on. Below two in three
#: it is wrong on that line at least one time in three: more often than a pass resting on it alone
#: can be taken as earned.
MIN_AGREEMENT = Fraction(2, 3)


@dataclass(frozen=True)
class Agreement:
    """Of the labelled tries of one line, how many the grader matched the person on."""

    agreed: int = 0
    labelled: int = 0

    @property
    def unreliable(self) -> bool:
        """Enough labels, and too few agreed: the grader's passes on this line aren't earned."""
        return self.labelled >= MIN_LABELS and Fraction(self.agreed, self.labelled) < MIN_AGREEMENT

    def view(self) -> dict[str, Any]:
        return {"agreed": self.agreed, "labelled": self.labelled, "unreliable": self.unreliable}


def grader_said(check: CheckResult | None) -> bool | None:
    """Whether the grader held a rubric line (``None``: it couldn't say). A pass held back as
    unreliable is still the grader's pass: that is what a person is asked about."""
    if check is None:
        return None
    if check.outcome is Outcome.PASSED or check.unjudged is WhyUnjudged.GRADER_UNRELIABLE:
        return True
    return False if check.outcome is Outcome.FAILED else None


def agreement(examples: Iterable[Mapping[str, Any]]) -> Agreement:
    """*examples* of one line: ``holds``, the person's answer, against ``graded``, the grader's."""
    found = list(examples)
    return Agreement(sum(e["holds"] == e["graded"] for e in found), len(found))


def by_line(examples: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Agreement]:
    """The agreement of each line labelled, by ``(case, line key)``."""
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for example in examples:
        grouped[example["case"], example["line"]].append(example)
    return {where: agreement(found) for where, found in grouped.items()}


def unreliable_lines(examples: Iterable[Mapping[str, Any]]) -> dict[str, frozenset[str]]:
    """The line keys each test's grader is unreliable on, by test."""
    found: dict[str, set[str]] = defaultdict(set)
    for (case, line), said in by_line(examples).items():
        if said.unreliable:
            found[case].add(line)
    return {case: frozenset(lines) for case, lines in found.items()}
