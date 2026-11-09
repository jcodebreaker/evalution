# 0011: A run plays a named version of the code

> **Status:** Proposed 2026-11-08. Plan: [test a version](../plans/test-a-version.md). Needs [0009](0009-isolation-is-found-and-proven.md) for its copies.

A developer tests a change before opening its pull request. The change lives on a branch, and that branch may not be the one checked out: they may be on `main` with work in progress, or the branch may exist only on GitHub, pushed by someone else. Evalution can't test it today, and it can't say what it did test:

- **A copy runs whatever images are on the computer.** A compose copy starts with `--no-build --pull never` (`isolation/launch.py`, `ComposeCopy.start`), so it plays whatever was last built here, from whichever branch, whenever. A process copy starts in the person's own folder, so it plays the working tree as it is at that moment.
- **The run records the checkout, not what played.** `code.Code.state()` records the folder's `HEAD` and its files' hashes. With images built last week from `main` and `feature-x` checked out now, the run says `feature-x` and played `main`. A comparison then reads "Code changed" between two runs that played the same thing, or "same code" between two that didn't.
- **There is no way to ask for a version.** Testing a branch means switching the checkout, rebuilding by hand, and remembering to switch back, which disturbs the person's work and is easy to get wrong.

## Decision

- **Every run plays a version, and names it.** A version is a commit of the target's repository, reached by a ref (a local branch, a remote branch, a tag, a commit, later a pull request), or **what's on disk now**: the checkout as it stands, uncommitted changes included. The run records the ref as asked, the commit it resolved to, whether uncommitted changes were in it, and what actually played (image ids for compose, the folder for a process). Old runs read as "what was on disk", their commit as recorded, marked unverified.
- **What's on disk now stays the default.** Nothing changes for a person who never picks a version, except that the run now says what it played, and warns when it can't vouch for it: images older than the checkout's last commit, or files changed since they were built.
- **Evalution never touches the person's checkout.** A version other than the disk is checked out by evalution into its own data folder with `git worktree add --detach`, from the person's own repository. A remote branch is fetched into evalution's own refs (`refs/evalution/...`), never into the person's branches. No switch, no stash, no reset, no change to their working tree, index or branches.
- **Evalution builds a version into its own names.** Building is new: evalution has never built or pulled an app's images. It builds only versions it checked out, and only into images named for it and the commit (`evalution/<target>-<service>:<commit>`), so the person's own images (`relay/engine:latest`) are never replaced. It never pushes. A built version is reused by commit. Base images may be pulled during a build, as a build does; the app's own images are never pulled.
- **How to build is found and proven, like how to reset.** The agent reads the code (compose `build:` sections, Dockerfiles, build scripts, `Makefile`, `package.json`) and proposes a build recipe: `docker compose build` with the image names moved to evalution's, or the app's own script when it takes the image name or tag as a setting. It proves the recipe by building the disk version into evalution's names and starting a copy from them. The recipe is versioned and undoable (ADR 0005, as isolation plans are). A script that only writes the person's own image names can't be used as is, and the agent says so with what to change.
- **The reset plan and the page map are checked against the version.** Both rest on files (ADR 0009's claims, ADR 0010's evidence). When a version changes those files, its run says the plan or the map may not hold there, and rehearses on that version's copy before the first try, as a stale plan does today.
- **Compare like with like, across versions on purpose.** A comparison of two runs says each one's version. The comparison card defaults to the latest run of the same tests on the base branch (`main`, as the repository names it), when there is one: "feature-x vs main". It never compares runs of different sets (fixed in `d5ebb0c`).
- **Local and CI only.** Versions need copies, and hosted evalution has none. In CI the checkout CI made is the version: no ref to pick, and the recipe builds it.

## Consequences

- Runs gain a `version` block (`ref`, `commit`, `dirty`, `played`, `verified`), and `results/compare.py` compares it in place of the bare commit. `RULES_VERSION` doesn't change: this is what played, not how it was judged.
- A new store under the data folder for worktrees and built versions, with a size and a limit (keep the last few per target), shown on the Test environment page with a button to remove them. Builds and worktrees take real disk: this branch's work already filled the disk once.
- Isolation gains a build step before a copy starts, timed and shown in the run's estimate ("builds feature-x first, about 4 min, once").
- A version's first run is slow (fetch, worktree, build). A second run of the same commit isn't.
- Repositories with submodules or Git LFS need those fetched in the worktree; the first version says what it couldn't fetch.
- Private remotes are fetched with the person's own git credentials, as their own `git fetch` would; evalution stores none.
- A process copy runs from the worktree, so its setup (installing dependencies) becomes part of the recipe.
