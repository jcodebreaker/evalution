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

## Amended: facts first, and a learn is a job (2026-11-09, docs/plans/next-level.md C1, C10)

- **Facts first, areas when needed.** "Useful early" is now the facts level: the code map, the skeleton, real use and at most one light call for the card's one line make a saved understanding by themselves (the card, a diagram from the skeleton's agents, tools and stores, the tools and state lists, `overview.state: "facts"`). A first learn stops there. The areas are learned when something asks: a person (Learn this area, Learn everything), the test writer about to rely on one (`read_understanding(area=...)` learns it and waits, bounded), or a target that sets `code.learn: full`. "Learn again" learns the facts and the areas learned before that moved.
- **The first overview is gone.** Its calls went to a page nobody had read yet; the facts card stands in until an area's section is in, and the overview is written from the areas only then.
- **A learn is a background job**, one per target, with its own record and progress (`project/learning.py`), not a chat turn: no wall-clock limit and no "say go on". It ends when its areas are done or their budgets spent; it outlives the chat; a Stop keeps what was learned; a server restart marks it interrupted and "Go on" starts a job that learns only what is left. The chat that asks for one says one line that points to What it learned.

## Amended after measuring what is learned (2026-11-09)

Measured on four real repositories, with no model: the 2,000-file watch cap cut by name before any scope was chosen (a repository of 3,923 source files never had its agent's `tools/` and `chat/` folders watched); routes registered inside a factory function were never seen (0 of 262); CSS class strings, licence text, error messages and coding-assistant notes counted as prompts; and the scope was ten times the hand-picked agent (658 files against 123), its first areas plumbing. So:

- **The scope is chosen from every source file.** The watch cap is a safety ceiling of 10,000 files, above the largest measured; past it, files rank by what their path says of an agent, never by name, and what is left out is still counted. A file git has just listed is read without asking git again whether it is ignored, which was most of a cold map's time (the largest measured, 3,920 files: about 30 s cold, 10 s warm on a quiet machine).
- **Routes and tools registered one level inside a function** are definitions.
- **Prompts are what a model is told**: a constant holding one word, CSS classes, licence or disclaimer text, an error message and its fix, or SQL is not one; `AGENTS.md`, `CLAUDE.md`, `SKILL.md`, `.claude/**` and `.agents/**` are only when the app's own code names them. An exception class is never an agent.
- **The scope starts from prompts, tools, model calls and agents that are one** (made by an agent's class or factory, named `…Agent`, or defined by settings), never from routes, state or entry points; imports are followed one step, a second only into folders holding two core files or more, with a package's front file and a file importing three core files or more (a registry, the loop that runs them). A module named in a string (`"pkg.inner.pi_executor:Runner"`) that is exactly one file of the repository is an import. A prompt, tool, agent or model call weighs 3 in an area, the rest 1, so those areas are learned first. On one measured repository: 264 files in scope, 55 of the 59 hand-picked code files.
- **A folder holding one folder is split below it**, and an outside area never takes the name of an area learned in pieces.
