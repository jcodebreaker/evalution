# Plan: limits on the test writer's loop

The test writer's chat loop must always come to a stop by itself. Removing its spending limit showed how little else stopped it.

## What's wrong

| Gap | What can happen |
|---|---|
| `MAX_STEPS` is only checked after a whole model reply | One reply asking for 500 tool calls runs all 500 |
| No time limit | 40 steps of slow calls, each model call allowed 5 minutes, can run for hours with nobody asked |
| Nothing notices repeats | The model calls the same tool with the same arguments until the step limit, and "go on" starts it over |
| `_trim` only cuts tool results | One chat per app keeps growing, until the text alone is too big for the model and every message fails |

## What changes

All in `server/evalution/writer/agent.py`.

1. **Steps:** count each tool call and stop as soon as `MAX_STEPS` (40) is reached, including partway through a reply. Calls that didn't run are answered as not run.
2. **Time:** stop a turn after `MAX_SECONDS` (20 minutes), including during a slow model or tool call.
3. **Repeats:** stop when the same call with the same arguments comes 3 times in a row, without running the third.
4. **History:** when cutting tool results isn't enough, drop the oldest exchanges whole, and tell the model that earlier conversation was cut. The chat shown on the page keeps everything.

Each stop leaves the session idle with a message in the chat saying why, and the person decides what happens next. Stop keeps working as it does now.

## What doesn't change

- No spending limit comes back. What's been spent stays visible.
- A person can always say "go on", so a limit never ends the work for good.
- Limits are constants for now, not settings. They become settings if someone hits them in real use.

## How it's tested

With a scripted model, offline: a reply with too many calls, a model that repeats itself, a model that never answers, and a history over the limit, plus a normal turn that hits none of them.
