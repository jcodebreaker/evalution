# 0012: Learn from the skeleton, read on demand

> **Status:** Proposed 2026-11-09. Plan: [learn on demand](../plans/learn-on-demand.md). Builds on [0005](0005-keep-what-it-knows-current.md) (keeping what it knows current).

A learn (`server/evalution/writer/learn.py`) runs in five stages: the code map (parsed, no model), **Read** (the light model summarizes every file of the slice, in batches), folder and app summaries (`project/modules.py`, bottom up), **Areas** (an agent per area, three at a time, four steps each, with tools that read the area's files), and the overview. On a big app this is the wrong shape:

- **Read comes before anything is understood.** On a person's app of 1,996 files, 760 were chosen as files "that do something". Read took over 26 minutes, reached 392 of them, and was cut by the chat's 20-minute limit; nothing was saved, so "go on" started it again from the first file. Commit `4d0583c` stops the bleeding (each batch kept as it finishes, a learn's own hour, 8 batches at once, at most 300 files), but the learn still reads hundreds of whole files before the first area starts.
- **Most of what Read reads, no one asks about.** Area agents already have tools to read their own files, and their brief already carries the code map's card and static facts for each file. The summaries are a head start they can do without, paid for every file whether or not an area needs it.
- **What the app actually does is used last.** The traces show which tools and prompts real use touches; today they inform the overview, not what is read first or how areas are ordered.
- **Nothing useful shows until the end.** The page shows stages and counts for half an hour, then the understanding at once.

## Decision

- **The skeleton first, with no model.** From the code map, a learn first writes the app's skeleton: its entry points, its prompts (their text), its tools (names and arguments), its model calls, its routes and where it keeps state, each with file and line. It takes seconds and costs nothing. It is shown on What it learned as soon as it exists.
- **Real use second.** The traces' digest is matched to the skeleton: which tools ran, which prompts were sent, how often. That ranks what matters.
- **No global reading step.** Areas start right after the skeleton, more at once (eight by default), each briefed with its part of the skeleton, the code map's cards and static facts, the trace hits in its files, and whatever is already known of them. An area's agent reads a file only when it needs to, within a budget of steps and tokens that grows with the area's weight (prompts, tools, state, trace hits) rather than its size.
- **A summary is a by-product.** When an agent reads a file, what it learns of it is kept under the file's fingerprint (the existing `note`), and the next learn's brief carries it. Nothing is read just in case. The bulk summarizer stays only as a fallback for files the code map can't parse.
- **Useful early, deeper as it goes.** A first overview is written from the skeleton and the traces as soon as they exist, a few minutes in on a big app, and marked as a first pass. Each area's section is saved as it finishes and shows on the page; the overview is rewritten once the areas are in. A stop, a restart or a time limit loses at most the area in flight.
- **Never learn the same code twice.** The skeleton, what an agent learned of a file, an area's section and the overview are each kept under the fingerprints of what they came from (files, and trace hits in bands), and reused while those hold. A learn after a change relearns only the areas that moved, starting from what was known of them and reading only the files that moved; with nothing moved it makes no model call. Relearning what hasn't moved is something a person asks for on purpose.
- **Order by what matters.** Areas run in order of weight. An area with no prompt, tool, state or trace hit is listed, not learned, until someone asks for it ("Learn this area") or a change touches it.

## Consequences

- `filememory.index` and `modules.index` leave a learn's critical path; the slice (`project/slicing.py`) becomes the order areas are learned in rather than a list of files to read.
- Area agents get more steps and a weighted budget; `STEPS = 4` and `PARALLEL = 3` change, and a provider's rate limit becomes the ceiling, said when it is hit.
- The understanding gains a skeleton part and a "first pass" state; the page shows the skeleton, then areas as they land, then the overview.
- Fewer model calls for a first learn of a big app, and later learns read only what moved in the areas that matter.
- An area learned from cards and on-demand reads may miss what a full read would have caught in a file no one opened. The skeleton's names (every prompt, tool and store must be covered by some area's section, as today) and the trace hits are the guard; a miss is fixed by asking for that area.

## Amended after a large app (2026-11-09, docs/plans/next-level.md C)

A learn of a large multi-agent repository behind a company gateway made 35 areas, failed 22 on gateway timeouts and then could not save. So:

- **The agent, not the repository.** Without `code.include`, areas are made only of the files that are the agent, found from the skeleton and the imports with no model (`areas.agent_scope`); the rest is `outside`, listed, learned only when asked. A learn runs at most 12 areas, heaviest first; the rest wait for the next.
- **Calls stream**, and a call a gateway cut is asked for half the output rather than made again as it was; the reason is kept on the area.
- **The profile is fitted, never refused**: past its cap the heaviest areas' sections are left out of `profile.md` and named; Details is not in it. The writer's copy is capped near 8,000 tokens.
- **An area's kept section is the one source**: every save writes back into it, so a refresh survives the next learn.
- Folder and app summaries are gone.
