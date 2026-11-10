# 0005: Keep what it knows current, on the owner's terms

> **Status:** Accepted 2026-11-01 by the owner.

"What it learned" went stale silently. Claims cited evidence, but nothing watched those links, and `impact.py` only said which tests a change affects. So the owner corrected a picture of the app that had moved on, and the test writer and the change card read it anyway.

## Decision

- **Link claims to files, and trace commits to claims.** Each claim and node links to the files its evidence names, with their hashes, in a file beside the understanding. A commit marks what cites a changed file (direct), or what imports one within two hops (indirect, and weaker). Import tracing is best-effort by language, cached by hash, and has no edges where it cannot read.
- **Only commits count.** Working-tree churn stays on the change card. Knowledge should describe what was committed, and an edit in progress should cost nothing.
- **Detection is free; spending is a per-app choice.** "Update what it knows" is *When I ask* (`manual`) or *Automatically* (`auto`), default `manual`, because `auto` spends without a click. Hooks, hashes, the graph and the marks run either way. `manual` shows how many claims may be out of date and one button with a rough cost. `auto` makes one light call per distinct committed change. Both share one refresh path. There is no spending limit.
- **Turning `auto` on spends nothing.** What is marked stays behind the button; the next commit is the first one read.
- **The owner's steers win.** A claim the owner fixed or marked is never rewritten by a refresh, only flagged. Every refresh is a version and can be undone.
- **Nothing is invented.** A rewrite must cite a real watched file, and an indirect claim is only rewritten if the model cites the changed file. It is never removed that way. A failed call changes nothing.
- **One record of what the code was.** The code as it was learned, as what it knows was last refreshed, and as it was last tested are three marks in one document per app (`state.json`, amended 2026-11-09). A successful refresh moves the refreshed mark and the learned mark for the files it covered: every watched file the commits it read changed, but those a claim still marked rests on. So the page stops saying "changed since learned" about what was just brought up to date.
- **Hooks are opt-in and chained.** Only the command or the toggle installs them. They add a marked block, leave everything else alone, never slow or fail git, and never touch tracked files.

## Amends ADR 0003

ADR 0003 has the change card read each change with the light model by itself, viewing Home included. That call now follows the same setting: in `manual` the card is its no-model fallback (the changed files and all the tests) with a way to ask for the reading. Without this, the setting would not stop surprise spending.

## Consequences

- New apps, and apps connected before this, stop getting the card's interpretation until the owner asks or chooses `auto`. The card shows all tests until then.
- `POST /v1/targets/{t}/changed` now answers 202 at once and runs the code part, instead of waiting and returning the drift. Trace drift is still checked before each run and daily.
- In `auto`, a commit can cost two light calls: this one and the change card's.
- A refresh clears "Changed since learned" for the files it covered (amended 2026-11-09; it used to leave the fingerprint alone, so the pill stayed after a successful update until Learn again). A changed file no claim rests on has nothing left to update, so it counts as covered. A file a claim still marked rests on stays changed. The trace part of the fingerprint and the change card's tested mark are not touched, so tests are still marked stale as before.
- Because the learned mark moved, Learn again after a refresh that covered every changed file says nothing changed; an area can still be learned on its own. Undo puts the claims back but not the mark: the links start again from HEAD, as before.
- A secret written into a source file can reach the model, as with any diff (ADR 0003). Files named as secrets are never sent.
- Files the agent read but no evidence cites do not link to claims, so a change there marks nothing.
