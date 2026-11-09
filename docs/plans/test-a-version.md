# Plan: test a version of the code, before its pull request

Decided in [ADR 0011](../adr/0011-a-run-plays-a-named-version.md) (Proposed). Today a run plays whatever is on this computer and records the checkout's last commit, which may not be what played. This plan lets a person pick the version a run plays: a branch, local or only on GitHub, a tag, a commit, or what's on disk now. Evalution checks it out and builds it on its own, without touching their checkout, and every run says exactly what it played.

## What the person gets

- **The Run dialog names the code:** "Code: what's on disk now: `page-testing` @ e535c4d, with uncommitted changes ⌄". The picker lists local branches, GitHub branches (fetched when opened), tags, and "a commit…". Later: open pull requests.
- **Picking `feature-x` says what will happen first:** "Checks out feature-x @ a1b2c3d into evalution's folder and builds it, about 4 min, once. Your checkout isn't touched." A version built before says "Built already".
- **Every run shows its version:** a chip in the runs list ("feature-x @ a1b2c3d"), in the run's header, and in a comparison ("feature-x vs main").
- **"Compare with main"** on a run of a branch: opens the latest run of the same tests on `main`, or offers to run them there (with its cost).
- **Honest about the disk:** when what's on disk can't be vouched for, the run says so: "The images are older than this checkout's last commit: built from an earlier version" with **Build what's on disk** beside it.
- **In a terminal:** `evalution run --ref feature-x`, `evalution run --ref origin/feature-x`, `evalution run --ref a1b2c3d`. `evalution versions` lists what's checked out and built, with sizes; `evalution versions --remove`.

## What's true today (checked in code)

| | Where | What it does |
|---|---|---|
| Compose copy | `isolation/launch.py` `ComposeCopy.start` | `up -d --wait --no-build --pull never`, with an override file evalution writes (ports, volumes, env). Missing images are refused: "images not on this computer (evalution never builds or pulls them)" |
| Process copy | `isolation/isolate.py` `copy_for`, `launch.py` `ProcessCopy` | Runs the plan's command with `cwd` from the plan: the person's own folder |
| What a run records | `runs/runner.py` (`commit`, `code`), `code.py` `Code.state` | The code folder's `HEAD` and a hash of its watched files' hashes |
| What a comparison says | `results/compare.py` | "Code" differs when the two commits differ |
| Reset plan's evidence | `data/isolation/<target>/versions/*.json` | File:line claims, e.g. `deployment/compose.yaml:121` |
| relay-lite's build | its `deployment/build.sh`, `compose.yaml` | Builds `relay/engine` and `relay/host` images under its own names |

## Decisions

1. **A version is a commit, reached by a ref or "disk".** `Version(ref, commit, source: disk | local | remote | tag | commit | pr, dirty: bool)`. "Disk" is the default and keeps today's behaviour.
2. **Worktrees in evalution's folder, never the person's checkout.** `git -C <repo> fetch <remote> <branch>:refs/evalution/<target>/<branch>` for a remote branch; `git -C <repo> worktree add --detach <data>/versions/<target>/<commit> <commit>`. The repository is the target's code folder (`Code.root`'s top level). Nothing that writes `refs/heads`, the index or the working tree. A repo's hooks and settings don't run (`-c core.hooksPath=/dev/null`, `core.fsmonitor=false`, as `Code._git` does today).
3. **Built into evalution's names.** For compose, the recipe sets each built service's `image:` to `evalution/<target>-<service>:<commit12>` in a build override and runs `docker compose -f <worktree files> -f <build override> build`. The copy's start override then names those images. `--pull never` stays for starting a copy; a build may pull base images.
4. **The recipe is found and proven** by the agent, as isolation plans are: read the code, propose, build the disk version into evalution's names, start a copy from them, probe it. Kept as `data/builds/<target>/versions/<n>.json`, undoable. Evidence is file:line, so a version that changes those files makes the recipe stale for that version.
5. **The disk version gets a fingerprint, not a build.** For compose, the image ids the copy starts from and their creation time; for a process, the working tree's hash. The run warns when an image is older than `HEAD`'s commit time, or the working tree changed after the images were built. **Build what's on disk** builds it into evalution's names, tagged `disk-<code hash>`.
6. **Limits:** a size per target (default 25 GB; phase 0 measured 19.5 GB for a first relay-lite version and 9.4 GB for each nearby one), oldest unused version first, worktrees with them; a build is refused with less than 5 GB free on the computer's own disk, with what to remove. Sizes are shown, and removing a version trims the Docker VM's disk where it can.
7. **The reset plan and the page map are re-checked per version** by their evidence files: unchanged, they hold; changed, the run rehearses on that version's copy first and says why.

