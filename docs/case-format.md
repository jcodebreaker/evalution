# The case format

What a test file may say. `server/evalution/suite/cases.py` checks every test against this when it loads and saves, and an error names the file and the field.

## Where it lives

evalution keeps tests in its database, each file's text as written. `evalution sets export [DIR]` writes them as files and `evalution sets import [DIR]` reads them back (DIR defaults to the config's `sets_dir`, `sets/`), each set replacing the one of its name; `evalution run --sets` imports before it runs, for CI:

```
sets/<target>/<set>/set.json
sets/<target>/<set>/<case>.case.json
sets/<target>/<set>/fixtures/...        files a turn attaches
```

A set, case or check id is one safe path segment: lowercase letters, digits, `-` and `_`, at most 80 characters, starting with a letter or digit.

## `set.json`

| Field | Meaning |
|---|---|
| `name` | Required, not blank |
| `about` | One line on where the set's cases come from |
| `grader` | The model key that grades: `strong` (default) or `light` |
| `filler` | The model key that writes filler messages: `light` (default) or `strong` |
| `pass_marks` | `{"tools": 0.8, "reply": 0.7}`, each 0 to 1; a key left out keeps that default. The old `pass_scores` key still reads |

## `<case>.case.json`

Only these fields are allowed. `name` and `turns` are required.

```json
{
  "name": "Short sentence-case name",
  "about": "One sentence on the behaviour it checks",
  "source": "Where it came from",
  "turns": [
    {"message": "What the person sends", "files": ["fixtures/x.csv"], "fresh": true},
    {"filler": 20, "about": "Topic of the generated messages"},
    {"expect_state": {"context_contains": "A fact the answering model call must be sent"}},
    {"break": "conversation"}
  ],
  "tools": {
    "order": "in_order",
    "extras": true,
    "strict": false,
    "expected": [
      {"name": "tool_name", "args": {"arg": {"contains": "word"}}, "times": 1},
      {"parallel": [{"name": "a"}, {"name": "b"}]}
    ],
    "results": [{"name": "tool_name", "path": "slides", "want": 2}],
    "never": ["other_tool"]
  },
  "reply": {
    "reference": "What a good reply does",
    "rubric": ["One checkable line", "Another"],
    "pass": 2,
    "contains": ["4,828.13"],
    "lacks": ["a word"]
  },
  "page": {"contains": ["Biscuit"], "lacks": ["Couldn't load"], "decides": false},
  "limits": {"tries": 3, "seconds": 120}
}
```

## Turns

At least one. They play in order, each waiting until the app is done.

| Turn | What it does |
|---|---|
| `{"message": "...", "files": [...], "stop_after": 5, "fresh": true}` | The person says something. `files` are paths inside the set folder. `stop_after` presses Stop that many seconds after sending. `fresh` asks the adapter to start this try with nothing from an earlier one; the message is never changed |
| `{"stop_after": 5}` | Alone, right after a message turn: presses Stop on that turn |
| `{"filler": 20, "about": "..."}` | That many generated messages (1 to 200) on the topic, written by the set's filler model |
| `{"expect_state": {...}}` | Checks what's true at this moment, one step per key. `context_contains` and `context_lacks` are decided from the call that answers the next message and take non-empty text. Any other key goes to the adapter's state checks and takes true/false, text or a number; on a target without them the step misses |
| `{"memory_holds": "..."}` | Reads the app's memory here, on an adapter that can show it, and the Grader says whether it holds this. A measurement stored beside the try: it never decides it |
| `{"break": "conversation"}` | The same person starts a new conversation, keeping what the app remembers of them. On a target without `conversation_break` the try fails, saying so |

## Tools

