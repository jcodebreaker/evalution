# 0007: A result is a judgement of checks, and missing evidence is not a result

> **Status:** Accepted 2026-11-01 by the owner.

A try was a `passed` boolean, an error string and step statuses, and each reader made its own verdict from them. `case_row` and `_run_test` disagreed about one document. Absent evidence read as a result: a `context_lacks` passed on a model call with no captured text, a `never` passed on a read that stopped at the first span, a grader outage failed a try and read as a regression, and a judge unsure twice made a pass. The proof harness pinned twenty-two of these as strict xfails.

## Decision

One pipeline, arrows one way: `Case → play → Evidence → Checks → Judgement → Verdict → Reading → views`. The rules are pure functions in `core/rules.py`; `docs/judging.md` says them in words.

- **Three outcomes.** A check passes, fails, or could not be judged, with one of eight reasons. Any must failed fails the try; else any must unjudged leaves it unjudged. A real failure beats a missing grader.
- **Code decides what code can.** A model decides rubric lines and the reference score. Cause, fix and suspected fault are its reading, shown as that, and move no number.
- **Model-only passes are labelled and counted apart, not demoted.** The owner's call: a reply-only test is still useful, and pretending it was checked is what's wrong.
- **Evidence is kept.** Each try's in a 0600 sidecar, capped per item with a hash of what was left out, so a verdict can be regraded and a claim checked.
- **Judgements are appended.** Stored runs upcast on read, and a rules change marks old results rather than reading as improvement. Comparison needs the same test, rules, judge model and pass marks.
- **Approval binds a test.** A plan carries the whole test; saving anything else is refused.

## Where this went further than the plan

- **Turn coverage in trace collection.** A still span count isn't enough: one batch 60 s late looks settled at 6. A finished turn with no AI span of its own also keeps a read partial. A turn in which an app really calls no AI, or clocks more than a second apart on a short turn, reads as partial: a false "couldn't judge", never a false verdict.
- **A partial read decides on what it saw.** Seeing a forbidden call fails; not seeing it decides nothing. The plan said partial reads make trace-dependent checks unjudged; doing so for a call in plain sight would throw away a real failure.
- **`rules_from`, `cut` and `verified`** on the types, to say what the record needs to.

## What this doesn't do

- It doesn't measure how often a model is right. A model-only pass is labelled, not improved.
- A wrong reading with valid refs still gets through claim checking.
- A leak across tries is still not caught: a pass can rest on what an earlier try left behind (`test_11` stays a strict xfail). Catching it needs a read of state before the first turn, which adapters can't yet do without waiting on it.
- An adapter that reads the wrong place without knowing it still says `observed: true`. The seam only carries what the adapter can tell.
