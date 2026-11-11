# Plan: a durable conversation that is warm before you write and keeps its words

A durable conversation is one long thread per person. An engine keeps the record. A per-person host runs a runner, and the runner drives an agent harness (pi, `--mode rpc`). This plan makes it answer faster with no loss of quality. It uses only two kinds of state: **the conversation record** (every item, never deleted) and **the long-term index** (memory claims and the knowledge index, both derived from the record). No new memory type is added.

There are two tracks:
- **W, wake ahead:** keep the assistant warm before the person writes.
- **C, context assembly:** stop bulk from crowding out the dialogue, and take memory writes off the path to the answer.

Both start from one measurement run (phase 0). After it, each phase ships on its own and leaves `main` working.

Two independent review passes were run against the code, one per track. Their corrections are folded in.

## Where it stands

| Part | Today | Problem |
|---|---|---|
| Host container | always on | none |
| Runner process | exits after 1 h with no work | first message after it pays fork, init and history load |
| Harness process (pi) | reaped after 1 h idle; runs `--no-session`, so history lives only in process memory | any exit loses the verbatim history; a restart rebuilds it from a summary |
| Prompt cache | 1 h, from the start of the last request that read it | nothing keeps it; a restart changes the request bytes, so it misses even inside the hour |
| Short-term window | the harness's in-memory history, verbatim | grows with bulk; replaced at min(0.6 × window, 120k) tokens by a code-built block (working list ≤ 4k, one line per earlier turn 4k, verbatim person words 3k, last 8k) |
| Per-turn prefix | local time, gap line, event notes, open tasks, Projects, memory profile, file handles, message blocks | the profile and Projects deltas cover the whole block, so one changed claim re-sends all of it (≤ 6,000 chars), and the old copy stays in the window |
| Tool output entering the window | shell / read ≤ 100,000 chars per stream; file get ≤ 60,000; attachment text ≤ 48,000; helper notices ≤ 12,000 | no clip on the harness bridge; clipped only when a new window is assembled |
| Reading the record back | `session_history` read/search | tool results are clipped to 300 chars; the cursor pages, it is not a get-item; recall indexes only turns before the current window |
| Memory writes | the prompt says "search memory first, then save before you reply" | extra model round trips before the answer on any turn with a profile fact; conflicts with the rule "first sentence before any tool call" |
| Background memory | upkeep after each summary fold and hourly; idle-time and nightly passes | already extracts inferred facts |

Measured on a local stack, with an eval person driven through the engine API:
- **Warm turn:** first word ~0.9–1.2 s after send; model time to first token ~0.7 s.
- **Cold conversation, container up:** ~4.0 s to first word.
- **First launch after a long idle:** 13.4 s (one run).
- **pi run directly:** ready 0.25–0.29 s after spawn; first word ~0.6 s after send.

In the counted sample, 17 of 20 cold starts came from restarts or harness switches, not idle time.

## Principles

1. **Never spend a model call on a guess.** Every wake starts processes only. A wrong guess costs RAM, never tokens.
2. **The record is the truth.** Anything kept elsewhere is a cache of it, checked against it and thrown away on mismatch.
3. **Every byte that leaves the window comes back by an exact read of the record.**
4. **Stop bulk where it enters.** Do not prune it later at cache-rewrite cost.
5. **Every injected block is a line-level delta.**
6. **No memory write sits in front of the answer.**
7. **No stored presence history.** Rhythm is read live from message timestamps in the record.
8. **Provider-neutral.** Nothing outside a harness adapter knows a vendor's cache semantics.

## Phase 0: one measurement run for both tracks

Extend the round profiler (client threads on the event streams, host status polled every 100 ms, server logs merged and corrected for clock skew).

**Wake matrix.** Five states × two history sizes (~25k and ~80k prompt) × 10 repeats:

| State | How |
|---|---|
| S1 warm harness, warm cache | two turns, 30 s apart |
| S2 warm harness, cold cache | 1 h cache off, wait 6 min |
| S3 harness reaped, runner up | harness idle timeout 60 s, wait 2 min |
| S4 runner exited | runner idle timeout 60 s, wait 2 min |
| S5 engine restart | restart through the stack script, then send |

Record for each: send → first text delta, the runner's turn-setup split, and the first call's cache read and write tokens.