## Decided while building (2026-11-09)

Phases 1 to 6 are built (`13abb78`, `62fd69f`, `0982031`, and `evalution demo versions`). Where they differ from the decisions above:

- **Fetching writes only evalution's refs, by an explicit refspec and an empty `--refmap`.** `git fetch <remote> refs/heads/<b>:refs/evalution/<target>/<remote>/<b>` alone would still update `refs/remotes/<remote>/<b>` through the remote's configured refspec; `--refmap=` turns that off. No tags follow, `FETCH_HEAD` isn't written, no maintenance runs after. The picker lists a remote's branches live with `ls-remote`, so nothing is fetched until a branch is run. Every git call runs with hooks and fsmonitor off and no password prompt; tests prove the person's refs, index, files, stash and `HEAD` byte-identical.
- **Stale is decided by file hashes, not by a diff.** The build recipe, the reset plan and the page map each keep a hash of every file their evidence cites, from when they were proposed or proven. A recipe whose files changed on disk is stale; a version whose copies of the plan's or the map's files differ from those hashes gets them checked on its own copy. Comparing hashes needs no git and works on a worktree, an uncommitted change or a folder that isn't a checkout.
- **Proving a recipe is a build and an HTTP check, no message.** It builds what's on disk into evalution's names (tag `disk-<hash of the watched files>`), starts a copy from those images, checks the copy runs the very images just built (by id) and that its address answers at all, and takes it down. Sending a message would prove the app, which isolation already does. A process recipe's setup is never run in the person's folder, so proving one only starts the copy.
- **The reset plan and the page map are checked on the version's copy, and saved nowhere.** When the version's files differ, the plan is rehearsed on that version's copy before the first try, the map checked there by the runner; the run says so in its version's notes ("rehearsed again on this version's copy before the first try, and it held"). Neither is saved: they stay as proven on what's on disk. A plan that doesn't hold on the version ends the run before any try, with how to prove it there.
- **A build is reused by commit, while it is still there.** The recipe store keeps what was built per tag (the commit's first twelve characters, `built.json`); a run reuses it when every image it names is still on this computer (compose), or the worktree it was set up in already existed (process). Otherwise it builds again. What a build made counts toward the target's 25 GB with its worktree, and goes with it.
- **The base-branch default takes a later run too.** A run of a branch other than the base branch compares by default with the latest earlier run of the same tests on the base branch; when there is none, with a later one, so running `main` after seeing "No run of these tests on main yet" fills the comparison in. Only runs that played anything, in the same environment, count.
- **A version run's commit is the version's.** The run's top-level `commit` (what the comparison's Code row and older views read) is the commit it played, not the checkout's. Found by the demo: before, a branch's run said "feature-x @ <main's commit>".
- **The env file stays the person's, even inside the repository.** A copy's `env_file` is read from the person's checkout, as phase 0 decided for relay-lite's git-ignored `.env`. An env file the repository tracks, and that differs on the version, is not mapped into the worktree: the version's copy reads the person's. Known gap.
- **The writer proposes, the page proves.** The rule's recipe is the default; the test writer explains it and, asked, adjusts it as data (kind, variables, services, profiles, setup), each change checked against the app's files and saved as a new `proposed` version. It never builds: proving is one press on the *How a version is built* card, with the cost said first, not the two approvals in the writer that phase 3 planned.
- **Phase 5 is a process app, not a Dockerfile.** `evalution demo versions` makes a repository whose one file runs the made-up app (`assistant.py`, `made_up.remembers` set per branch), with a `process` recipe that sets up nothing. It needs no Docker, so it runs anywhere in about a minute; the compose path is tested with fakes, and waits for the live check on relay-lite.

## Where it stands

