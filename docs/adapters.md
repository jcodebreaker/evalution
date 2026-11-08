# Writing an adapter

An adapter is how evalution plays a conversation on your app. Most apps don't need one: the `http` adapter covers a chat endpoint, including one that makes its own sessions or answers before its work is done ([Connecting your agent](../README.md#connecting-your-agent)). Write one when your app takes more than that, or when you want it to answer state checks.

```sh
evalution adapter new my_app                   # writes data/adapters/my_app.py, never over a file that's there
evalution adapter test my_app --target my-app  # its contract over the real app
```

The starter works as it stands against an app with the routes it names, and each place to change is marked `CHANGE`. It's one module: `create(target) -> Target`, a class that declares its `Capabilities`, and `INFO`, an `AdapterInfo` ([`adapters/base.py`](../server/evalution/adapters/base.py) lists every field). evalution finds it in `data/adapters/` or wherever `adapters_dir` says, with no edit to evalution. That code runs, so only your config can point at it, and connecting a project never does.

## Secrets, and when your own adapter breaks

An adapter reads a secret its target names (an option ending in `_env`) with `target.keys.get(name)`: the environment variable of that name, else the secret stored when the target was connected from the page. Never read `os.environ` yourself; hosted, the server's environment is not the workspace's.

An adapter of your own that stops importing after an update (it used something evalution no longer has) is not skipped quietly: `serve` says so when it starts, `evalution doctor` names the error and the file, and the app's page shows "Its adapter doesn't load" with the error. None of its runs can start until it loads.

## The turn contract

`send(session, message, *, files, stop_after, deadline) -> Turn` is one person's message.

- **Done means done.** It returns when the app has finished, not when the first words come. An app that answers "Queued!" and works on: `send` waits for that work to end, returns the outcome, and the adapter declares `background`. A later try must never start on top of an earlier one's work.
- **Background work.** With `background` declared, evalution knows a wait after an answer has missed nothing, and only the deadline ends it. Without it, a turn still out long after its traces show an answer is taken to have misread the app. A state read waits out background work itself.
- **The deadline** is an absolute `time.time()`. At the deadline `send` returns with `Turn.error` set. One that doesn't is given up on 30 seconds later.
- **Failures are answers.** An app that answered badly or couldn't be reached is `Turn.error`, with `Turn.failure` from `failure_of(exc)`: it also tells credentials the app refused (401, 403), which end the run, from an app or gateway that's busy (429, 502, 503, 504), which isn't the app's failed check. Set `Turn.heard` when the app took the message and what failed came after, so it isn't sent twice. `send` raises only `TargetGone`, when the session itself went away. Anything else it raises reads as the adapter's fault.
- **Asked for what it can't do**, without `files` or `stop` declared, a turn ends with the error `NO_FILES` or `NO_STOP`, and nothing is sent.
- **The steps aren't asked of the adapter.** They're read from your app's traces, found by `session.trace_attribute` = `session.trace_value`.

## The session lifecycle