- `order`: `exact` (every expected call, in order, with nothing else; one extra call or one out of place fails the steps, whatever the score), `in_order` (default; other calls may come between) or `any_order`.
- `extras`: `true` by default; `false` makes any unexpected call fail the steps, and costs score.
- `expected`: each step is a call, `{"name", "args", "times", "in"}` (`times` is a whole number of at least 1), or `{"parallel": [...]}` with at least two calls, which take no `times`. `in` says whose call it must be: `any` (default: the assistant's or any delegated agent's), `main` (the assistant's own), or a delegated agent's name as its span gives it (`gen_ai.agent.name`, `agent.name`, else the span name).
- A tool's name is written without its MCP server prefix (`search` for `mcp__crm__search`), or with the server to say which (`crm__search`), in `expected` and `never` alike. When a try calls tools of one bare name on two servers and a step wrote only the bare name, that step isn't judged: "two tools are named search: say which".
- A delegated agent's own calls that match no step are shown and never count as extras, unless a step names that agent with `in`. Only the assistant's own extra calls cost score or fail `exact` and `"extras": false`.
- `never`: tool names. Any call to one always fails the try.
- `strict`: `true` makes the steps decide the try.

An agent varies, so `expected` is a guide: a different route to a good result is noted, not failed. The steps only decide a try when the test sets `strict`, asks for `exact` order, sets `extras` to false, or has no reply to judge (`reference` and `rubric` both empty). Otherwise put what truly must not happen in `never`, and judge the result in the rubric.

**Argument matchers**, for each value in `args`:

| Value | Passes when |
|---|---|
| a plain value, or `{"eq": v}` | The argument equals it |
| `{"contains": "s"}` | It holds the text, ignoring case |
| `{"re": "pattern"}` | The pattern is found in it |
| `{"len": {"min": 1, "max": 5}}` | Its length is in range; either bound may be left out |
| `{"any": true}` | It is there at all |

## Reply

- `reference`: what a good reply does; it counts as one more line in the reply score. With only a reference and no rubric, the score is 1 or 0, measured against the set's `pass_marks.reply`.
- `rubric`: the lines that decide, judged on the last reply of the last turn. The Grader also sees the tool calls with their results and the state checks, so a line may say what the reply must be true to ("Links both files"). Each line is one checkable fact, never a degree ("good tone").
- `pass`: how many lines must pass, from 1 to the number of lines. Left out, every line must.
- `contains`, `lacks`: exact words the last reply must and must not have, whatever the case and spacing. Code decides these, and every one must hold. An empty reply fails both. Use them for facts with one right spelling; a fact that could be put another way belongs in the rubric.

## Page

Only read on a target that plays through the app's own page (ADR 0010). There, each message turn's reply is checked to have shown on the page, and the page to have logged no console errors or failed requests to the copy, whatever the test says ([judging.md](judging.md), "The page").

- `contains`, `lacks`: words what the page showed of the last reply must and must not have, read without Markdown's marks, spacing and case aside. Code decides them. A reply that never showed fails both. The reply's own words stay `reply.contains` and `reply.lacks`, which read the reply the API returned.
- `decides`: `true` makes the page's checks decide the try. Left out, they are a guide: shown with the try, never failing it, as steps that don't bind are.
- `proof`: what a try keeps of the page, part by part, over what the target's page map keeps: `{"screenshots": "off", "video": "always"}`. Each is `always`, `failed` (deleted once a try passes) or `off` (not taken). A part left out follows the target's (by default every screenshot, and the video when a try fails). It changes nothing the test checks; the test's page sets it under "What it checks".

On a target played through its API alone the block is not read.

## What a test needs clean

`"needs_clean": ["memory"]` (also `conversation`, `files`) says a try means nothing unless it starts with none of that from earlier tries. A new session id alone only promises `conversation`. An app that can't promise what a test needs has the test left out of the run, and says why, rather than running a weaker one.

## Tool results

`tools.results` says what a call must return. Code decides it, and any call to that tool will do.

- `name`: the tool.
- `path`: where to look in the result, read as JSON (`deck.slides`, `rows.0.id`). Left out, `want` reads the whole result as text.
- `want`: a plain value or a matcher, as for arguments.

The tool not being called fails the check. With only part of the traces it can't be judged, and neither can a result that isn't JSON when a `path` is set.

## Expecting the app to stop and wait

`"blocked": true` on a message turn says the app should stop there and wait, for an approval, say. The next turn carries on from there. If it answers instead, the try fails. Without it, an app that stops to wait leaves the try unjudged. An app whose adapter can't tell has the test left out of the run.

## Limits

- `tries`: 1 to 20, default 3.
- `seconds`: a limit above 0 on the whole try. Left out, a try is still cut off at 300 seconds.

A case has no cost limit: a `usd` in an older file is accepted and not read.

## Verdicts

Tries no one could judge don't count. Of the rest, a case that passes every one is a `pass`, one that passes none a `fail`, anything between `flaky`, or `fail` with `"must_hold": true` at the top of the case. With none judged it is `unjudged`. A try passes when nothing went wrong playing it, no limit broke, every state check held, no `never` tool was called, the steps held if they decide the try (the steps score reached `pass_marks.tools`; with an `exact` order or `extras: false`, the strict rule above), and the reply passed. The rules in full are in [judging.md](judging.md).
