# Benchmarks

A benchmark is a public golden dataset: questions with known answers. evalution ships [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (Wu et al., ICLR 2025, MIT) with the questions the page runs in this repository. Benchmarks stay apart from your own tests: their scores never count in Home's numbers.

## On the page

| Dataset | Questions | Messages each | What it tests |
|---|---|---|---|
| Short histories | 5 | About 11 | Whether saved memory alone can answer: the conversation is cleared between chats |
| Middle histories | 10 | About 50 | Whether it can still answer once older chat is folded away, in one conversation |

Press Run on a card; a confirm step shows the estimate first. These questions are in the repository (`server/evalution/benchmarks/longmemeval_data/`, with the dataset's MIT license), so they need no download, and they stay the same so runs compare. Middle is our own variation, so its scores don't compare with published ones. A question that broke off (the app hung, the computer slept) reads as not judged rather than wrong.

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
| Answer accuracy | questions right / questions judged, per kind of question | The grader says yes or no against the known answer; code does the arithmetic |
| Task average | mean of each kind's accuracy | Same |
| Memory write recall | known answers in committed memory / questions measured | The grader says yes or no on the memory, read just before the question. It never decides a question |
| Abstention | said it didn't know / unanswerable questions | The grader says yes or no |

A line the grader stays unsure of after two gradings goes to the stronger model for a final answer, on benchmarks only, and the question says so. If that fails too, it reads "Unsure", with which way it leaned, and stays out of the numbers.

An agent that remembers nothing scores well on abstention, so read it beside accuracy. Retrieval recall needs an adapter that shows what the agent retrieved, in order, and none does yet.

The sentence on top of a run comes from these numbers, with no model involved, and it says so when there are too few to go on.

The questions about what the assistant itself said are left out, because a live agent won't repeat the recorded replies. Scores compare with LongMemEval's published ones only with the same judge and the same way of feeding history.
