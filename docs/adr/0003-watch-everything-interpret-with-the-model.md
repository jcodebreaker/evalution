# 0003: Watch everything, interpret with the model, run nothing by itself

> **Status:** Accepted 2026-11-01 by the owner. Amended by [ADR 0005](0005-keep-what-it-knows-current.md): the interpretation follows the app's updates setting, and in manual, the default, only runs when asked.

Change detection watched a list of globs per project: README, docs and one route file for most. Most logic changes went unseen, and every new layout needed another list. A change that did not make a test stale was not shown at all.

## Decision

- **Watch all source, in every project.** Files come from git or a walk, minus tests, lockfiles, build output, tool and credential folders, binaries, large files and secrets. One optional `code.ignore` replaces `include`. An adapter may be specific to an app; a folder layout may not.
- **Interpret with the light model.** Hashes say what changed; only a model can say what that means for the tests. One call per distinct change, cached by fingerprint, with secrets files never sent. No model or a failed call falls back to the changed files and all the tests, so the page never depends on it.
- **Compare with what was last tested.** The question is which tests have not seen this code, so the base is the code when a run started, not the profile. Running the affected tests clears the card.
- **Nothing runs by itself.** A run costs money, so the owner presses the button, behind the usual estimate and token. The test writer proposes tests for an uncovered area; the owner approves.

## Consequences

- Viewing Home can spend one light model call per distinct code state, at most every five minutes.
- More of a project's source reaches the configured model, in diffs and through the agent. Secrets are filtered by file name, so a secret written into a source file is not caught.
- A project over the 2,000 file cap is watched in path order up to the cap.
