# 0009: Isolation is found and proven before a test runs

> **Status:** Proposed 2026-11-07. Prototype on branch `isolation-discovery`.

Isolation was a promise. An adapter declared `Isolation.SESSION` or `IDENTITY`, and nothing checked that the app kept to it: `fresh` is passed and never verified, the HTTP adapter only starts a new session id, and a pass that rests on what an earlier try left behind is still an open xfail in `tests/proof/test_vacuous.py`. For an eval tool built for stateful assistants, that is the result it can least afford to get wrong.

The fix can't be written per project. Each app keeps state its own way: Postgres in a container, a SQLite file, a memory index in a volume, a file at a path nothing in the code names. So evalution has to find out where an app keeps state, make a plan to reset it, and prove the plan works, on its own and before the first test.

## What the prototype showed

- **Reading the code suggests; watching the app decides.** On relay-lite the code read found the right settings (`ATRIUM_DB_URL`, `DATABASE_URL`, the `postgres` service, the `artifact-data` volume) among about 90 SQLite names from fallbacks and comments. One probe carrying a canary, watched from inside the containers, found exactly four places it landed: `engine.conversation_items`, `engine.round_events`, `phoenix.spans` and a memory index file in `artifact-data`.
- **A database reset alone would have leaked.** The memory index lives on disk beside Postgres. Resetting the database between tries would have left memory behind.
- **Not everything found should be reset.** The app's own traces live in the same database (`phoenix.spans`). Resetting them would erase evidence.
- **A plan can be proven, and an incomplete one can't pass.** On a toy app with a store outside its folder that no code names, the rehearsal proves a full plan, fails a plan that misses that store and names it, and leaves the app byte for byte as it found it either way.
- **On the host, a Docker app is a port forwarder.** Watching only the process on the target's port saw nothing. The containers behind the port have to be watched too.

## Decision

- **A new stage, Isolate, between Learn and Run.** It discovers the stores, writes a plan, rehearses it, and records it as proven. A target with no proven plan runs labelled "not isolated", or is refused, as a setting says.
- **Three sources of evidence, the probe decisive.** The code read (packages, env names, compose files, literals) gives candidates and the settings that move each store. The learn agent sorts the candidates and says what each is keyed by. A watched probe (one message carrying a canary; files written and held open on this computer; `pg_dump` and `grep` inside the target's compose project) says what the app really touched. A store the probe touched and the plan doesn't cover means the plan is incomplete.
- **The model decides; deterministic code acts.** The model reads evidence and writes the plan as data: each store's kind, locator, the setting that moves it, what it's keyed by, the strategy, what to leave alone, and the evidence. Snapshots, hashes, copies, restores, `pg_dump`, `grep`, starting and stopping are typed drivers. The model never gets a shell, and the existing agent loop with a few typed tools is enough. No second agent runtime.
- **Strategies by kind, the best one each store allows.** Relocate and clone (a setting points the store at a copy of the starting state: a Postgres `TEMPLATE`, `cp -c`, a fresh volume); else snapshot and restore in place, with the app stopped; else identity only, where the store is keyed by person and the rehearsal shows it holds; else declared not isolated (hosted memory) and labelled on every result.
- **Prove before trusting.** The rehearsal keeps the starting state, sends a probe, fails if the app wrote anywhere the plan doesn't cover, restores, checks every hash, starts again, fails if the canary is anywhere the app can reach, and always ends with the starting state put back. The model revises the plan from a failed rehearsal's evidence, in a bounded loop like the writer's.
- **Evalution works on its own copy.** For a real project, rehearsals and runs use an instance evalution starts: its own compose project name, ports and volumes, or its own copies of files and databases. The person's own stack and data are never reset. Evalution removes only what it created and named (`eval_*`), as `docs/adapters.md` already requires.
  - *Amended:* a folder (bind mount) or external volume the copy shares with the person's own app is always named, with the service and whether it may be written, in the copy's summary, the rehearsal's account and the plan (`shared`). The plan is refused, not proven, only when the rehearsal's probe turns up inside a writable one: a test that writes there would change the person's own files, and putting it back would too. A source folder mounted for live reload that no test writes in only warns. A volume is kept as a second volume inside Docker (`<volume>_golden`), and every throwaway container a driver starts is named after its volume, so it is `eval-*` too.
- **Every try is checked, not trusted.** Before a try: restore, then check hashes against the starting state. After it: look for the earlier try's canary and for writes outside the plan. A failed check leaves the try unjudged, the way `probe` does today.
  - *Amended:* the full hash runs after a slot's first restore and at the end of the run, with a cheap check of each store's shape between (`isolation.verify: every_try` keeps a full hash per try); an end-of-run hash that fails takes back the cheaply checked tries since the last good one. A slot's first try, on a copy still as kept, isn't restored.
  - *Amended:* a restore stops the copy only as far as its stores need (a database not at all, a volume its services), once the rehearsal proved the app survives that; otherwise, and for an app that keeps state in its own memory, it stops and starts the whole copy as before.
- **The plan is kept current like a claim.** It links to the files its evidence cites (ADR 0005). A commit touching them marks it stale, and it's rehearsed again before the next run.
- **The person approves the plan once,** in Connect, before the first rehearsal. After that, rehearsals that follow a stale mark run without a click, since they touch only evalution's own copy.

## Consequences

- A new `Isolation.MANAGED` level. `starts_clean` for it is what the proven plan covers, not what the adapter promises.
- Runs get slower to start: a rehearsal on first connect and after relevant commits, and a restore per try or per slot. Restoring per slot, with database-level restores between tries, keeps slow-starting apps usable.
- Evalution needs Docker for compose apps, and `lsof` on the host. Without them it falls back to what it can see and says so.
- The probe sends real messages, so it spends what one turn of the app costs.
- Containers an app starts at runtime (relay's per-person hosts) aren't in its compose project. The probe has to add whatever it sees created while it runs.
- Hosted memory can never be better than identity only, and results say so.
- Vendor knowledge (a framework's checkpointer, its in-memory store) stays behind adapters (`tests/test_no_vendor_in_core.py`). An adapter's `INFO` may add store hints.

## Amends

- **ADR 0004 / `docs/adapters.md`:** `isolation` and `clean` remain for adapters, but a proven plan overrides them for the stores it covers.
- **README, Known limits:** "Isolation depends on the adapter" and "`fresh` isn't verified" become true only for targets without a proven plan.
