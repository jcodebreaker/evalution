# Plan: learn from the skeleton, read on demand

Decided in [ADR 0012](../adr/0012-learn-from-the-skeleton-read-on-demand.md) (Proposed). Today a learn reads and summarizes hundreds of whole files before it understands anything. This plan makes it start from what costs nothing (the code map's skeleton and the traces), put area agents to work at once, read a file only when an agent needs it, and show something useful within minutes.

## What the person gets

- **Within seconds:** What it learned shows the app's skeleton: entry points, prompts, tools, model calls, routes, where it keeps state, each with file and line, and which of them real use touched ("search_docs: 140 calls this week").
- **Within a few minutes on a big app:** a first overview ("first pass, from the skeleton and the traces"), and areas filling in one by one, the most used first.
- **When it's done:** the overview rewritten from the areas, as today.
- **Never starting over:** a stop, a restart or a time limit loses at most the area being learned; "go on" continues with the next.
- **Big apps don't wait for small parts:** areas with no prompt, tool, state or trace hit are listed and learned only when asked ("Learn this area") or when a change touches them.

## What's true today (checked in code)

| Stage | Where | What it costs |
|---|---|---|
| Code map | `project/codemap.py` | No model. Parses every file, kept by fingerprint. 199 s for 1,996 files on a big app, the first time |
| Read | `project/filememory.py` `index` | The light model reads whole files of the slice, 8 batches of up to 60,000 characters at once (4 before `4d0583c`), at most 300 files (no cap before) |
| Folder and app summaries | `project/modules.py` `index` | More light-model calls, bottom up |
| Areas | `writer/areas.py`, `writer/learn.py` `PARALLEL = 3` | An agent per area: `STEPS = 4`, `BUDGET = 60,000` tokens, tools to read its own files (`READ_CHARS = 16,000`); brief = cards, static facts, Read's summaries, names to cover |
| Overview | `writer/learn.py` `_overview` | One bounded call once all areas are in |
| Traces | `traces/profile.py`, read once per learn | Inform the overview; not the order, not what is read |
| Time | `writer/agent.py` | The chat's 20 minutes; a learn's own hour since `4d0583c` |

Measured on one big app (2026-11-09): Map 199 s; Read reached 392 of 760 files in 26 minutes, before the cap and the parallel change; nothing learned yet when it was cut.

## Decisions

1. **Skeleton = the code map's facts, gathered.** `CodeMap` already marks what a file *does* (`tool`, `prompt`, `prompt-builder`, `agent`, `route`, `state`), finds definitions, references and static facts (constants, limits). The skeleton is those, grouped by kind, with the text of each prompt (capped), each tool's name and arguments, each model call's model and where it's called from. A new pure function over `CodeMap`; stored as part of the understanding with the map's fingerprint.
2. **Trace hits join the skeleton by name.** A tool span's name, a prompt's hash or first words, a route's path: matched to the skeleton's entries, counted over the traces' window. What doesn't match is listed as "seen in use, not found in the code" (a finding in itself).
3. **Weight decides order and budget.** An area's weight = its prompts, tools, state and agents (from the skeleton) plus its trace hits. Areas run heaviest first, eight at once by default (a setting; the provider's rate limit is the ceiling, said when hit). An area's budget (steps, tokens) grows with its weight, from a floor; zero-weight areas are listed, not learned.
4. **An area's brief replaces Read.** Its part of the skeleton, the cards and static facts of its files (as today), its trace hits, its previous section, and kept notes and summaries of its files. No bulk summaries are made for it first. Its agent reads files through its existing tools; what it learns of a file is kept with `note` under the file's fingerprint.
5. **Saved per area.** Each area's section is checked and saved as it finishes (today: merged at the end). The page reads them as they land. A learn's progress is the list of areas done.
6. **A first overview early.** As soon as the skeleton and trace hits exist, one bounded call writes a first-pass overview from them alone, marked as such; the final overview replaces it.
7. **Bulk reading stays as a fallback only:** for a file the code map can't parse (no grammar), when it is in a weighted area.
8. **Never learn the same code twice.** Everything learned is kept under the fingerprints of what it was learned from, and reused while they hold:
   - the skeleton and the code map, by each file's fingerprint (parsed again only when a file moved, as the map is today);
   - what an agent learned of a file (`note`), by that file's fingerprint;
   - an area's section, by the fingerprints of its files and its trace hits' counts rounded into bands, so a few more calls of the same tool don't relearn it;
   - the overview, by its sections' and the skeleton's hashes.
   A learn with nothing moved makes no model call (as today); a learn after a change relearns only the areas whose files or trace bands moved, and those agents start from their previous section and the notes still current, reading only the files that moved. "Learn this area" on an area that hasn't moved says it's current and shows when it was learned, with "Learn it again anyway" as the deliberate way to spend.

## Phases

### Phase 0: measure today, on a big app and a small one

Instrument a learn's stages (already timed by `_Clock`) and count model calls and tokens per stage; record on a big app and on the made-up app. These numbers are the baseline every later phase is checked against. Pick a public, large agent app as a second big fixture for tests that can run anywhere.

### Phase 1: the skeleton (no model)

