# How a result is decided

The rules, in plain words. The code is `server/evalution/core/rules.py`; if the two ever differ, the code is what ran, and this page has a bug.

```
Case → play → Evidence → Checks → Judgement (one try) → Verdict (one test) → Reading (opinion) → views
```

The arrows go one way. Nothing downstream changes an outcome: a model's reading of a failure can't move a number.

## Who decides

**Code:** whether the expected steps happened, `never`, limits, state and context checks, the reply's exact words (`reply.contains`, `reply.lacks`), what tools returned (`tools.results`), why something couldn't be judged, the verdict, what can be compared, regressions and counts.

**A model:** rubric lines, the reference score, and, as its reading only, the cause, the fix and where it suspects the fault is (`app`, `test` or `unclear`). `flaky` and "the setup failed" aren't its to say: the code knows those.

## One try

Each check is a **must** or a **guide**. A guide is recorded and never decides.

1. Any must failed: the try **failed**. A real failure beats a missing grader.
2. Else any must couldn't be judged: the try is **unjudged**.
3. Else it **passed**.

## One test

Only judged tries count.

| Judged tries | Verdict |
|---|---|
| All passed | `pass` |
| Some passed | `flaky`, or `fail` if the test sets `"must_hold": true` |
| None passed | `fail` |
| None judged | `unjudged` |
| No tries | not run |

## What a pass rests on

`checked`: a must that passed came from a code check of the outcome: state, context, reply text or steps that bind. `model_only`: otherwise, a model's reading of the reply. These are shown and counted apart: "Passed, judged by a model only". A test rests on its weakest try. `none` is a pass nothing decided, a test with no checks at all whose try ran and was seen.

A passing `never` or limit doesn't make a pass `checked`: not doing something wrong doesn't show the right thing happened.

## When steps bind

Steps are a guide when the test has a state or context check or a rubric, so another route to a good result is noted, not failed. They bind when the test sets `strict`, an `exact` order or `"extras": false`, or has nothing else to decide it. A route is only called different on complete traces.

## Why something couldn't be judged

| Reason | What happened |
|---|---|
| `no_trace_source` | The target has no traces to read. `never`, context checks and binding steps can't be made. A rubric still can, so a reply-only test can pass, `model_only`, route unknown. |
| `no_traces` | The target has traces, and none arrived for this try, or its source failed when read. |
| `partial_trace` | Part of them arrived. |
| `content_not_captured` | A context check found no model call after it, none that answered, or an answering call with no captured text, a redaction marker such as `__REDACTED__` included. |
| `judge_failed` | The grader couldn't be reached, or didn't answer in a form that reads. Also the filler model, and no model set up. |
| `judge_unsure` | Both gradings of a line weren't sure. |
| `app_unreachable` | The app couldn't be reached mid-try. The run ends there. |
| `credentials_refused` | The app answered 401 or 403. The run ends there, and the next run checks the setup again even if it passed before. |
| `app_busy` | The app, or a gateway in front of it, answered 429, 502, 503 or 504. The run goes on. |
| `not_supported` | The target doesn't answer this check, or says it couldn't read what it answers about. |
| `blocked` | The app stopped to wait on something, an approval, say, and the test didn't expect it. A test that does sets `"blocked": true` on the turn: then not stopping fails the try. |
| `never_started` | Out of time, with no sign of the agent working: the target has traces, and none arrived. It may have hung before any span ended. |

An app error is not on this list: the app did that, and the try failed. Failed work is the same, with the app's own reason when it gives one. An exception out of `send` isn't an app error: it is the adapter's, and reads as such, unless it is the session going away, the app not being there, or running out of time.

## Out of time

A try that runs out of time is **unjudged** when the target has traces, none arrived, and it said nothing: there is no sign of the agent working, and that may be the app not starting it or a hang before any span ended, so no one can say whose it is. It is unjudged too when the source itself failed, as missing evidence and not as a quiet agent. Otherwise it is **failed**, for the reason "timed out". With no traces to read at all, nothing would ever show the agent working, so that's failed too. The test's `seconds` limit is that deadline, so breaking it is the same fact and isn't counted twice.

These tries count neither way, so they say so on the test's line: "Passed" over two of three such tries reads "2 of 3 tries showed no sign of the agent working".

Old runs keep the outcome they were judged with: a timeout read as failed then still is. Only its label is new.

## How it reads

Every try and test reads as one label, worked out from its checks when it is read, so old runs read the same way (`label_of`).

| Label | Means | Whose |
|---|---|---|
| Passed | Every must held | The app's |
| Failed check | A must failed, or the app answered with an error (not one below) or said its work failed | The app's |
| Timed out | It ran out of time, with the agent seen working or nothing to show otherwise | The app's |
| Setup blocked | The app couldn't be reached, refused the credentials, was busy, doesn't answer this check, stopped to wait, or showed no sign of working | Not judged |
| Adapter error | The adapter didn't read the app | Not judged |
| Missing evidence | Traces, or what they hold, didn't arrive | Not judged |
| Grader unavailable | The grader failed, or wasn't sure | Not judged |

