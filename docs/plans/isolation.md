# Plan: isolation found and proven (ADR 0009)

From the prototype on `isolation-discovery` to a target whose tries are proven independent. Each phase ships on its own and leaves `main` working.

## Where it stands

| Part | File | State |
|---|---|---|
| Code read | `isolation/discover.py` | Prototype. Finds the right settings; noisy on real code |
| Probe on this computer | `isolation/observe.py` | Prototype. Files written and held open, database sockets |
| Probe inside containers | `isolation/containers.py` | Prototype. `pg_dump` and `grep` across the compose project |
| Rehearsal | `isolation/rehearse.py` | Prototype. Files, folders and SQLite only |
| CLI | `evalution stores FOLDER [--target T]` | Prototype |
| Plan, launch, drivers for Postgres and volumes, runner, page | | Not started |

Tests: `tests/test_isolation_discover.py` (scan, lsof parsing, toy app probed end to end, container finds matched), `tests/test_isolation_rehearse.py` (full plan proven, missing store caught, app left as found).

## Phase 1: the plan as data

`isolation/plan.py`.

1. `Plan`: `stores` (id, kind, locator, `moved_by`, `keyed_by`, `strategy`, `evidence`, `seen`), `launch`, `exclude`, `status` (`draft`, `proven`, `stale`, `failed`), the rehearsal's report, and the hashes of the files its evidence cites.
2. Stored beside the target's understanding, versioned like it, so a plan can be undone.
3. `draft_from(stores, observation)`: the plain-code first draft. Keeps probe-confirmed stores and settings that move them, drops literals the probe never saw, and puts a found trace store (`*.spans`, a Phoenix schema) in `exclude`.
4. Strategy chosen by rule: `relocate` when a store has `moved_by`, `snapshot` when it has a path, `identity` when `keyed_by` is a person and nothing else applies, `none` for hosted.

**Done when:** relay-lite's probe gives a draft with two stores to reset (the `engine` schema, `artifact-data`) and `phoenix.spans` excluded, with no model call.

## Phase 2: drivers for every kind the plan names

`isolation/drivers/`, one protocol: `keep(into)`, `put_back(kept)`, `digest()`, `holding(canary)`, `covers(path)`.

1. `files`: today's `FileStore`, with `cp -c` on APFS.
2. `postgres`: through `docker exec` or a URL. Keep is `CREATE DATABASE eval_golden_<id> TEMPLATE <db>` (connections closed first). Put back is drop and create from the template. Digest is row counts plus `md5` of each table's rows, per schema, leaving out `exclude`.
3. `volume`: keep is a tar of the volume from a throwaway container. Put back is clear and untar. Digest is a hash of the tar listing and contents.
4. Each driver refuses to touch anything not named `eval_*` or not inside evalution's own copy (phase 3), with a test for each refusal.

**Done when:** each driver round-trips (keep, change, put back, same digest) in tests, the Postgres and volume ones against a throwaway container in CI.

## Phase 3: evalution's own copy

`isolation/launch.py`.

1. Compose apps: `docker compose -p eval-<target>-<slot>` with an override file evalution writes (ports remapped to free ones, named volumes left fresh, no host ports below 1024). The target's adapter is pointed at the copy's port.
2. Process apps: the start command from Connect, with the settings in `moved_by` pointed at evalution's copies, and `PORT` set.
3. Health check from Connect, a start limit, and logs kept for the report.
4. Containers created while the copy runs (labelled or named after the project) are added to what the probe and rehearsal search, and removed with the copy.
5. `evalution isolation down <target>` removes every copy evalution started, and only those.

**Done when:** relay-lite runs as `eval-relay-lite-0` next to `relay-evals`, with its own ports and volumes, and `down` leaves `docker ps` and `docker volume ls` as they were before.

## Phase 4: rehearsal on the plan, and the model's part

1. `rehearse(plan, copy)` over drivers instead of `FileStore` only. Restore per slot: stop and start the app only where a driver needs it (files held open), else restore the database under a running app with its connections reset.
2. The model's part, as typed tools in the existing agent loop: `read_store_evidence`, `propose_plan`, `rehearse`. It sorts the code read's candidates, fills `keyed_by`, and revises the plan from a failed rehearsal's leaks. At most 3 revisions, then the person is asked.
3. Rehearsal also catches a store the probe never reached: a planned store whose hash didn't move is shown as unconfirmed, not failed.

**Done when:** relay-lite's plan is proven on its copy, and removing `artifact-data` from it makes the rehearsal fail naming `memory_index`.

### 4.1 as built: put back without stopping the app

