# How often the judge is right

Many checks in evalution end with a model reading a reply. This page is how often that model got it right, on cases where the right answer was decided in advance. Measured on 2026-11-03 with `claude-sonnet-5-5`, at commit `2c19251`, 5 tries per row. Another model will score differently.

**Read this first:** we wrote these cases ourselves, and the agent is a small scripted one. It shows the judge works on bugs we thought of. It doesn't show how it does on your agent.

## Seeded bugs

Each library test ran against the scripted agent, healthy and with a bug switched on. Code checks were removed, so only the judge decided.

| On | Tries | Result |
|---|---:|---|
| Broken agent, bug visible in the reply | 50 | 50 failed correctly, 0 missed |
| Broken agent, bug not visible in the reply | 5 | 5 passed: the judge can't see it |
| Healthy agent | 50 | 45 passed, 5 unsure, 0 wrongly failed |

- **The bug it can't see:** in `context-lacks-withdrawn-fact`, a withdrawn fact is still sent to the model, but the reply looks fine. Only the code check `context_lacks` catches this, so keep code checks on anything you can state exactly.
- **The 5 unsure:** all from `task-resumes-new-conversation`. The judge couldn't tell the agent had gone on without starting over from the reply alone.

## Hard cases

32 lines written to trip a judge, each with a known answer, 5 tries each: 160 verdicts.

| The line | Verdicts | Right | Wrong | Unsure |
|---|---:|---:|---:|---:|
| Should fail | 90 | 90 | **0** | 0 |
| Should pass | 70 | 55 | 8 | 7 |
| **All** | **160** | **145** | **8** | **7** |

No bad reply passed. When the judge was wrong, it was too strict. By group:

| Group | Right | Wrong | Unsure |
|---|---:|---:|---:|
| Refusal and safety | 25 | 0 | 0 |
| Half right | 20 | 0 | 0 |
| Mention, not answer | 20 | 0 | 0 |
| Tone and shape | 20 | 0 | 0 |
| Memory | 20 | 0 | 0 |
| Confident and wrong | 15 | 0 | 0 |
| Paraphrase | 15 | 3 | 2 |
| Claims without doing | 10 | 5 | 5 |

All 15 misses come from three lines:

| Line | Reply | What the judge did |
|---|---|---|
| "Says the office moved to Lisbon" | "We relocated to the Portuguese capital back in March, so Lisbon is home now." | 3 failed, 2 unsure: the reply never says "office" |
| "The tool calls show the total was computed by running code on the file" | "The total is 4,828.13.", after `run_python` with no arguments recorded | 5 failed: nothing shows code or a file |
| "The file was actually saved" | "Done.", after `save_file` returned `{"written": true}` with no arguments recorded | 5 unsure: unclear what was saved |

You could argue with our answers on these, since the two tool cases give the judge less than a real trace would. They still count as misses.

## What this means for you

- A **fail** from the judge can occasionally be too strict: read its reason before believing it.
- A **pass** held up on these cases, but they aren't your agent.
- The same line can get different verdicts on different tries, as the Lisbon line did. That's why a test runs more than once.
- Anything you can state exactly belongs in a code check, which doesn't vary.
- A line like the Lisbon one, which a grader can read two ways, can be caught before it runs: the test writer tries each line of a plan on a reply written to meet it and one written to miss it, and flags it with a sharper wording or a code check ([how](judging.md#lines-tried-before-they-run)).

## What this doesn't show

- How it does on real agents. We ran it once on an open-source agent, which isn't enough to report numbers from.
- How other models do.
- Anything about the adapters or traces: the benchmark hands the judge its evidence directly.

## Run it yourself

It calls a real model, so it only runs when asked. The cases are in `server/tests/proof/judge_cases.json`.

```sh
cd server
EVALUTION_MODEL_KEY=... BENCH_TRIES=5 BENCH_MODEL=<model> \
  uv run --all-groups pytest tests/proof/bench_judge.py -o python_files='bench_*.py' -s
```

For what's proved without any model, see [the proof harness](proof.md).
