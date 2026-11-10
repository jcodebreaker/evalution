# Next level: core logic and UI

A plan for an implementer agent. It says what changes, where, in what order, and how the work is split between sub-agents. The design it implements is `docs/mockups/evalution-next.html` (open it in a browser; every screen is there). The reasons behind each change are in this file, with the code they come from.

Three areas matter most and come first: **the agent's trajectory** (how tool calls are graded and shown), **the test environment** (the copy tests run on), and **learning** (what evalution knows about the app). The rest supports them.

## How to run this

You are the lead. Do not write the code yourself; split it.

1. Read this file, `docs/architecture.md` and `docs/judging.md`. Open the mock-up.
2. Start one sub-agent per work package below, each in its own git worktree, on the cheaper model (Opus). Give each: its package section from this file verbatim, the files it owns, and its done-check. Two agents never own the same file. Where two packages touch one file, the table says who goes first.
3. Backend packages A, B, C can run at the same time. The UI packages wait for the API notes each backend package publishes (a short section it adds to `docs/architecture.md`), then run at the same time as each other.
4. When a package reports done, check its diff and run its tests yourself before merging. A report is not proof.
5. Merge order: A, B, C, D, E, F, then the UI packages. Run the whole suite after each merge.

Ground rules for every sub-agent:

- Tests first where the behaviour is a rule (`server/tests/`, `uv run pytest`; web: `npm test` in `web/`).
- Anything that changes how a stored try reads must raise the rules version in `server/evalution/core/rules.py` and add a migration in `results/migrate.py`, as the file's own comment says.
- No vendor names outside `adapters/` (`tests/test_no_vendor_in_core.py`).
- Keep the product words in `docs/architecture.md` ("Words").
- Never delete or stop anything not named `eval-*`. That guard already exists on every destructive call; keep it.
- Update the doc that describes what you changed, in the same change.

| Package | Agent | Owns | Needs first |
|---|---|---|---|
| A Trajectory | backend-judging | `judging/steps.py`, `judging/checks.py` (steps check only), `core/trace.py`, `traces/view.py`, `review/divergence.py`, `runs/collect.py`, `docs/judging.md`, `docs/case-format.md` | nothing |
| B Test environment | backend-isolation | `isolation/*`, `runs/isolated.py`, `api/isolation.py`, `api/isolation_words.py`, `docs/adr/0009*`, `docs/plans/isolation.md` | nothing |
| C Learning | backend-learn | `writer/learn.py`, `writer/areas.py`, `project/*` except `versions.py` and `building.py`, `api/understanding.py`, `docs/adr/0005*`, `0012*` | nothing |
| D Runs | backend-runs | `runs/runner.py`, `review/findings.py`, `review/testplan.py`, `results/store.py`, `storage/files.py`, `api/runs.py`, `api/tests.py`, `writer/agent.py` (approve path only) | A, B merged |
| E Code and versions | backend-versions | `project/versions.py`, `runs/versioned.py`, `project/building.py`, `connect/discover.py`, `api/versions.py`, `cli.py` (`--ref`) | nothing |
| F Benchmark scoring | backend-bench | `benchmarks/*`, `judging/rubric.py` (benchmark prompt only), `web/src/features/benchmarks/*` | nothing |
| U1 Shell and assistant | ui-shell | `web/src/features/shell/*`, `writer/*`, `ui/*`, `index.css`, `lib/router.ts` | nothing |
| U2 Connect, setup, environment, learned | ui-app | `web/src/features/connect/*`, `environment/*`, `learned/*` | B, C API notes |
| U3 Tests, test, runs, home | ui-results | `web/src/features/tests/*`, `test/*`, `runs/*`, `home/*` | A, D API notes |

`web/src/lib/schema.ts` and `lib/api.ts` are shared: each UI agent adds its own types at the end of the file and the lead resolves the merge.

---

## A. Trajectory: how tool calls are graded

Today (`judging/steps.py`, `grade_tools`): expected calls are lined up with the calls made, the score is `matched / expected`, less 0.25 for each call to a `never` tool and, when extras are off, less 0.1 for each extra call. The steps hold when the score reaches the pass mark, 0.8 by default (`judging/checks.py:589`).