- **`open(run)`** gives a new session. Every session needs its own `id` and `trace_value`, or tries read each other's spans. With `reset` declared the runner keeps one session per slot and resets it before every try. Without it, it opens one per try **under the same `run`**, so the same `run` must still give a new session. Use a random part, and a prefix your `INFO.own_traffic` names, so eval traffic stays out of digests of real use.
- **`reset(session)`** clears what the next try would otherwise find.
- **`probe(session, check)`**, with `probe` declared, reads a state check as the session stands, once and without waiting. It's called before a try's first message: a `*_contains` check that already holds leaves the try unjudged, as leftover state from an earlier one.
- **`break_conversation(session)`** starts a new conversation for the same person, keeping what the app keeps across conversations. It may change `session.trace_value`; a try reads the spans of every value it had.
- **`watch(session, since, deadline=...)`**, with `watch` declared, waits for the turn something else sent in the session at `since` (the app's own page, ADR 0010) and returns it exactly as `send` would: when it's done, or at `deadline` with `Turn.error`, and `Turn.heard` on an error, since the message was sent. The http adapter declares it when it has `done.poll`.
- **`close(session)`** ends it. It's also called for sessions an earlier server process left open, rebuilt from `Session`'s fields, so it can't rely on anything in memory, and `Session.extra` must survive JSON.
- **Never delete by default.** Leaving a session behind costs little and losing someone's data can't be undone. Delete only what the adapter can prove it made, like a namespace holding the eval user's own name. Never act on a real person's data, and never reuse a real session id.

## The test person

A person can choose who plays the tests: a name, a language and a time zone (`Person` in [`adapters/base.py`](../server/evalution/adapters/base.py)). An empty field is the adapter's own choice.

- **`persona(person)`** is called before a run opens its sessions, and the person stays until it's called again. The default keeps it as `self.person`; `open` plays as that person.
- **Declare what you honor** in `Capabilities.person`, any of `name`, `language` and `timezone`, and only what your app really takes. `persona` never raises for the rest: it ignores them. Nothing declared means the adapter can't take a test person.
- **`new_every_try`** is on by default. Off, reuse one person across a run's tries where you can; you may still need a new session id per try.
- **A session keeps the person it opened as.** Keep what you used in `Session.extra` if a later call needs it.
- **The `http` adapter** fills `{person_name}`, `{person_language}` and `{person_timezone}` in its templates, like `{session}`, and honors the ones its templates use.

## What a new session starts clean of

`isolation` and `clean` promise what a try can rely on finding nothing of from earlier ones. There are three kinds:

| Kind | What it means |
|---|---|
| `conversation` | No earlier messages in this chat |
| `memory` | Nothing the app remembers across chats |
| `files` | Nothing an earlier try left in the person's files |

| `isolation` | Means | Starts clean of, unless `clean` says otherwise |
|---|---|---|
| `Isolation.NONE` | Tries share everything | nothing |
| `Isolation.SESSION` | A new session id, and what carries over is up to the app | `conversation` |
| `Isolation.IDENTITY` | A new user, who has nothing of anyone else's | all three |

Promise only what's true: where the promise fails, a pass can rest on what an earlier try left behind. `evalution check --target <name> --isolation` tries one case of it, a word said in one session and asked for in another, and spends two messages.

A proven isolation plan ([ADR 0009](adr/0009-isolation-is-found-and-proven.md)) overrides `isolation` and `clean` for the stores it covers. `evalution isolate --target <name>`, or *Find and prove isolation* in Connect, finds where the app keeps what it remembers on evalution's own copy of it, writes a plan to reset each place, and rehearses it there. What it covers is what a try starts clean of, whatever the adapter promises; for anything it doesn't cover, the adapter's promise still holds. A commit that changes a file the plan's evidence cites marks it out of date until it's proven again. An adapter's `INFO` may add hints about where its framework keeps state, so vendor knowledge stays behind the adapter.

On a copy, evalution points the adapter at the copy's address: an adapter made with the copy's `url` in place of the target's (its `INFO.on_copy`, which by default sets `url` and nothing else). So an adapter of your own reaches the app only through `options["url"]`, or gives `on_copy` that moves whatever else names an address; anything it can't move exactly raises `ValueError` saying what, and the run is refused before a copy starts rather than sending tries to your app while labelled as played on the copy. The `http` adapter's routes (`send`, `open`, `done.poll`, `cancel`, `health`) play on the copy when they start with `{url}`, or are written in full on the target's own `url`, same scheme, host and port, which is moved to the copy's with the path kept. A route on any other address, `127.0.0.1` for a `url` of `localhost` included, or any written in full on a target with no `url`, is refused by name: start it with `{url}` instead.

## What `adapter test` checks

It looks at the adapter, never the agent: what your app answers isn't judged, only that the adapter read an answer. Before spending anything it runs the adapter's own `check`, then tells you how many messages the sequence sends and asks (`--yes` to skip that). That's 2, plus one each for `reset`, `conversation_break`, `files` and `stop` if declared. It refuses while a run is going.

| Step | Fails when |
|---|---|
| Declares what it implements, exports `INFO` | A capability and its method don't match, or there's no `INFO` |
| `open` gives unique sessions | Two sessions share an `id` or a `trace_value` |
| Opens as a test person, if `person` is declared | `open` raises after `persona(Person(name="Contract Check", ...))`, or it declares a field that isn't one. What the app makes of the person isn't judged |
| `send` returns a reply and ends | It raises, ends on an error, or reads an empty reply |
| The trace value finds spans | No AI spans carry the session's value. Skipped while no traces have come from the target |
| Each state check declared | It raises, or says it couldn't read (`observed=False`). What it finds isn't judged |
| Each `probe` if declared | The same, on a session that hasn't sent anything yet |
| `sections`, `files`, `stop`, `reset`, `conversation_break` if declared | It raises or refuses, or the session doesn't work after |
| The deadline holds | `send` is still out 30 s past a 5 s deadline, or returns late with no `Turn.error` |
| `close` doesn't raise | Called with a `Session` rebuilt from its fields, for every session opened |

An endpoint that isn't there reads as a clear error: `state check memory_contains: not supported by this app: POST http://.../memory answered 404`. Declared and not tried, like `parallel`, or a capability newer than the kit, is listed as skipped. Steps passing mean the adapter did what it says on this app, this once. They don't mean a pass in a test is right.

The same check from your own tests:

```python
from evalution.adapters.contract import run

report = await run(target, INFO, on_step=print)
assert report.ok, report.steps
```

## Which versions of your app

If your app says its version, the adapter can say which it was written for, so a wrong one reads as a setup problem rather than as odd failures in your tests:

```python
from evalution.adapters.contract import version_part

async def _check(client, options, lookup):
    answer = await client.get(f"{options['url']}/health")
    return version_part("The app", answer.json()["version"], INFO.supports)

INFO = AdapterInfo(..., check=_check, supports=">=1.2,<2")
```

`evalution doctor` and `adapter test` then say which version it is and what's supported, and `adapter test` stops before spending anything.

## What the built-in adapters promise

Generated from each adapter's `Capabilities` and `INFO`; a test fails when this table drifts. To update it: `UPDATE_DOCS=1 uv run --all-groups pytest tests/test_adapter_conformance.py`.

<!-- matrix:start -->
| Adapter | Isolation | Clean of conversation | Clean of memory | Clean of files | `reset` | `conversation_break` | `sections` | `files` | `stop` | `parallel` | `probe` | `background` | `cancel` | `blocks` | `deletes` | `watch` | State checks | Test person | App versions |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `http` | session | yes | no | no | no | no | no | no | no | yes | no | no | no | no | no | no | none | none | any |
| `langgraph` | identity | yes | yes | yes | yes | yes | no | no | yes | yes | yes | no | yes | yes | no | no | `checkpoint`, `memory_contains`, `memory_lacks`, `messages_trimmed`, `state_contains` | none | any |
| `letta` | identity | yes | yes | yes | yes | yes | no | no | yes | yes | yes | no | yes | yes | no | no | `memory_contains`, `memory_lacks` | none | any |
<!-- matrix:end -->

What each one does to deliver those: [architecture](architecture.md), and each module's opening comment.