1. `project/skeleton.py`: `skeleton(cmap) -> Skeleton` (entries by kind, with path, line, name, a capped text), deterministic, with the map's fingerprint.
2. Trace hits: `match(skeleton, digest) -> hits` by name; unmatched uses listed.
3. Stored with the understanding; `What it learned` shows it at once, before any model call.

Tests: on the made-up app, every prompt, tool and store it has is in the skeleton with its line; a tool span name matches its definition; a span with no match is listed.

### Phase 2: areas start at once, weighted, saved as they finish

> Built 2026-11-09: no Read or module summaries on the normal path (the fallback for files no grammar reads stays), areas weighed and keyed, heaviest first, eight at once (`code.area_parallel`), budgets from weight, each section kept as it lands, zero-weight areas listed with "Learn this area" (`POST /v1/targets/{t}/areas/{area}/learn`). The page still shows the sections when the learn is saved, not as they land: that is phase 4.

1. `learn.begin` skips `filememory.index` and `modules.index` on the normal path; the slice becomes the area order.
2. Area weight and budget (decision 3); `PARALLEL` from a setting, default 8; `STEPS` and `BUDGET` scaled by weight.
3. The brief (decision 4); notes kept as agents read.
4. Each section checked and saved as it lands; progress = areas done; resume = areas not done.

Tests: on the big fixture, the first area starts within seconds of the map; a learn stopped after two areas keeps both and the next does only the rest; a zero-weight area isn't learned and is listed.

### Phase 3: the first overview, then the final one

The early first-pass overview (decision 6) and its replacement; the page shows the state ("first pass" until the areas are in).

### Phase 4: the page

What it learned shows the skeleton immediately, areas as they land (with "Learn this area" for listed ones), and the overview's state. The Learning card's stages become Skeleton → Areas (n of m) → Overview, with no global "Read n of 760".

### Phase 5: compare and decide

Run phase 0's measures again. Keep the change only if, on the big app: the skeleton shows in under 10 s after the map, a first overview in under 5 minutes, the full learn in well under today's, with fewer model calls; and on the made-up app the understanding still covers every prompt, tool and store (the existing coverage checks). Retire the bulk path then, or keep it behind the fallback only.

**Run (2026-11-09).** One real app (relay-lite: 123 files in its code include, 54 read), learned cold twice from copies of the same data: the old path at `75ccd25`, the new at `8898f55`, one after the other, measured by `evalution learn --report`.

| | Old | New |
|---|---|---|
| Whole learn | 175 s | 111 s |
| Model calls | 34 (read 17, folders 1, areas 14, overview 1, other 1) | 23 (first overview 2, fallback read 1, areas 18, overview 1, other 1) |
| Tokens in / out | 425k / 108k | 401k / 34k |
| Skeleton shown | — | 2.7 s after the map |
| First overview | at the end, 175 s | 26 s after the skeleton |
| Skeleton names covered by the understanding | 65 of 121 | 71 of 121 (tools 51 → 56, model calls 0 → 2, prompts 7 and agents 8 both) |
| What it wrote | 9 sections, 583 claims, 173k characters | 7 sections, 221 claims, 75k characters |

- **Kept.** Faster, fewer calls, a third of the output, more of what matters covered, a first overview within half a minute. Bulk reading stays behind the fallback only (files no grammar parses), as phase 2 built it.
- **Reading moved, it didn't vanish.** Tokens in are about the same: area agents now read files themselves (areas 109k → 368k in). On a bigger app the old Read grows with every file in the slice, the new reading with the areas' weight, so the gap should widen; this app is small for that.
- **Two gaps found, to fix:**
  1. One heavy area (48 tools named only in agent settings) ran out of its budget and wasn't learned. A heavy area should be split, or given its budget by its entries rather than capped at `MAX_BUDGET`.
  2. Four prompts were named in no section (reported, not asked again). The coverage guard should give the area that holds them one more step to say them.

## Testing

- Unit: skeleton extraction per language the code map parses; trace matching; weights and budgets; per-area save and resume.
- Learn tests in `tests/test_agent_approval.py`'s style, with the light and strong models scripted: no Read call on the normal path; areas in weight order; a stop keeps finished areas.
- Acceptance: the made-up app learned end to end with the new path; the understanding's coverage checks unchanged.

## Order and size

Phase 0 is half a day and decides nothing by itself; phase 1 is the core and useful alone (the skeleton on the page); phase 2 is the change in shape; 3 and 4 make it visible; 5 decides whether the old path goes.

## Risks

| Risk | What we do |
|---|---|
| An area learned on demand misses something a full read would have caught | Every skeleton name must be covered by some section (as today's names-to-cover check); trace hits show what matters; "Learn this area" goes deeper on request |
| Eight areas at once hit the provider's rate limit | A setting, a backoff on 429, and the page says it's waiting on the provider |
| Apps in languages the code map can't parse have a thin skeleton | The fallback: bulk reading for those files only, in weighted areas |
| A first overview from the skeleton reads too confident | Marked "first pass" until the areas are in; it names what it's based on |
| Traces name things differently from the code | Unmatched uses are listed, not dropped; matching rules per adapter where needed |

## Open questions

1. Should the first learn of a big app stop after the heaviest N areas by default and offer the rest, or always learn every weighted area?
2. Eight areas at once: a fixed default, or set from the provider's known limits?
3. Should "Learn this area" be in What it learned only, or also in Ask AI ("learn the memory part")?