### A1. "Exact" means exact

- **Problem.** `order: "exact"` only turns extras off (`steps.py:229`). One or two extra calls still score 0.9 or 0.8 and hold. It also never checks that the calls came one after the other with nothing between.
- **Change.** When `order` is `exact`: the steps hold only if every expected call matched, no other call was made, and they came in that order. When `extras` is false: any extra call fails the steps. The score stays as a number for the page; it no longer decides these two cases.
- **Where.** `grade_tools` returns a third value, `strict_ok: bool | None`; `checks.py` uses it instead of `score >= marks["tools"]` when it is not `None`.
- **Done when.** New tests: exact with one extra call fails; exact with a reorder fails; `extras: false` with one extra fails; `in_order` with extras allowed is unchanged. Rules version raised; `migrate.py` marks old tries of such tests "Originally passed; rules changed" where the verdict flips.

### A2. Sub-agent calls are kept apart

- **Problem.** A call made inside a delegated agent counts as the main agent's (`steps.py`, `chosen_calls`; only the reason text says "in a delegated agent"). A helper's own tool use can satisfy an expected step or show up as an extra.
- **Change.** Each expected step may say `in: "main" | "any" | "<helper name>"`; default `any` for matching, so existing tests read the same. Extras are counted for the main agent only, unless the test names a helper. `Step` carries `actor`.
- **Where.** `core/trace.py` (`Call.helper` is already there), `steps.py` (`_claim`, the extras list), `suite/cases.py` (accept `in`), `docs/case-format.md`.
- **Done when.** A helper's unrelated calls no longer fail `extras: false`. A step with `in: "main"` does not match a helper's call.

### A3. Two tools with one name

- **Problem.** `tool_name` strips the server prefix with `rsplit("__")` (`core/trace.py:35`). `mcp__crm__search` and `mcp__docs__search` both become `search`.
- **Change.** `Call` keeps `server`. A test may write either `search` or `crm__search`. When a try holds calls to the same bare name from two servers and the test wrote the bare name, the step is not judged, with the reason "two tools are named search: say which".
- **Done when.** Test for the collision; existing single-server tests unchanged.

### A4. First difference from a passing try

- **Problem.** `review/divergence.py:163` lines the failing try up with a passing one by step number. One harmless extra call early moves every later step and the marker lands on the wrong one.
- **Change.** Line the two tries up by longest common subsequence over `(actor, tool name)`, then report the first place they part, with the kind it already has (missing, tool, args, context).
- **Done when.** Test: a passing try `[a, b, c]` and a failing `[x, a, b, d]` reports `d` against `c`, not `x` against `a`.

### A5. One shape for the page

- **Problem.** The try's API carries the same alignment three ways: `tool_rows`, `trajectory` and the plan `steps`' `actual`. The page draws all three.
- **Change.** One list, `timeline[]`, in time order: each entry is a turn message, an expected-against-made call pair, a reply with its graded lines, a state check, a page frame or a break. Each carries `status`, `span`, `at`, `actor`. The three old fields stay for one release, built from it.
- **Where.** `results/views.py` (owned by D for everything else; A adds this one builder in a new file `results/timeline.py` and D wires it).
- **Done when.** The mock-up's "Step by step" can be drawn from `timeline[]` alone.

### A6. Waiting for traces

- **Problem.** Every try waits until its spans have held still for `traces.settle_seconds` (6 s by default) before it is judged (`runs/collect.py:229-271`).
- **Change.** Stop as soon as every trace the adapter saw has its root span ended and nothing new has arrived for 1.5 s; keep the 6 s rule as the fallback when a root can't be told. Read the code before choosing the exact rule; the aim is to stop paying 6 s on tries whose traces are plainly whole.
- **Done when.** Existing `test_traces.py` passes; a new test shows an early stop on a closed trace and the fallback on an open one.

---

## B. Test environment: the copy tests run on

Today (`runs/isolated.py`): before a run, each copy is started one after another, its stores hashed and kept. Before every try, `restore()` stops every service but Postgres (`_pause`), puts each store back, starts the services and waits for healthy (`_resume`), then hashes every store again and compares.

