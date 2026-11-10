# Benchmarks

A benchmark is a public golden dataset: questions with known answers. evalution ships [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (Wu et al., ICLR 2025, MIT) with the questions the page runs in this repository. Benchmarks stay apart from your own tests: their scores never count in Home's numbers.

## On the page

| Dataset | Size | Questions | Messages each | What it tests |
|---|---|---|---|---|
| Short histories | `short` | 30 | About 10 | Whether saved memory alone can answer: the conversation is cleared between chats |
| Middle histories | `middle` | 10 | About 55 | Whether it can still answer once older chat is folded away, in one conversation |
| Quick check | `quick` | 5 | About 11 | Only whether a run works on the app: one answer moves its score 20 points |

Each card says how many questions it has and about how many messages each sends, read from the bundled file. The confirm step after Run gives the estimate for the whole run.

Press Run on a card; a confirm step shows the estimate first. These questions are in the repository (`server/evalution/benchmarks/longmemeval_data/`, with the dataset's MIT license), so they need no download, and they stay the same so runs compare. Middle is our own variation, so its scores don't compare with published ones. A question the app broke off on (it timed out, crashed or couldn't be reached) counts as wrong, as it would for a person waiting on an answer; the run says how many. Only a question the grader couldn't judge stays out of the numbers.

The page shows accuracy with its 95% range, says how many questions timed out and were counted as wrong, and says the numbers are a sample, not comparable with published scores. A change in points against the run before shows only when it's real (see The numbers).

### The short size

`short` is 30 questions from LongMemEval's `longmemeval_oracle.json`, whose histories hold only the sessions with the answer (the evidence sessions), so a run stays affordable: 307 messages in all. `bundle("short", data_dir)` in `server/evalution/benchmarks/longmemeval.py` picks them with the same code as the other sizes: the playable questions are grouped by kind, with the unanswerable ones as a group of their own, each group is sampled with `random.Random(0)` (`_SAMPLE_SEED`), and the groups take turns until 30 are chosen. Run it again on the same file and it picks the same question ids, in the same order.

| Kind | Questions |
|---|---|
| Single-session facts (`single-session-user`) | 5 |
| Preferences (`single-session-preference`) | 5 |
| Across conversations (`multi-session`) | 5 |
| Dates and order (`temporal-reasoning`) | 5 |
| Updated facts (`knowledge-update`) | 5 |
| Unanswerable (abstention): 3 `multi-session`, 1 `knowledge-update`, 1 `single-session-user` | 5 |

The source file is `longmemeval_oracle.json` from the benchmark's dataset page, `https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json`, linked from the LongMemEval repository. The copy it was made from has 500 questions and sha256 `821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c`. The same file rebuilds `quick.json` byte for byte. Only the chosen questions are kept (`longmemeval_data/short.json`, about 90 KB, with what the person said and nothing of the recorded replies); the full file isn't in the repository. With a downloaded copy, `LONGMEMEVAL_ORACLE=<path> uv run pytest tests/test_longmemeval_short.py` checks its sha256 and that it rebuilds `short.json` exactly. LongMemEval is © its authors, Wu et al., under the MIT license, in `longmemeval_data/LICENSE`.

Thirty questions is still a sample: one answer moves the score about 3 points, and its 95% range is wide. Its scores aren't comparable with published LongMemEval scores, which use all 500 questions, their own judge and the full histories.

The questions about what the assistant itself said (`single-session-assistant`, 56 of the 500) aren't in it. They ask about a reply recorded in the dataset, and a live app's replies are its own: the only way to play them would be to send the recorded replies as if the app had said them, and evalution sends only what the person said.

A laptop that sleeps stops a run, and closing the lid sleeps it even with sleep blocked.

## From the command line

The full-length sizes (`s`, `m`, `oracle`) and the plan below aren't on the page. They download the full data, and a long question sends about ten times as much as a middle one.


A benchmark says what it needs, not how to play it, because apps differ. One has a new conversation per chat; another has one conversation per person forever.

1. **Plan.** An agent reads what evalution learned about the app and how its adapter plays it, then picks from named options: how one session gives way to the next, how long the history is, and how many questions. It quotes the notes it relied on.
2. **Check.** Code refuses a plan whose quotes aren't in the notes, an option that isn't one, or a choice that can't measure anything, such as a short history in one conversation, which is still in front of the model. The agent may also say the benchmark can't be measured on this app.
3. **Try.** A few real messages show whether the plan plays, whether memory can be read, and how long a message takes.
4. **Approve.** You read the plan and approve it. Nothing long runs before that.
5. **Run.** It plays as fixed steps with no agent involved, so runs under one plan compare. The plan is stored with each run. If what evalution knows of the app changes, the plan is marked stale.

```
evalution benchmark plan --target my-app
evalution benchmark approve --target my-app
evalution run --target my-app --set longmemeval-s-same
evalution benchmark report <run>
```

## The numbers

| Number | How it's worked out | Who decides |
|---|---|---|
| Answer accuracy | questions right / questions judged, per kind of question; unanswerable questions count, right when it said it didn't know, as in LongMemEval's own scores | The grader says yes or no against the known answer; code does the arithmetic |
| Task average | mean of each kind's accuracy | Same |
| Memory write recall | known answers in committed memory / questions measured | The grader says yes or no on the memory, read just before the question. It never decides a question |
| Abstention | said it didn't know / unanswerable questions | The grader says yes or no |

Accuracy comes with a range, `range: [low, high]` in the report: the Wilson 95% interval of right / judged. A few questions give a wide range, so the score is a sample, not a measurement to quote. Against the run before of the same size, `change_is_real` is true only when both had the same judge and the earlier accuracy (`previous_accuracy`) lies outside this run's range; the page shows a change in points only then.

The grader reads a benchmark question with its own prompt (`BENCHMARK_INSTRUCTIONS` in `judging/rubric.py`): the question, the known answer and the reply, nothing of the history or of what the app did, and without the rule for your own tests that a figure in a reply is real only if a tool call made it, since an answer from memory has none. Its prompts are stamped apart (`prompt_hash(benchmark=True)`), so a benchmark run under them doesn't compare with one graded before, and your own tests' stamps don't move.

A line the grader stays unsure of after two gradings goes to the stronger model for a final answer, on benchmarks only, and the question says so. If that fails too, it reads "Unsure", with which way it leaned, and stays out of the numbers.

An agent that remembers nothing scores well on abstention, so read it beside accuracy. Retrieval recall needs an adapter that shows what the agent retrieved, in order, and none does yet.

The sentence on top of a run comes from these numbers, with no model involved, and it says so when there are too few to go on.

The questions about what the assistant itself said are left out, because a live agent won't repeat the recorded replies. Scores compare with LongMemEval's published ones only with the same judge and the same way of feeding history.