A try holding several speaks for what the app did first, then for what stands most in the way: adapter, setup, evidence, grader. A test takes the same order across its tries, with a pass above anything unjudged, as for its verdict. The last four stay out of what's counted for or against the app.

A try also says whether it was `called`: any model call or anything the assistant said. A failed try that was never called is a different problem from one that was.

## Trying again

Two things that may just pass get one more go after a short wait, and the try keeps a `retried` entry saying what and why, shown in plain words:

- A try that couldn't reach the app at its very first message, or was answered 429 or 503, with nothing to show the app heard it: the adapter saw no message taken, and the traces show nothing. A 502 or 504 isn't sent again, since a gateway may have passed the message on. Spans of the first go don't count in the second.
- A grader call that failed on the network or a busy provider (a transport error, a 429 or a 5xx).

A failed check is never tried again, and neither is a try that got further. If the second go fails too, it reads as it would have. A failed grader call costs nothing, so a second one adds only the answer it gets.

### When it's the adapter

An adapter that doesn't read the app says nothing about the agent, so it mustn't read as if it did.

- **Before the first run** on an adapter and its settings, one real turn (`evalution check --target <name>` does the same by hand). An adapter that fails twice in a row fails the run here, in seconds, before any test is played. It costs a message or two, and passes aren't repeated until the adapter's code or settings change. `check_adapters: false` turns it off. If the app simply isn't there, that's said as the app, not the adapter. No traces is a warning, not a failure: the run goes on, reply-only tests play and are judged, and what needs traces comes out as missing evidence.
- **An app that answers first and works on** needs a way to wait for the work: the HTTP adapter refuses `background: true` without `done.poll`.
- **While a turn is out,** the traces are read every 15 seconds. If the agent answered and the adapter is still waiting `traces.stuck_seconds` later (120 by default, 0 for never), the turn is given up on. "Answered" is strict: a model call that said something and asked for no tool, in the turn's own tree (that of its first span, so not a title or a summary made on the side with a root of its own), holding the message it was sent when its request was captured, every tool its tree's calls asked for closed, with nothing after it and a closed turn around it, every span above it arrived. A model call with nothing above it shows no turn that ended, so it waits for the deadline, and running out of time is then the app's. In doubt, it waits.
- **Past the deadline,** a turn is stopped 30 seconds later even if the adapter never comes back, with or without traces.
- **After a turn,** an empty reply to a turn the agent answered, by the same reading short of the closed turn, is the adapter's, unless the turn stopped to wait: having nothing to say then is no misreading. So is a wait that never came back more than 30 seconds after the agent's answer in a closed turn, timed out or with its connection lost, such as the app restarting under a poll, if no reply was read. If one was, or the app returned a status, the error is the app's.

Any of these makes the try *Couldn't judge*, with what the traces saw it do. Two in a row end the run and show on Home: the tries left would only read nothing again.

## Work left going

`send` waits for the work to end, so a try that goes well leaves none. When a turn is given up on, runs out of time, is lost to a busy gateway or stops to wait, whether the test expected that or not (at the end of the try, if it did), and when a run is stopped, the adapter's `cancel` is called if it has one. A cancel that fails is logged and changes nothing. A try that runs out of time, or loses sight of work the app took, on an app that works on after answering (`background`), with no working `cancel`, ends the run and says why.

**On any other app with no `cancel`, what timed out may still be running.** The adapter stops waiting and the app isn't told, so the next try plays beside it, in a new session or after a reset. What the two share is the app's own, as for `isolation`, and only an app without `background` is taken to end its work with the request. Give the adapter a `cancel` if that isn't so.

A run the server restarted under reads as interrupted, says nothing of the app, and leaves what it played standing. Its open sessions are cancelled and closed, and the next run can start.

## Traces