### B1. Put it back without stopping the app

- **Problem.** The stop and start run on every try, try 1 included, though the copy was kept a moment before. Stop and start are each allowed up to 180 s (`isolation/launch.py:661-679`). `docs/plans/isolation.md` phase 4.1 planned this and it was not built.
- **Change.** Each driver says what it needs: `needs_stop: none | services | all`. Postgres: end the database's connections, drop it, create it from the template, with the app still up; then one health call. A volume: stop only the services that mount it. Stop everything only when the rehearsal saw the app keep state in its own memory (the rehearsal already tells; store that on the plan as `app_holds_state`). If the app doesn't answer after a hot put-back, fall back to today's path for the rest of the run and say so on the try.
- **Where.** `isolation/drivers/base.py` (the new attribute), `postgres.py` (`put_back` with `pg_terminate_backend`), `runs/isolated.py` (`restore`, `_pause`, `_resume`), `isolation/rehearse.py` (prove the hot path too, once).
- **Done when.** A Postgres-only plan restores with no container stop. The rehearsal fails a plan whose app breaks under a hot put-back and records that it needs a restart.

### B2. Check cheaply per try

- **Problem.** `_digests` runs after every restore: md5 over every row of every table, sha256 over every file in a volume. The cost grows with the sample data.
- **Change.** Full hash when the plan is proven, once after the first restore of a run, and once at the end of the run. In between, a quick check: the database exists and was created from the template after the try began; the volume's top-level listing matches. Setting `isolation.verify: run | every_try`, default `run`. The try's `isolation.checked` says which check ran.
- **Done when.** A run of 10 tries hashes twice, not ten times. A deliberately broken restore is still caught by the end-of-run hash, and the tries since the last good hash read as not judged.

### B3. Skip the first restore

- Try 1 of a slot runs straight after `keep`. Don't restore before it.

### B4. Start copies together

- `Copies.start` loops `for index in range(slots)`. Start them with `asyncio.gather`, at most 3 at once.

### B5. Keep a volume inside Docker

- **Problem.** `VolumeStore.keep` and `put_back` stream a tar through the host (`isolation/drivers/volume.py`).
- **Change.** Keep a second volume, `<name>_golden`, and copy between the two in one helper container (`cp -a`). Named `eval-*` like everything else.

### B6. Keep the copy warm, and clean up

- **Problem.** Every run starts the copy and takes it down. A crash leaves it behind and the next start fails with "already running: take it down first" (`launch.py:579`). Work folders of rehearsals are never deleted.
- **Change.**
  - After a run, keep the copies up for `isolation.keep_warm_seconds` (default 600). The next run or page-learn step reuses a copy when the plan version and image ids are the same; it restores first.
  - Each copy carries labels `evalution.owner=<server id>` and `evalution.run=<run>`. On server start and before any start, remove `eval-*` projects whose owner is not running.
  - One lock per target for isolate, rehearse and run, on the data folder, so the CLI and the server can't collide.
  - Delete a rehearsal's work folder when it ends.
- **Done when.** Two runs in a row start the copy once. Killing the server mid-run and starting again leaves nothing behind and the next run starts.

### B7. What leaves the copy

- **Problem.** The copy is started with the project's own env file (`launch.py:516`). It holds the live keys, so a test can charge a card or send mail. Nothing warns.
- **Change.** When the plan is drafted, list env names that look like third-party credentials: a short pattern list (payment, mail, SMS, webhooks, cloud storage) plus any name ending `_KEY`, `_SECRET`, `_TOKEN` that isn't the model provider's or evalution's. The copy's override sets those to an empty value unless the person allowed them. The isolation API returns `outbound: [{name, kind, allowed}]` and takes `PUT .../outbound {name, allowed}`. Names only; values are never read into memory beyond the scrub that already exists.
- **Not in scope.** A network proxy. This is env only.
- **Done when.** A compose app with `STRIPE_SECRET_KEY` in its env file starts its copy with it empty, and the setup shows it as blocked.

### B8. Say no where it can't hold

