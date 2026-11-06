"""How many tries a test gets when the run didn't fix the count: until its tries say something.

Pure: it reads the test's verdict so far, and the one it is compared with, and says whether to play
one more. The runner plays a run's own ``tries`` exactly, and only asks this when there is none.

* **The least.** Every test plays its own ``tries`` (three by default) before anything is decided.
* **Agreement stops it.** Judged tries that all passed, or all failed, end it there: more would
  most likely say the same. So do tries none of which could be judged: the rest would only read
  nothing again.
* **Disagreement extends it.** A test that both passed and failed plays on until it has shown each
  outcome its least number of times (inverse sampling), so its pass rate rests on that many of
  each, not on one odd try. A rare failure plays to the most.
* **A move keeps it going.** Against the run it is compared with, a test that moved plays on until
  Fisher's test (:func:`evalution.core.rules.could_be_chance`) calls the move real, or until even
  the tries left going all the move's way could not: then it is chance, and more won't change that.
* **Never past** :data:`MOST_TRIES`.
"""

from __future__ import annotations

from evalution.core.case import MOST_TRIES
from evalution.core.judgement import Change, Verdict
from evalution.core.rules import change_of, could_be_chance


def one_more(now: Verdict, least: int, base: Verdict | None = None) -> bool:
    """Whether a test with *now* so far plays another try: it plays at least *least*, and *base*
    is its result in the run this one is compared with, if any."""
    played = now.judged + now.unjudged
    if played >= MOST_TRIES:
        return False
    if played < least:
        return True
    if base is not None and _move_undecided(base, now):
        return True
    # The rarer outcome: none means the judged tries agree.
    rarer = min(now.passed, now.judged - now.passed)
    return 0 < rarer < least


def _move_undecided(base: Verdict, now: Verdict) -> bool:
    """A move from *base* that Fisher's test can't call real yet, but the tries left still could."""
    change = change_of(base, now)
    if change not in (Change.BETTER, Change.WORSE) or not could_be_chance(base, now):
        return False
    left = MOST_TRIES - (now.judged + now.unjudged)
    furthest = now.model_copy(
        update={
            "passed": now.passed + (left if change is Change.BETTER else 0),
            "judged": now.judged + left,
        }
    )
    return not could_be_chance(base, furthest)