| Part | Where | State |
|---|---|---|
| What a run played (phase 1) | `core/version.py`, `runs/runner.py`, `results/compare.py` | Built. Runs keep a `version` block; old runs read "not recorded" |
| Versions and worktrees (phase 2) | `project/versions.py`, `evalution versions`, `/v1/targets/{t}/versions` | Built |
| Build recipes (phase 3) | `isolation/recipe.py`, `isolation/build.py`, `project/building.py`, `writer/building.py`, the *How a version is built* card | Built |
| Running a version (phase 4) | `runs/versioned.py`, the Run dialog's picker, `evalution run --ref` | Built |
| The made-up app's versions (phase 5) | `demo/versions.py`, `evalution demo versions` | Built: `feature-x vs main: 1 test got worse` in about 70 s, free |
| Comparing across versions (phase 6) | `results/compare.py`, the comparison card, the runs list's filter | Built |
| **Build what's on disk** (decision 5) | | Not built: an unverified disk run says why, with no button yet |
| Live check on relay-lite | | Not done |
| Pull requests, CI | | Not started |

## Phases

### Phase 0: spike on relay-lite (throwaway)

Answer before building anything:

1. Can `docker compose build` with an override that renames `image:` build relay-lite's engine and host? Or does `deployment/build.sh` do something compose can't (build args, a web bundle built first, a multi-stage order)?
2. relay-lite's engine starts each person's host container from an image name in its settings (`relay/host`). Can the copy be pointed at `evalution/relay-lite-host:<commit>` through env alone? If not, a version's host image needs a different route, and that decides the recipe's shape.
3. How long does a cold build of a branch take here, and how much disk does each version take?
4. Does `git worktree add` from relay-lite's repo give a folder its build accepts as is (no submodules, no LFS, no files expected outside the repo)?

Write what was found into this plan, as page testing's phase 0 did.

#### What phase 0 found (2026-11-08, on `~/pttx/relay-lite`, nothing built)

