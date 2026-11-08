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