Also check:
- whether the harness survives S5;
- the RSS of an idle harness, and of a harness plus three helpers;
- the system and tools digest across sessions, with the screen subscribed and not.

**Context makeup.** One scripted 40–60 turn conversation with file reads, web, mail, one attachment and one helper. Per request, report tokens by kind: system, tools, person words, assistant text, thinking, tool calls, tool results, attachments, helper notices, prefix lines, repeated profile copies. Then report:
- the share of tool-result tokens that come from results over 2k tokens;
- turns until the handoff threshold;
- per turn, whether memory calls come before the first and the last text, and the seconds they add;
- how often the full profile is re-sent.

**Quality baseline and threshold sweep.** Run the handoff probe with the offsite cases and the hint-free memory test, with the handoff threshold at 40k, 80k and 120k. This answers whether a long verbatim window beats a short assembled one.

**Done when:** one table per track (latency and cache tokens per state × size; tokens by kind; probe score per threshold) is in the PR description, written as words and numbers. Each later phase names the row that justifies it.

## Phase C1: memory writes leave the answer path, and the profile becomes a line-level delta

1. Prompt rule: save during the turn only an explicit standing instruction ("remember", "always", "never", "from now on"). Inferred facts go to the background upkeep that already runs after each fold and hourly. Remove "search memory first": near-duplicates are already reinforced server-side.
2. Profile and Projects deltas send only added or changed lines and removed ids, as the file-handles block already does. The full block goes only to a fresh window.

Not "reply first, save after": the transcript, the recall index and the outbox take the last assistant text as the answer, so any text after the save would replace it. Prompt-only ordering is also unreliable on this harness.

Cost: a helper started within the hour does not see an inferred fact. The live window has it immediately.

**Done when:** answer-ready time on memory turns matches ordinary turns; the memory eval's save rate holds and false saves do not rise; profile tokens per turn drop to the changed lines.

## Phase W1: the harness keeps its own session on disk

1. Spawn the conversation's harness with `--session-dir <host home>/sessions --session-id <conversation>-<epoch>` instead of `--no-session`. The epoch is the current view start, and it changes on handoff, manual compaction and reset.
2. On respawn for the same conversation and epoch, resume the file if it is valid. Then the first request after a restart is byte-identical to the one before it, and it reads the cache inside the hour.
3. Validity: an anchor sidecar written after each settled turn must match the record's last person message for this epoch. No unfinished assistant message may sit at the tail. Otherwise, discard the file and assemble as today.
4. Keep at most two files per conversation. Helpers stay `--no-session`.

This is not a new memory type: it is the harness's own serialization of the history already kept in memory. Nothing else reads it.

**Done when:** in S3 and S5 there is no history load, `cache_read > 0` on the first call, first word ≈ S1, and the probe is unchanged.

## Phase C2: stop bulk where it enters, with exact pointers back