- A bind mount shared with the real app is only noted today (`launch.py:226-233`). Refuse to prove a plan that leaves one writable and uncovered; say which path.
- Any `container:db` locator gets a `PostgresStore` whatever its kind (`isolate.py:351-362`). Build one only for Postgres. For Redis without persistence, strategy `restart` (true once B1 lands, since restart is no longer free). For others, strategy `none`, shown as "not put back".
- On Linux, add `extra_hosts: host.docker.internal:host-gateway` to the override so the copy's traces reach the intake. Check first whether it is already there.

### B9. Use what learning already found

- `isolation/discover.py` reads up to 4,000 files with its own patterns to find stores. The code map (`project/codemap/facts.py`) already has env reads, settings and compose facts per file. Take the candidates from the code map when the target has one; keep the scan as the fallback.

---

## C. Learning: what evalution knows about the app

Today (`writer/learn.py`, `_begin` onward): a free check for "nothing changed"; code map and skeleton with no model; then one agent per area, 8 at a time, each with a budget of 60k to 200k tokens; a first overview and a final overview; the whole saved as the understanding.

What reads it: the test writer (the rendered profile, in its instructions at every step) and the page. Judging does not read it. Details, the skeleton page, the diagram and the first overview are for the page only.

### C1. Facts first, areas when needed

- **Problem.** The first learn of an app runs every area before anything else can use it. The parts setup and test writing need (entry point, tools and their arguments, state, how it starts, how often real traffic used each) all come from the code map and skeleton, with no model.
- **Change.** Two levels.
  - **Facts**: code map, skeleton, trace hits, and one light call for the one-line summary. This is "learned" for the purposes of connecting, setting up the copy and writing a first test. Seconds, not minutes.
  - **Areas**: learned when something asks: the test writer is about to write a test that names a tool or file in that area; a person presses Learn on it; or `code.learn: full` is set. The writer's `read_understanding` triggers the area learn itself and waits for it.
- **Where.** `writer/learn.py` (split `_begin` at the point the skeleton is ready and return there by default), `writer/areas.py`, `writer/tools.py` (the on-demand trigger), `project/understanding/computed.py` (a card and diagram from facts alone).
- **Done when.** Connecting an app shows tools, state and the diagram with no area agent run. Writing a test about one tool learns that tool's area only.

### C2. One record of what the code was

- **Problem.** Four baselines track "has the code moved": the drift fingerprint (`fingerprint.json`, since the profile was written), `files.json` commit and hashes (since the last learn), `knowledge.json` `checked` (since the last refresh), `tested.json` (since the last run). The page shows three different signals from them. A refresh leaves the fingerprint alone on purpose, so "Changed since learned" stays after a successful update.
- **Change.** One document, `<target>/state.json`: `{learned: {commit, hashes}, refreshed: {commit}, tested: {commit, code}}`. One function answers "what changed since X" for all three marks. One signal to the page: `changes {files, areas, claims, tests}`. Old documents are read once and folded in.
- **Where.** New `project/state.py`; `drift.py`, `knowledge.py`, `impact.py`, `filememory.py` read and write through it.
- **Done when.** The Home page's change row, the learned page's "changed" pill and stale test marks all come from one call and agree.

### C3. A refresh must survive the next learn

- **Problem.** A knowledge refresh rewrites claims in `understanding.json`. The next learn merges sections from `files.json` (`learn.py:734`, `:745`) for any area whose key did not move, so the refreshed claims go back to what they were. The writer's own edits are lost the same way.
- **Change.** The area's stored section is the one source. A refresh, a steer that becomes a fix, and a writer edit all write into the area section; `understanding.json` is built from the sections.
- **Done when.** Test: refresh a claim, learn again with nothing moved, the claim is still the refreshed one.

### C4. Drop what no longer earns its place

- `modules.summaries` and `modules.app_summary` are read into every overview (`learn.py:518-520`, `955`, `1160`); nothing writes them any more. Remove the reads and `project/modules.py`.
- The first overview (up to 2 calls) goes; the facts card from C1 stands in until an overview is written.
- `_say_missing` (the uncommitted change in `writer/areas.py`) must spend from the area's own budget, not on top of it.

### C5. The profile the writer carries