A read is complete when the count of AI spans has held still across two reads more than `traces.settle_seconds` apart (6 by default, above OpenTelemetry's 5 s batch) and every turn that finished has an AI span of its own. Neither by 20 seconds, or a read cut by the span cap, and the read is partial. Each read looks up to that moment, so what the agent does after its reply counts, and a `never` tool called then fails the try. What it does only after the read has settled isn't seen. A source that fails with nothing read is its own status, `unread`: it says nothing of the app.

On a partial read **only what was seen decides**. A `never` call seen fails the try; not seeing one decides nothing. A word seen in a model call decides; not seeing it decides nothing. Expected calls that were seen can hold, but steps with `"extras": false` or an `exact` order are unjudged: a call not seen could be the extra one, or out of order.

The cap is 20,000 AI spans of that try, not of all traffic in the window, and it keeps the start of the try.

A context check reads the answering call: the assistant's last model call with something in its output, no helper's, from the check to the reply of the next turn. A compaction or a side call earlier in the turn decides nothing, and since a request carries the history, a fact still in the conversation shows on that call. `context_contains` holds if it has the word, `context_lacks` if it doesn't. Not captured, not finding the word decides nothing, and with calls seen but none that answered, or only part of the traces, the check isn't judged.

**Which spans are a try's** is told by ids, never by time alone; the window only bounds the read. Traces are found by:

- Carrying this try's `evalution.run` and `evalution.try`, for an app that copies the adapter's headers onto its spans. A trace marked as another try's isn't this one's, whatever its session says.
- The adapter naming them, and the session's trace values. Without marks, a value an earlier try also played on counts only from this try's first message.
- Delegated work: a trace with a span whose parent is one of the try's, or in a session that names one of the try's as its parent (`session.parent_id` or `gen_ai.conversation.parent_id`), and then the rest of that child session. It shows as helper work.

A found trace can hold other people's work too. A span in a session other than the try's counts only when it is linked as delegated work is, or is marked as this try's; one saying no session goes with the span above it. Another session that links to none of these isn't the try's, in the same trace or the same window. Where the app has session ids of its own and all that ties a trace to the try is the adapter naming it, the session the trace began in stands for the try's.

When traces are missing or cut, the try says so on the test page.

## Grading

One model call per try, with the rubric lines and the evidence. Answers are matched to lines by number or text, never by position. Only a line the grader wasn't sure of is graded again, and only that line, by a prompt told to find why it doesn't hold.

| First grading | Second | Line |
|---|---|---|
| Sure | Not asked | What it said |
| Unsure | Sure | What the second said |
| Unsure | Unsure | Unjudged, whether they agree or not |

A score from 0 to 1 is shown and never decides, unless a reference is all a test has.

## The scores

**Steps score** (stored as `tools`): the share of expected calls done, less 0.25 for each call to a `never` tool and, with `extras` off, 0.1 for each extra call, 0 at the least. It only decides when steps bind. **Reply score** (stored as `reply`): lines that held / lines judged. The grader says yes or no to each rubric line, and to the reference as one more line. Lines it wasn't sure of on both gradings don't count. The grader gives no number of its own. A test with nothing to score shows neither, rather than 1.00. Tools a target lists as `automatic_tools` aren't in the steps score, and show as "automatic" on the test page, unless the test names them.

## Money

"About" is an estimate, made before anything runs. "Spent" is what a try's traces recorded for the app's model calls: their own cost, or their tokens at the price table. It leaves out grading. A try with nothing recorded shows no figure: that doesn't mean it was free.

## Evidence

Each try is kept in `data/runs/<run>.evidence.jsonl.gz`, owner-only: the whole transcript including generated messages, tool arguments and results, what each model call was sent and said, and every state check. An item over 64 KB keeps its head and tail, how much was left out, and a hash of the whole.

The grader reads it through one function with a budget of about 24,000 characters. State and check lines, the reference and the rubric are never cut. Other items keep their head and tail with a visible `[3,200 chars cut]`, and how much was cut is recorded on the check.

## Facts and its reading

A report has two parts. **Facts** come from the checks, with or without a model: how many passed, what failed, what regressed, what couldn't be judged. **Its reading** is the model's: likely causes and what to do next, from every try, and labelled as its own.

What a model says comes as claims. Code checks that the refs exist and the quotes occur in the evidence, whitespace aside. A claim that doesn't check out is kept and marked "Not verified". A next step that cites nothing is shown as a suggestion. Its reading sees the grading that decided a line, never one it replaced. A grader's quotes get the same check. **A wrong reading with valid refs still gets through**: this shows the model looked at something real, not that it read it right.

## What can be compared

Two runs side by side (`evalution compare`, and the card on a run's page) go by each test's share of judged tries that passed. A move is said to be possible chance by Fisher's exact test at 5%, so three tries each can never rule it out and four against four just can. A test edited or judged another way is still shown, and says so, rather than being left out. This is separate from the regressions on Home, which only count like for like.

Two results of a test compare only with the same test version, rules, judge model and pass marks. Otherwise it's "test or rules changed": no better, no worse, no regression. A regression is a test that fails now and passed on another commit, comparably; a test no one could judge isn't failing.

## The record

A try keeps a list of judgements, only ever added to; the newest is current. Every view, the health page and the CLI read those, through one verdict function.

`RULES_VERSION` goes up when a rule changes what a stored try would read as. `evalution regrade <run> --rules` reads its checks again: free and offline. The checks stay the ones made then, so it doesn't compare with a run whose checks were made under today's rules, and a trend point where that changes is marked *Rules changed*. `evalution regrade <run> --judge` grades its replies again from its evidence: new spend, through an estimate and a token, and refused on a run without evidence or with no model set up. A try the grader fails on keeps what it had and is counted apart, and the command exits non-zero.

## Old runs

Runs from before this are read as today's on load, and `evalution migrate` rewrites them, keeping each original as `.bak`. Their `passed` stays as `legacy_passed`, and their judge is on record by key only, so they compare with nothing. Where today's rules read one differently it says so ("Originally failed; rules changed") with a marker on the trend. It never reads as an improvement.