1. Add an exact read to `session_history`: `get` by `call_id` with `offset` and `length`, returning the stored tool output unclipped. This comes first, because it is the recovery path for everything below.
2. Lower the entry caps:
   - shell, read and file get: 16,000 chars;
   - helper notice: 4,000 chars plus a pointer to the helper's transcript;
   - attachment text: unchanged until measured (it is the person's own input).

   Each clipped result keeps its head (and its tail, for logs) and a pointer line: `[result clipped at N of M chars: session_history get call_id=<id> offset=N]`.
3. Works the same on every harness and never rewrites the cache.

**Done when:** window growth per turn falls; turns until the threshold rise; the probe score holds or improves; the pointer-follow rate is reported.

## Phase W2: intent wake

1. Triggers: the read marker the web already sends on open, focus and visibility; the opened event of a proactive message; a debounced one-shot `warm` hint on the first keystroke into an empty composer. The hint carries no content and is not stored.
2. Handler: owner only, never an eval person, only the person's conversation, only when its runner is not connected. Then bring a runner up in the background:
   - with a per-session single-flight lock shared with the message path;
   - never taking the managed-sandbox relaunch branch;
   - at most one wake per session per 60 s.
3. After a deploy, wake runners for conversations whose owner wrote in the last 2 h, spread over 2 min, process only.

**Done when:** S4 with a read marker 10 s before send ≈ S1, and no double launch under a wake racing a message.

## Phase W3: load the history at wake, not at the first turn

When the pre-launch runs and no valid session file exists, load and assemble the history in the pre-launch, write it as a harness session file, and spawn on it. The first real turn then sends only the new message: no flattened history, and no history reads on the turn path. The first turn's cache write remains; avoiding it would need a speculative model call.

**Done when:** the turn-setup log shows no history time on the first turn after a wake.

## Phase W4: hold the conversation through the day

1. The conversation's runner and harness idle timeout becomes 12 h. Helpers stay at 1 h or less.
2. Gated on the phase 0 RSS numbers against the host's memory limit.

**Done when:** idle 3 h then send ≈ S1, with headroom under three concurrent helpers.

## Phase W5: fewer reads per turn

The engine sends the session labels and the last items with the forwarded turn. The runner then drops its two sequential per-turn reads.

**Done when:** S1 turn setup drops by the measured read time.

## Phase C3: set the handoff threshold from quality

Take the threshold from the phase 0 sweep. The rule stays size-only; only the number moves.

**Done when:** the chosen threshold is the cheapest one whose probe score matches the best.

## Phase C4: keep the working list fresh in a long window (needs a decision)

When handoffs get rarer, the model sees the working list only at the start of a window. Background folds keep advancing it, so the copy in the window goes stale.

Option: after a stored fold, the next turn's prefix carries the working-list delta (added, changed and closed lines). This pushes content into the turn beyond the standing memory block, so it needs an explicit decision. The alternative is to accept a stale list until the next window.

**Done when:** the probe's stale-list cases pass.

## Phase W6: morning wake from rhythm (only if first-of-day is still slow)

1. Read the person's own message times from the record at decision time (last 28 days). No table and no histogram store.
2. One process-only wake at the start of the earliest hour that held a first message of the day on at least half of the last four matching weekdays.

**Done when:** the p50 first word for first-of-day messages approaches S1.

## Phase C5: trim old tool output inside the harness (last resort)

Only if C2 leaves tool output dominant. The harness supports `context_edit` entries that replace a tool result's content; the latest edit wins and the edit persists in the session file. Apply at a Round boundary, only when that Round's bulk is large. The stub is the existing one-line tool digest plus the C2 pointer. Never on a warm cache mid-hour.

Cost: an edit rewrites the cache from the earliest edited entry. At the threshold that is about twice what a handoff writes. This path also exists for one harness only, which breaks the rule of one context path for all harnesses, so it needs that rule amended.

**Done when:** a paired probe against a handoff shows a gain worth the cache-write tokens per shed.

## Rejected

- **Cache refresh heartbeat.** The break-even uses the wrong probability (a return within the hour given 55 quiet minutes). Each refresh reads the whole history. Replayed calls bypass the harness's usage accounting. Vendor cache logic would sit outside an adapter.
- **Cache primer before the person writes.** It only moves the write earlier and wastes it on a no-show. The engine cannot rebuild the harness's prompt bytes, and the tools vary with screen subscription.
- **Fresh window when the cache expires.** Removed earlier by decision; needs new numbers to reopen.
- **Recall or named-person lines pushed into the prefix.** Breaks "nothing pushed beyond the standing memory block". Person notes can carry pasted third-party text, and common names collide.
- **Full-text (stemmed) record search.** Worse for exact errors and paths; the previous index was dropped at multi-GB size.
- **Assembling the history in one engine call.** Real, but W1 and W3 take it off the turn path. Revisit only if phase 0 shows wake-time assembly is slow.
- **A stored rhythm histogram.** Personal data, for a gain W4 already gives.

## Decisions needed

1. Start with phase 0 only?
2. Limit in-turn memory saves to explicit standing instructions?
3. Cap tool output at 16,000 chars with exact pointers?
4. Let the quality sweep move the 120k threshold?
5. Push the working-list delta after a fold (C4)?
6. Allow a harness-owned session file as a cache of the record (W1)?
7. Hold one harness per active person for 12 h, and send a warm hint on the first keystroke?

## Order

0 → C1 → W1 → C2 → W2 → W3 → W4 → W5 → C3 → C4 → W6 → C5.

C1 and W1 are independent, so either can go first. Every phase after 0 ships only if its phase 0 row shows the case is both common and slow.