- **Problem.** `writer_profile` renders the whole understanding with no cap (`project/understanding/rendering.py:35`) and it is sent in the instructions at every writer step (`writer/agent.py:470`).
- **Change.** Send the overview, the tool table and the state list always. Send an area's claims only when the conversation names a tool, file or area it covers, or when the writer asks with `read_understanding(area)`. Cap the whole at about 8,000 tokens.

### C6. Say what was left out

- `code.py` watches at most 2,000 files (`HASH_CAP`); beyond that, files are dropped with nothing said. Say so in the learn's reply and on the learned page. Same for submodules and LFS files in a version's checkout.

### Seen on a large app (do these first in C)

A learn of a large multi-agent repository behind a company model gateway ended like this: 35 areas, 13 learned, 22 failed; "I stopped after 20 minutes"; then "model answered 504: Gateway Timeout"; then "profile: at most 400000 characters" and nothing saved. The diagram showed 17 agent boxes and "Show 94 more never seen in use". Each has a cause in the code.

### C7. Learn the agent, not the whole repository

- **Problem.** Every source file is watched unless `code.include` is written by hand (`code.py:162-171`). The author's own app has a hand-written include; a new app has none. So areas were made for the web components (217 files), the iOS project (101), database migrations (222), vendored plugin payloads. `MAX_AREAS = 12` (`project/areas.py:40`) caps folders, but heavy areas are then cut into pieces ("part 3 of 5"), so 12 became 35.
- **Change.** Pick the scope from the skeleton, with no model: start from its entries (prompts, tools, agents, model calls, routes, state) and take the files that hold them plus what they import, two hops (`project/imports.py` already has the graph). Everything else is "not part of the agent": listed, never learned unless asked. Cap the total number of pieces, not only folders: at most 12 learned in one go, heaviest first; the rest wait as "not learned yet" with a button. `code.include` still wins when set.
- **Done when.** On a repository with a large UI and mobile folder, no area is made for them, and a first learn runs at most 12 area agents.

### C8. Survive a gateway that cuts long calls

- **Problem.** A model call waits up to 300 s for one whole answer and nothing streams (`llm.py:41`, `TIMEOUT`). A gateway that closes idle requests after a minute answers 504. The call is retried the same way (`llm.py:153-169`), fails the same way, and the area is marked failed. That is most of the 22 failures.
- **Change.** Stream every model call (both the Anthropic and the OpenAI-compatible paths) so bytes keep the request open. On a 504 or a read timeout, don't repeat the same call: ask for a smaller answer (halve `max_tokens`, as the area agent already does for a cut answer). Make the timeout a setting, `model.timeout_seconds`. Say the cause on the area: "the model's gateway timed out (504)", not just "Failed".
- **Done when.** A test gateway that closes after 30 s of silence no longer fails an area.

### C9. A learn must never fail at the save

- **Problem.** After the areas are learned, the save raises `profile: at most 400000 characters` (`project/profile.py:30,96`) and the turn ends with "Something went wrong". The work is kept per area, but the understanding is not written, and the next learn hits the same wall.
- **Change.** The rendered profile is cut to fit, heaviest areas first, and says what it left out; it never raises. Details is not part of the profile (it is already not sent to the writer). With C5 the writer's copy is far smaller than the cap anyway.
- **Done when.** A learn whose sections total over the cap saves, and the page says "12 of 35 areas are in the profile".

### C10. A time limit that doesn't need "go on"

- **Problem.** The learn runs inside a chat turn with a 20 minute limit (`writer/agent.py:80`, `MAX_SECONDS`); the person must type "go on", which starts another turn that can fail for an unrelated reason.
- **Change.** A learn is a background job with its own record and progress, not a chat turn (the isolation job is the model: `api/isolation.py`). It has no wall-clock stop; it stops when its areas are done or its budget is spent, and resumes by itself after a server restart. The chat only links to it.

### C11. A diagram a person can read

- **Problem.** Every agent definition in the code becomes a box: 17 shown, 94 more hidden. On a platform that hosts many agents the picture is a list.
- **Change.** At most 8 boxes per column. Group the rest by folder into one box with a count ("Example agents · 23"), opened on click. Order by real use, then by weight. An app that hosts many agents gets a first question at connect: "Which agent do you want to test?", and the scope in C7 starts from that one.