- Each driver says how far the copy stops while it puts its store back (`needs_stop`): Postgres `none` (`DROP DATABASE ... WITH (FORCE)` ends the app's connections, the database is made again from its golden copy, then one health call), a volume `services` (only the services that mount it stop), a file `all`. The copy stops as far as the widest needs (`isolation/hot.py`), and wholly when the plan has a store of kind `memory` (`plan.app_holds_state`).
- Less than a whole stop is used only once the rehearsal proved it. After the usual proof, the rehearsal puts the stores back twice with the app up, a probe between (a third message to the copy): the app must answer each time and end in the starting state with that probe's canary gone. The plan records `hot: proven | failed`; a plan from before reads `None`. Anything but `proven` keeps the whole stop and start, so today's path is the fallback, chosen per plan.
- In a run, an app that doesn't answer after a hot put back is stopped and started, and stays on that path for the rest of the run; the try's `isolation.note` says so.
- A slot's first try isn't put back: the copy, started again after keeping, is hashed once, and plays as kept when it matches and nothing opened a session on it (the connection check, say).
- Checks: a full hash after a slot's first put back and at the end of the run; between, each store's shape (Postgres: its tables and a new `oid`; a volume: its top-level listing; files: each file's size). `isolation.verify: every_try` hashes every put back. Each try's `isolation.check` is `kept`, `full` or `quick`. An end-of-run hash that doesn't hold marks the cheaply checked tries since the last good hash "reset didn't hold" (`Copies.late`).

### A volume kept inside Docker

A volume's starting state is a second volume, `<volume>_golden` (named after the copy's project, so `down` removes it with the copy), made and put back with `cp -a` in one throwaway container with no network: no tar streams through this computer. A folder kept as `volume.tar` before this is still put back from the tar.

### Copies started together, one holder at a time

- A run's copies start side by side, at most three at once (`STARTING_AT_ONCE`); every one started is kept for `close`, even when another failed.
- `isolation/lock.py`: one lock per target on `<data>/isolation/<target>/copy.lock` (an advisory `flock`, let go by the system when its holder exits). Finding isolation, a rehearsal, and a run's copies (from `Copies.start` to `Copies.close`) each take it; a second holder, from the server or the CLI, gets `Busy` at once, a `LaunchError` saying the copy is in use. A run that finds it busy while rehearsing a stale plan again leaves the plan as it was.
- A rehearsal's work folder (its override file and starting state) is deleted when it ends; kept when the copy didn't start or stop, since that error names its logs there.
- Not done yet: keeping copies warm between runs, and labelling copies with their owner so a crashed server's are removed at the next start. Both need the server's start and the runner's end of a run (not in this package).

### Saying no where a reset can't hold

- Only a planned database of kind `postgres` in a container gets the Postgres driver. One of kind `redis` keeps nothing on disk (a Redis that does has its volume planned too): a restart clears it, so the plan holds state (`plan.holds_state`) and every put back stops and starts the whole copy. Any other kind is noted "not put back", and a run doesn't promise the memory clean (`covered`).
- On Linux, where Docker doesn't name `host.docker.internal`, the override gives each service `extra_hosts: host.docker.internal:host-gateway` unless it already maps that name, so the copy's traces reach evalution's intake.
- Not done yet: refusing to prove a plan that leaves a bind mount shared with the person's app writable and uncovered. Every compose copy's bind mounts are the person's (`roots_of` gives a compose copy none), so the literal rule refuses any writable bind mount, source-code mounts for live reload included; which ones to refuse is left to decide.

### What leaves the copy

The copy starts with the project's own env file, which may hold live keys. `isolation/outbound.py` lists the setting names (from the env file, the compose files' `environment` and `env_file`, and a process copy's `env`) that look like a third-party credential or address: a word of a kind (payment, mail, SMS, webhook, cloud storage) with an ending that reaches it (`_KEY`, `_PASSWORD`, `_HOST`, `_URL`, ...), or any name ending `_KEY`, `_SECRET`, `_TOKEN`. The model provider's, evalution's and the app's own (session, signing, database) are left alone. The copy, in a run and in a rehearsal alike, starts with each set empty unless the person allowed it (`<data>/isolation/<target>/outbound.json`, names only). Values are never kept, returned or logged. Env only, not a network proxy. A framework's own signing key with a generic name (`*_SECRET_KEY`) is listed too; an app that won't start without it fails its rehearsal, and the person allows it.

## Phase 5: the runner

1. `Isolation.MANAGED` in `adapters/base.py`; `starts_clean` is what the proven plan covers.
2. In `runs/runner.py`, where `reset` is called today: restore the slot's copy, then check hashes. Beside `probe`: look for the earlier try's canary in every planned store. A failed check leaves the try unjudged, "reset didn't hold", with the evidence.
3. Each try sends its own canary in its first message's metadata where the adapter allows it, else in a harmless first line, so leftover state from one try can be told from the next.
4. A stale or failed plan: the run is labelled "not isolated", or refused, as `isolation.require_proven` says (default: label).
5. `tests/proof/test_vacuous.py`: the xfail for a pass resting on leftover state flips to a pass with a managed scripted agent.

**Done when:** the xfail is gone, and a run on relay-lite shows each try's restore and check in its timeline.

## Phase 6: keeping it current, and the page

1. The plan's evidence files join the claim links (ADR 0005). A commit touching them marks the plan stale. The next run rehearses first, with no click, since it only touches evalution's copy.
2. Connect gets an "Isolation" step: what was found, the plan in plain words, one approval, then the rehearsal's steps as they run.
3. Each result says how its try was isolated: proven plan, identity only, or not isolated.
4. README Known limits and `docs/adapters.md` updated (ADR 0009, "Amends").

**Done when:** editing relay-lite's `db/utils.py` and committing marks its plan stale on Home, and the next run rehearses before playing.

## Order and size

Phases 1 and 2 can go side by side. 3 needs 2's volume driver. 4 needs 1 to 3. 5 needs 4. 6 needs 5. Roughly: 1 is small, 2 and 3 are the bulk, 4 to 6 are medium.

## Risks

| Risk | What we do |
|---|---|
| A slow app makes restarts per try too slow | Restore per slot, database-level between tries (phase 4.1) |
| `TEMPLATE` needs no connections to the source | Close the app's pool or stop the app around the copy |
| An app ignores the settings and hardcodes paths | Snapshot in place on the copy; the rehearsal catches what was missed |
| Hosted memory | Identity only, labelled on every result |
| The probe spends a real turn | Said before it runs, like `check --isolation` |
| Touching the person's own data | Drivers refuse anything outside evalution's copy; tested |
