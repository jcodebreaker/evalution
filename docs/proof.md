# The proof harness

Whether evalution catches broken agents, and where it passes without having looked. It runs offline with no model: `server/tests/proof/`.

```sh
cd server
uv run python -m pytest tests/proof -q -rx
```

The agent is a small rule-based assistant with fault switches. Each library test is filled through the real library path and played by the real runner. Spans go through the real OTLP decoder, built-in store and `traces/view.py`, in the OpenInference and GenAI shapes (the code-decided rows run in both). Time is simulated, so nothing sleeps. The judge is a stub that reads no evidence.

## What the matrix shows

The judge passes every line, on the healthy agent and the broken one alike, so a failure can only come from the code.

| Library test | Decided by | Healthy | Seeded bug | Result on the bug |
|---|---|---|---|---|
| compaction-keeps-early-decision | code: `context_contains` | pass | `summary_drops_early_facts` | fail, caught |
| context-lacks-withdrawn-fact | code: `context_lacks` | pass | `withdrawn_fact_still_sent` | fail, caught |
| forget-on-request | code: `memory_lacks`, `context_lacks` | pass | `forget_says_done_only` | fail, caught |
| forget-on-request | code: `context_lacks` | pass | `withdrawn_fact_still_sent` | fail, caught |
| memory-recalls-fact | code: `memory_contains`, reply words | pass | `forgets_to_store` | fail, caught |
| correction-replaces-fact | code: reply words | pass | `correction_keeps_old_fact` | fail, caught |
| long-chat-keeps-name-role | code: reply words | pass | `summary_drops_early_facts` | fail, caught |
| preference-across-conversations | model | pass | `forgets_to_store` | not provable here |
| recall-right-conversation | code: reply words | pass | `forgets_to_store` | fail, caught |
| task-resumes-new-conversation | model | pass | `forgets_to_store` | not provable here |
| withdrawn-instruction | model | pass | `withdrawn_fact_still_sent` | not provable here |

On the model-judged rows the broken agent passes too, because the stub was told to pass it. All those rows show is that the bug is visible in the reply the judge is given.

`test_vacuous.py` holds the false passes and false fails found in review, each asserting the correct behaviour. All but one now hold. The one left, a pass resting on what an earlier try left behind, is a strict xfail, so a fix flips it loudly; `-rx` gives its reason.

## What it can't show

- That any model catches any of these bugs ([accuracy.md](accuracy.md) measures that). Six of the ten library tests, and the reply half of the other four, rest on a model's verdict, and no model runs here.
- That a real agent behaves like this one. Its bugs are seeded by whoever wrote the checks, in the shapes the checks look for.
- Anything about the adapters that talk to real apps, Phoenix as a source, or how real instrumentation exports spans.
- That a passing test means a healthy agent. The xfail is one way it doesn't, and a pass judged by a model only rests on that model.
