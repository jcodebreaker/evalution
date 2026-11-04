# What testing Letta found

Notes from running evalution against a real Letta Code App Server on 2026-11-03, version 0.34.23. Letta's source was at 0.34.39 by then, and these were not rerun on it, so any of this may already be fixed. Nothing here has been reported to Letta.

**Read this first:** the samples are small, and the agent ran with `claude-sonnet-5-5`. Treat these as leads to confirm, not as measured rates.

## Main finding: "saved" memory doesn't reach the next conversation

The agent writes the fact to `MEMORY.md` and tells the person it's saved, but nothing commits the file. A new conversation for the same agent then behaves as if the fact was never saved.

Letta keeps each agent's memory in a git repo. A new conversation seems to load committed memory only. That's inferred from the results below and from the words "edited and committed at revision" in Letta's source, not from reading how the prompt is built.

| Setup | Agent said saved | Next conversation applied it |
|---|---|---|
| Letta's default tools, no approval involved | 2 of 2 | 0 of 2 |
| File tools only, uncommitted | 3 of 3 | 0 of 3 |
| The same 3, after committing by hand | | 2 of 3 |

The test: state a lasting preference ("from now on, end with a line starting 'In short:'"), start a new conversation, and ask for something the preference applies to.

In the default-tools runs, `git status` in the memory folder showed `M MEMORY.md` after the agent said it had saved. There was 1 commit and the agent made no attempt to commit.

Two more default-tools runs tried to write through Bash. Those approvals were denied, and the agent correctly said nothing was stored, so they aren't counted.

Not checked:
- Whether something commits the file later, after the conversation ends.
- Whether a person who approves shell commands gets a commit. All approvals were denied here.

To reproduce:
1. Start an App Server and create an agent.
2. Send a lasting preference and wait for the agent to say it saved it.
3. Run `git status` in `<state dir>/memfs/<agent>/memory`.
4. Start a new conversation for that agent and ask for something the preference applies to.

## Smaller findings

- **Fake tool calls:** with `client_tool_allowlist: []`, the model often typed `<invoke name="Bash">` as its reply, on the first turn of all 8 rounds in the last check. Its prompt probably still describes those tools, but that wasn't confirmed.
- **Pointless approval:** Letta sometimes asks for approval of a tool that isn't on the turn's allowlist (4 of 12 runs). Approving it returns `Tool not found: Bash`. The allowlist itself held.
- **One unexplained timeout:** one turn hit the 150 second limit with tools off. It did not come back in 8 more rounds.

## The eval run

Run `20261103-134543-143f`, file tools only, 6 cases with 3 tries each. 4 of 6 passed. The adapter has changed since, so this run is not current.

| Case | Result | Note |
|---|---|---|
| `preference-across-conversations` | fail, 3 of 3 tries | Fits the main finding, but the memory folders from this run weren't inspected |
| `task-resumes-new-conversation` | fail, 3 of 3 tries | The agent saved nothing when told "I'll come back". A weak spot, not clearly a bug |
| `correction-replaces-fact` | pass, 1 try blocked | |
| `memory-recalls-fact` | pass, 1 try blocked | |
| `long-chat-keeps-name-role` | pass | |
| `recall-right-conversation` | pass | |

The blocked tries came from the adapter, not Letta, as far as checked. It gave up when asked to approve a tool outside the turn's list, and now denies the request and carries on. One blocked try was traced to this; the other wasn't captured but is probably the same.

## What held up

- The contract kit passed for `parallel`, `cancel` and `blocks`.
- 3 agents running at once saved and recalled their own facts correctly.
- Stop from another socket ended a turn in 0.1s, and the next turn worked.
- Deleting an agent worked, although its memory folder stays on disk.

## Limits of the adapter

- **Memory checks read committed memory:** `memory_contains` and `memory_lacks` now read each file at `HEAD`. On the real server, a fact only written failed `memory_contains` with "1 more written but not committed", and passed after a hand commit. That was 1 agent, and in that run its reply did say it hadn't committed, so agents don't always hide this.
- **`memory_lacks` has a gap:** a committed file deleted but not committed isn't listed, so the check can't see it.
- **No traces, no cost:** the adapter sends no traces, and the run recorded 0 tokens and $0.00. The real spend is unknown; the estimate before the run was about $10.87.
