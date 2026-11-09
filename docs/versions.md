# Test a version before its pull request

## What it does

A run can play a version of your app's code other than what's on disk: a branch, a branch only on GitHub, a tag or a commit. Evalution checks that version out into its own folder, builds it into its own image names, starts its own copy of your app from it, plays the tests there, and compares the run with the latest run of the same tests on your base branch: "feature-x vs main: 1 test got worse". Your checkout isn't touched, so you can test a branch while you work on another, or test someone else's before reviewing it ([ADR 0011](adr/0011-a-run-plays-a-named-version.md)).

Every run says what it played: the branch and commit, and whether evalution can vouch for it. A run of what's on disk says so too, and warns when it can't vouch for it, for instance when the images your copy starts from are older than your last commit.

## Try the demo

```sh
uv run --project server evalution demo versions
```

In about a minute it makes a small git repository of a made-up assistant, with a branch `main` and a branch `feature-x` that changes one line so the assistant no longer remembers what it's told. It plays three tests on `main`, then on `feature-x`, each version checked out by evalution, and opens the comparison: `feature-x vs main: 1 test got worse: Remembers the dog's name`. It also checks that the repository's own checkout is still on `main`, untouched. It spends nothing: every check is decided by code and no model is called. Everything lives in a scratch folder of its own, never with your targets or runs, and Ctrl-C stops it and deletes that folder, the checked-out versions with it. `--no-open` prints the address instead of opening it.

## What you need first

Versions play only on evalution's own copy of your app, so the app needs:

1. **A proven isolation plan**: *Test environment → Find and prove isolation* (or `evalution isolate --target <name>`).
2. **A proven build recipe**: *Test environment → How a version is built → Propose*, then **Prove it**. Evalution reads your compose files (or lockfiles, for an app that runs as a process) and proposes how to build a version into its own names: through the variables your compose file already names images with (`${IMAGE_REGISTRY}/engine:${IMAGE_TAG}`), else with an override it writes in its own folder. Proving builds what's on disk that way, starts a copy from it and checks the copy answers. It sends no message.
3. **A code folder that's a git checkout**, set in the app's `code.path`.

Without these, picking a version says what's missing and the one place to fix it.

## Pick a version

- **On the page**: the Run dialog's **Code** line, "what's on disk now" by default. The picker lists your branches, your remote's branches (listed when you open it), tags, and **A commit…**. Picking one says what will happen first and what it costs, before you press Run: "Checks out feature-x @ a1b2c3d and builds it: about 4 min and up to 20 GB, once. Your checkout isn't touched." A version built before says "Built already".
- **In a terminal**: `evalution run --ref feature-x`, `--ref origin/feature-x` or `--ref a1b2c3d`. `evalution versions` lists the branches and tags, and the versions evalution has checked out, with their sizes.

On a run of a branch, the comparison card compares it with the latest run of the same tests on your base branch (`origin/HEAD`, else `main`, else `master`). When there is none yet, it says so and offers **Run them on main**; a run of `main` made after is picked up. The runs list filters by version.

## What it costs

A version is built once per commit and reused after. Measured on relay-lite, an app built as two Docker images (`docs/plans/test-a-version.md`, phase 0):

| | Time | Disk |
|---|---|---|
| First version (empty build cache) | about 4 min (233 s) | 19.5 GB |
| Each nearby version after | under 3 min (158 s) | 9.4 GB |
| A second run of a version already built | none | none |

Most of that disk is the app's own dependencies: relay-lite copies its source before installing them, so any change reinstalls them. An app whose Dockerfile installs dependencies first costs about its source per version. An app that runs as a process costs a checkout of its repository and its setup (`uv sync`, `npm ci`) per version.

Builds are refused with less than 5 GB free, and a target's versions are kept under 25 GB in all, the one used longest ago removed first.

## What's never touched

- **Your checkout**: no switch, stash or reset; your working tree, index, branches and `HEAD` stay as they are. A version is a detached `git worktree` in evalution's data folder (`data/versions/<app>/<commit>`).
- **Your branches and remotes**: a branch only on the remote is fetched into evalution's own refs (`refs/evalution/...`), never into `refs/heads` or `refs/remotes`.
- **Your images**: evalution builds only into names under `evalution/` and checks the names before building, so `relay/engine:latest` is never replaced. It never pushes, and never pulls your app's own images.
- **Your repository's hooks**: none run for evalution's git calls.
- **Your `.env`**: a version's copy reads yours, as a copy of what's on disk does, since it isn't in a checkout.

## Free the space

- *Test environment → How a version is built*: each built version with its size, and **Remove**.
- `evalution versions --remove <commit>` removes a version's checkout and what was built from it.

On Colima, removing a build also trims the virtual machine's disk, so the Mac gets the space back.

## Limits

- Local and CI only: a hosted evalution has no copies.
- A version that changes the files the reset plan or the page map rest on is checked on that version's copy before the first try (the plan rehearsed again, the map checked), and the run says so. Neither is saved: they stay as proven on what's on disk.
- An env file inside the repository is read from your checkout, not the version's.
- Not yet: pull requests by number.