### C12. Small things from the same screens

- A failed area shows its paths as its title and no reason. Give it a short title from its folder and the reason it failed, with "Try again".
- Titles show `&amp;` (escaped twice). Fix where the area title is rendered.

---

## D. Runs (supporting)

- **A queue.** One run plays at a time and a second is refused (`runner.py`, `RunLock`). Queue the second; play it when the first ends.
- **Write-up outside the lock.** The findings after a run (`review/findings.py:450-487`) hold the lock. Release it when the last try is judged; write findings after.
- **Save without a model turn.** "Add to tests" starts a model turn that only calls `approve` and `save_case` on a file that can't change (`writer/agent.py:338-380`). Save the approved plan's file directly.
- **No model call to word a plan.** `review/testplan.py` spends a light call per test version for wording. Use the mechanical wording.
- **Keep media for 30 days.** Screenshots and videos are never deleted; `RunStore.delete_run` has no caller. Delete a run's files after `files.keep_days` (default 30) and wire `delete_run`.

## E. Code and versions (supporting)

- **GitHub address.** `discover.py` takes a folder or an address. Accept `github.com/owner/repo`, `owner/repo`, `owner/repo#N`, `owner/repo@ref`: clone into `<data_dir>/code/<target>` with the person's own git credentials, then connect as a folder.
- **Pull requests.** `versions.py` fetches `refs/heads/*` only. Fetch `refs/pull/N/head` into `refs/evalution/pull/N`.
- **Always fetch a remote branch** before playing it. Today `reach()` returns an earlier fetch.
- **Don't remove a checkout another run is using**; lock the versions folder across processes.
- **Post the result** to the pull request as a check when a token is set (`cli.py --summary` already makes the words).

## F. Benchmark scoring (supporting)

- Quick is 5 questions with no unanswerable one; raise the smallest size to 30 with every kind.
- A try that broke off is turned from failed into not judged (`benchmarks/longmemeval.py`, `_said`), so an app that hangs loses nothing. Count the app's own timeouts and crashes as wrong; keep only grader failures as not judged.
- Show a range beside every percentage and call a change real only outside it. Count unanswerable questions inside accuracy.
- Grade on the question, the known answer and the reply only, without the general rule about figures needing a tool call.
- Say on the page that the numbers are a sample and not comparable with published scores.

---

## UI packages

Build from `docs/mockups/evalution-next.html`. Its tokens replace those in `web/src/index.css`; its type is one family at 15px. Keep every capability the current screens have (the inventory is in the mock-up's screens; where the mock-up shows less than today, put the rest under a fold, don't drop it).

- **U1 Shell and assistant.** Sidebar: Overview, Tests, Runs, Benchmarks; under the app's name, What it learned and Test environment; Settings at the foot. No breadcrumb on a top-level screen; a back button on a test or a run. The assistant is the logo at the bottom right; it opens a panel on the right. Each open starts a short session of one of three kinds with its own context: about what's on screen (the try, the run, the map), about the app, or a new test. The screen's context is attached as a chip the person can remove. Sessions are not carried from one screen to the next.
- **U2 Connect, setup, environment, learned.** One field to connect. Setup is one live screen: four stages, what it's doing line by line, the copy drawn as it is made, the map as it fills. Needs B7's `outbound` and C1's facts card.
- **U3 Tests, test, runs, home.** A test opens inside Tests with three tabs. The latest result is drawn from A5's `timeline[]`: verdict and layers, analysis with evidence that jumps to its step, step by step with an inspector, trace, what the model saw, how it was decided, the page's frames, earlier results. The run dialog has four rows: tests, code, how it plays, keeps.

## Checks before calling it done

- `uv run pytest` and `npm test` pass.
- `evalution demo ui` and `evalution demo versions` still play end to end.
- On the demo app: connect to first test in under a minute with no area agent run (C1); ten tries restore with no app restart and two full hashes (B1, B2); a second run reuses the copy (B6).
- A test with `order: "exact"` and one extra call fails (A1).