| Question | Answer |
|---|---|
| 1. Build into evalution's names | **Yes, with two variables, no override.** `deployment/compose.yaml` names both images `${IMAGE_REGISTRY:-relay}/<engine\|host>:${IMAGE_TAG:-local}`, and its own `deployment/build.sh` takes the same two. `docker compose config` on a worktree with `IMAGE_REGISTRY=evalution/relay-lite IMAGE_TAG=e535c4d89abb` resolves the engine to `evalution/relay-lite/engine:e535c4d89abb`, with the build context in the worktree. The host image is a build-only service under the `build` profile, so the recipe is `docker compose -f <worktree>/deployment/compose.yaml --profile build build engine host` with those two variables set. The person's `.env` sets neither, so nothing they built is renamed. |
| 2. Point the copy's host containers at the version | **Yes, by the same two variables.** The engine reads `ATRIUM_HOST_IMAGE: ${IMAGE_REGISTRY:-relay}/host:${IMAGE_TAG:-local}` from compose, which resolved to `evalution/relay-lite/host:e535c4d89abb`. Setting both in the copy's environment (`ComposeCopy`'s `env`, which wins over the `--env-file`) starts the version's engine, and the engine starts the version's host per person. No change to relay-lite. |
| 3. Build time and disk | **Cold: 233 s and 19.5 GB. Each nearby version after: 158 s and 9.4 GB.** Both built from detached worktrees into `evalution/relay-lite/{engine,host}:<commit12>`, with `docker compose ... --profile build build engine host`. Cold (`e535c4d`, empty build cache): images of 2.88 GB and 3.98 GB, plus about 11 GB of build cache: the VM used 19.5 GB more. Warm (`eccdf5a`, three commits back, changing engine and web code): base stages cached, but both Dockerfiles copy the source before installing its dependencies (`COPY engine/atrium/` then the venv), so any engine change reinstalls them: 9.4 GB more. Afterwards the four images and the build cache were removed and the disk trimmed: back to 7 images, 36 GB free. |
| 4. A worktree builds as is | **Yes.** relay-lite is itself a worktree of `~/pttx/relay-monorepo`; `git worktree add --detach` from it took 0.8 s and 47 MB (3,100 files, no submodules, no LFS). Every file the two Dockerfiles copy is tracked and present; the web bundle and the agents' bundles are built inside the image. The person's status, branches and stash were byte-identical before and after, and the worktree was removed. `.env` (secrets, git-ignored) isn't in a worktree: a version's copy keeps reading the person's `.env`, as today. |

What this changes in the plan:

- **The recipe for an app like this is two variables**, found by reading the compose file (`image:` interpolated) or its build script (`IMAGE_REGISTRY`/`IMAGE_TAG`). Phase 3's agent looks for that first; a build override that rewrites `image:` is the fallback for apps whose names are fixed.
- **Names have a slash:** `evalution/relay-lite/engine:<commit12>`, as the app's own `${IMAGE_REGISTRY}/<name>` makes them. Decision 3's `evalution/<target>-<service>` is one shape the override can make, not the only one.
- **The env file stays the person's.** A worktree has no `.env`; the copy reads it from their checkout, as `launch.env_file` does now. A version that adds a setting its `.env` lacks fails to start, and says which.
- **Disk is the cost, not time.** Three versions of relay-lite would take about 38 GB (19.5 + 2 × 9.4). Decision 6 changes: the limit is a size per target (default 25 GB, about two versions here), not a count; the oldest version not used by a run in progress goes first. The Run dialog says the cost before a first build ("about 4 min and 20 GB, once") and each later one ("about 3 min and 9 GB").
- **A Docker VM's disk doesn't give space back by itself.** On Colima, removing images frees room inside the VM, but the disk file on the Mac stays as large until `fstrim` runs in the VM: it returned 38.8 GB before the spike and 32.4 GB after. Removing a version should trim when the runtime allows it (Colima, Docker Desktop with discard), and the free-space check reads the Mac's disk, not the VM's.
- **The app's Dockerfile decides the cost of each version.** relay-lite installs dependencies after copying its source, so a one-line change costs 9 GB. Installing from `pyproject.toml` before copying the source would make a version cost about its source; that's the app's change to make, and the recipe card can say so with the numbers.
- **`relay-train` is a running stack** (up 7 days, its own engine, host and Postgres on `relay/*:train`). The spike left it alone. Any clean-up evalution does touches only images and caches it made.

### Phase 1: say what played (no new behaviour)

Built (2026-11-08, `13abb78`).


Worth doing even if nothing else ships: today's runs can be wrong about their code.

1. `Version` in `core/` (pure), and a run's `version` block: `ref: "disk"`, `commit`, `dirty` (from `Code.clean()`), `played` (compose: service → image id and created time; process: the working tree hash), `verified` (false when an image is older than `HEAD` or the tree changed after the build).
2. `results/compare.py` and the comparison card read `version`; old runs upcast to `{ref: "disk", commit, verified: null}` and say "not recorded".
3. Run header, runs list chip, Run dialog line: "Code: what's on disk now: page-testing @ e535c4d, with uncommitted changes". The warning when unverified.

Tests: a run on images built before `HEAD` is marked unverified and says why; two runs on the same images with different checkouts compare as the same code played.

### Phase 2: versions and worktrees

Built (2026-11-08, `62fd69f`).


1. `project/versions.py`: list refs (local branches, `refs/remotes/*` after a fetch, tags), resolve a ref to a commit, fetch a remote branch into `refs/evalution/...`, add and remove worktrees under `<data>/versions/<target>/<commit>`, sizes, limits. Every git call through one runner with hooks and fsmonitor off and a timeout.
2. API: `GET /v1/targets/{t}/versions` (refs, with what's checked out and built), `POST /v1/targets/{t}/versions/fetch`, `DELETE /v1/targets/{t}/versions/{commit}`.
3. CLI: `evalution versions [--remove COMMIT]`.

Tests on a throwaway repo: a remote-only branch is fetched without creating or moving any `refs/heads/*`; the person's working tree, index and stash are byte-identical before and after; a worktree is removed with its version; a branch that moved is a new commit, the old worktree kept until the limit.

### Phase 3: the build recipe, found and proven

Built (2026-11-08, `62fd69f`).


1. `BuildRecipe` (data): `kind: compose | command`, the services built and their image names, the files read as evidence, `setup` for a process copy (e.g. `uv sync`, `npm ci`), timings.
2. Agent tools in the writer (as isolation's): read the build files, propose a recipe, build the disk version into evalution's names on approval, start a copy from it and probe. Two yeses, as isolation: approve the proposal (it spends minutes of build), approve the proven recipe.
3. Test environment page: **How a version is built** card, beside How tests reach: the recipe, its evidence, last build time and size, undo.

Tests: on the made-up app (phase 5 gives it a build), a recipe found, proven, and stale when its Dockerfile changes.

### Phase 4: run a version

Built (2026-11-09, `0982031`).


1. `runs/isolated.py`: before the copy starts, when the run's version isn't disk: worktree (phase 2), build if not built (phase 3), then the copy's override names the version's images; a process copy's `cwd` is the worktree.
2. Re-check the reset plan and page map against the version's files; rehearse first when they differ.
3. Run dialog picker and its "what will happen" line; `evalution run --ref`; the estimate counts the build ("about 4 min, once").
4. A failed build ends the run before any try, with the build's last lines and one next step (ADR-style: unjudged, never a fail of the app).

Tests: a run of a branch plays that branch's reply, not the disk's; a run of a branch never changes the person's checkout; a failed build is not a result.

### Phase 5: the made-up app gets versions

The made-up app (`evalution.demo.app`) gets a tiny git repo in the demo folder with two branches whose replies differ, and a Dockerfile, so `evalution demo versions` shows "feature vs main: 1 got worse" in about a minute, spending nothing. It also gives the acceptance tests a real build.

Built (2026-11-09) as a process app, with no Dockerfile: `evalution demo versions [--no-open]` (`server/evalution/demo/versions.py`). In a new temporary folder it makes a git repository (hooks and the person's git config off) whose one file, `assistant.py`, runs the made-up app; `main` remembers what it's told, `feature-x` changes one line (`made_up.remembers = False`). The target `demo-assistant` has that repository as its code, a reset plan whose copy starts in it with `python assistant.py PORT`, and a `process` recipe with nothing to set up, both saved as proven. Three tests decided by code, four tries each (the fewest that tell 4/4 from 0/4 apart from chance): *Answers a sum*, *Says it noted a message*, *Remembers the dog's name*. It plays them on `main`, then on `feature-x`, through the real runner in an `evalution serve` of that folder: each version checked out as a worktree in the demo's data folder and its copy started there. It prints `feature-x vs main: 1 test got worse: Remembers the dog's name`, checks the repository's own checkout is unchanged, and opens the branch's run, its comparison first. About 70 s here (two runs of about 35 s). Ctrl-C (or a plain `kill`) stops it and deletes the folder, the worktrees with it. `tests/acceptance/test_demo_versions.py` plays it to the end. A real build for the acceptance tests waits for a Dockerfile; until then the compose path is covered by fakes.

### Phase 6: compare across versions

Built (2026-11-09, `0982031`).


1. The comparison card's default base: the latest run of the same set on the base branch (`origin/HEAD`, else `main`, else `master`), when the run is of another branch; else as today.
2. **Compare with main** on a branch's run: open that run, or offer to run the same tests on `main` with its estimate.
3. The runs list filters by version.

### After this

- **Pull requests** through `gh`: list open PRs, fetch `refs/pull/<n>/head`, show the PR's title and base, compare with its base branch.
- **CI:** `evalution run` in CI records the CI checkout as the version and builds it with the recipe; a PR's job compares with its base branch's last run.
- **Hosted:** none (no copies).

## Testing

- Every git operation tested on a throwaway repo, asserting the person's refs, index, working tree and stash are unchanged.
- Build tests use the made-up app's tiny Dockerfile; Docker-dependent tests skip without Docker, as isolation's do.
- One live check on relay-lite after phase 4: a branch with a known reply change, run against `main`, with the comparison saying which test moved. Its target config restored after, as page testing's live runs were.

## Order and size

Phase 0 first (a day), because relay-lite's host image decides the recipe's shape. Phase 1 next and on its own: it fixes a wrong record today. Phases 2 to 4 are the feature; 5 makes it shown in a minute and tested; 6 is the payoff for "before the PR".

## Risks

| Risk | What we do |
|---|---|
| Builds fill the disk (this branch already filled it once) | Limit per target, refuse under 5 GB free, sizes shown, one button to remove |
| An app's build script only writes its own image names | The agent says so and what to change; evalution never retags over the person's images |
| relay-lite starts host containers by image name from its settings | Phase 0 decides; if env can't point it at evalution's names, versions of the host need the app to take the name as a setting |
| Submodules, LFS, files outside the repo | Fetched in the worktree when present; the first version says what it couldn't get |
| A long first build feels like a hang | Live steps with the build's last lines, as installs and isolation show them; the estimate says it before Run |
| The reset plan or page map differs on a branch | Re-checked by evidence files, rehearsed on that version first |
| Fetching a remote touches the person's repo | Only `refs/evalution/*` and worktrees evalution made; `git worktree prune` only of its own |

## Open questions

1. Should picking a branch also offer "and run `main` too", to compare in one go, at twice the cost?
2. ~~Keep 3 built versions per target, or size-based?~~ Size-based, from phase 0's numbers (decision 6).
3. Pull requests in the first release, or after?
