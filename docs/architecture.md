# Architecture

evalution tests agent apps (**targets**). It plays cases through an **adapter**, reads what happened from its **traces** (evalution's own Phoenix, its built-in store, or the target's Phoenix; see "Traces"), then grades, analyses and reports. This page is how it is built.

## Words

One word per concept, on the page and in every message a person reads (errors, the test writer's replies). The code and the route paths keep their older names; nothing was renamed in the API, so this table maps one to the other.

| Product word | What it is | In the code and API |
|---|---|---|
| **App** | the agent app being tested | target (`/v1/targets/{target}`, `?target=`) |
| **Test** | one scripted conversation and what it expects: its messages, tool calls, reply rubric, limits | case (`*.case.json`, `/cases/{case}`, `CaseRow`, `CaseDetail`) |
| **Test set** | a named group of tests with its pass marks and Grader | set (`set.json`, `/evals/sets/{set}`) |
| **Run** | playing some or all tests of a set, each a number of times | run (`/evals/runs/{run}`) |
| **Try** | one play of one test in a run | try (`Try`, `?try=`) |
| **Test writer** | shown as "Ask AI"; the agent that answers from runs and traces and writes tests: the only way to make one, from words, a past conversation or a suggested test, always through plan → approve → save | the evals agent (`agent.py`, `/evals/agent/sessions`) |
| **Suggested tests** | tests the test writer proposes for this app, the library's included; the only place the library shows | understanding `starters` |
| **Pass mark** | the tool-call and reply score a try needs to pass, set once per test set | `pass_marks` (was `pass_scores`, still read) |
| **Finding** | why a test failed in a run, written automatically after every run with a failure | try `analysis`, `CaseDetail.finding` |

## Layout

```
server/            Python 3.12, uv, FastAPI. Package `evalution`.
  evalution/       Each layer imports only from below it (ADR 0006)
    cli.py         `evalution serve`, `evalution run --target --set`
    upgrade.py     `evalution backup`, `upgrade`, `restore`: what is kept, across new code
    api/           the HTTP API and the page: app.py create_app and the route order, deps.py what routes share
      traces.py targets.py settings.py connect.py   the /v1 routes by area, in the order they match
      understanding.py health.py sets.py tests.py runs.py writer.py
      plans.py     a test's plan, for the tests and runs routes
    writer/        the test writer: agent.py its sessions and loop, prompts.py its instructions, schemas.py what the model is told of its tools, tools.py what they do, sessions.py the session store, draft.py a case from a try or a conversation
    connect/       connecting a target: connect.py by its settings, discover.py by its project folder
    runs/          play.py one try, collect.py reading its spans back, estimate.py what a run costs, runner.py the one running task
    review/        findings.py a run's analysis and report, health.py the Home page, testplan.py what a plan will do
    project/       what is known about one target
      drift.py impact.py   noticing a change; what a code change means for the tests
      understanding/       what it understood about the app
        document.py computed.py   what the agent writes and its checks; what the engine computes
        steering.py rendering.py store.py   steers and the overlay; profile and card as text; the writes
      knowledge.py imports.py hooks.py   keeping that current: links, the import graph, git hooks
      history.py library_fit.py   what they need of the runs; the library as it applies to a target
      profile.py           what is kept on each target (kind `profile`), the updates setting, the profile and its versions
    targets.py     the configured targets: adapter, trace source, code, storage
    judging/       steps.py the tool-call grade, checks.py what code decides of a try, render.py what the Grader reads, rubric.py the Grader, claims.py a model's claims against the evidence
    results/       store.py stored runs, stop requests and the run lock, evidence.py each try's evidence, record.py what readers ask of a run, migrate.py older runs as today's, views.py what the routes return of them
    suite/         cases.py what a set and a case may say, checked; store.py sets, cases and their files as documents (kinds set, case, set-file), and import and export as folders; library.py the stateful test library
    benchmarks/    public benchmarks: longmemeval.py the dataset, its sizes and report, planning.py how a run of one fits an app; longmemeval_data/ the bundled questions
    traces/base.py      the neutral Span, the TraceSource interface, what kind a span is
    traces/view.py      what spans say, in either convention: the TraceView a try records
    traces/phoenix.py   a TraceSource over Phoenix REST v1
    traces/builtin.py   a TraceSource over evalution's own SQLite file, with retention
    traces/otlp.py      POST /v1/traces: standard OTLP into that file
    traces/managed.py   evalution's own Phoenix: the supervisor, where a target's spans are read
    traces/mirror.py    finished runs mirrored to a target's Phoenix, its run sink
    traces/setup.py     how to point an app at it: the tracing detector and the lines to add
    traces/profile.py   the digest of recent asks, and the fingerprint drift compares
    adapters/__init__.py  the registry: this folder's modules, by name
    adapters/base.py    the Target protocol below, Capabilities and AdapterInfo
    adapters/contract.py   `evalution adapter test` and `new`: an adapter's contract, over a real target (docs/adapters.md)
    adapters/http.py    generic chat endpoint, configured in YAML
    adapters/langgraph.py   a LangGraph graph through the LangGraph Server API
    code.py        a target's source: what is watched and read, hashes, diffs
    settings.py    the model set from the page: its block, its key, the model list, Test model
    llm.py         one model client: OpenAI-compatible or Anthropic messages API, tool calls, spend
    config.py      evalution.yaml: model connection, targets, data dir
    storage/       the database: documents, entries and encrypted secrets, by workspace (see "Storage")
    core/          case.py trace.py evidence.py judgement.py rules.py: what the rest shares, and the one place the rules live (docs/judging.md); imports nothing else of evalution
web/               React + Vite + Tailwind; the admin page. src/features/ has a folder per screen (home, learned, tests, test, runs, connect, settings), writer for the test writer, shell for the page around them, shared for what several use
sets/<target>/<set>/set.json, *.case.json, fixtures/   tests as files: `evalution sets export | import`, and `run --sets` in CI
library/stateful/<id>.json   ready-made stateful tests (see "Stateful test library")
examples/langgraph-memory-agent/   a tiny LangGraph app to try the langgraph adapter on
docs/
compose.yaml       evalution alone, with what it keeps in bind mounts; it points at the targets' apps
```

## Config (`evalution.yaml`, no secrets in it)

`evalution.yaml` is the operator's and is never written. What the page sets lives in the database ("Storage"), kind `settings`, owned by `config.py`: each target connected from the page under `targets/<name>` (its block, the same shape as one in the file), merged in, a name in both being the hand-edited one; and the page's model under `model`. Every `*_env` key names a secret, resolved by the config's `KeyRing`: the env var of that name, else the stored secret of that name (the page's: `targets/<target>/<key>`, `model/api_key`).

**The model** can be set by hand (`model:` below) or from the page (Settings, Model), which stores its `model:` block (same shape) and the key as the secret `model/api_key`. A `model:` in `evalution.yaml` wins whole, and the page says so and stays read-only. The key is the env var `api_key_env` (`EVALUTION_MODEL_KEY` for the page's model) first, then `model/api_key`; it is never returned or logged. A save swaps the model in the running server's one client, so the agent, Grader, analysis and filler use it from their next call. Prices (USD per million tokens) come from `model.prices`, else a small built-in table of Claude models; an unpriced model counts as 0. Every call's cost is added to the document `spend`/`model` (`llm.py`; "Spent so far" on the page).

- `GET /v1/settings/model`: `{provider: anthropic|openai|compatible, base_url, strong, light, key_set, source: page|config|env, spent_usd}` (`env`: the key comes from the environment).
- `PUT /v1/settings/model {provider, base_url?, strong, light, key?}`: `base_url` only for `compatible`; a `key` sets, `null` removes, absent keeps. 409 when `source` is `config`.
- `GET /v1/settings/model/models?provider=&base_url=`: the provider's list (Anthropic `GET /v1/models`, else `GET {base_url}/models`) with `input`/`output` prices when known. A key typed but not saved comes as the `X-Model-Key` header.
- `POST /v1/settings/model/test`, body as PUT or empty for the saved model: one tiny call on each, `{strong: {ok, detail, ms}, light: {...}}`.

```yaml
data_dir: ./data
traces: {managed: true, port: 6007}   # evalution's own Phoenix, or managed: builtin (see "Traces")
model:                       # evalution's own model, for Grader, filler, analysis, agent
  kind: anthropic            # or openai (any OpenAI-compatible base_url)
  base_url: https://api.anthropic.com
  api_key_env: EVALUTION_MODEL_KEY
  models: {strong: claude-sonnet-5-5, light: claude-haiku-5-5}
targets:
  my-agent:
    adapter: langgraph
    url: http://127.0.0.1:2024
    assistant_id: agent
    max_parallel: 1                  # optional: cases played at once (one test user each)
    phoenix: {url: http://127.0.0.1:6006, project: default, api_key_env: PHOENIX_API_KEY}
  my-bot:
    adapter: http
    phoenix: {url: http://127.0.0.1:6006, project: my-bot}
    http:
      send: {method: POST, url: "http://localhost:3000/chat", body: {"message": "{message}", "session": "{session}"}}
      reply: "$.reply"            # JSON path into the response
      session_header: null        # or a header name that carries the session id
      done: response              # or {poll: {url: "...{session}", until: "$.status == 'idle'"}}
      trace_attribute: session.id # how spans are found for this session
  my-graph:
    adapter: langgraph
    url: http://127.0.0.1:2024           # langgraph dev; langgraph up serves on 8123
    assistant_id: agent                  # an assistant id, or a graph name in langgraph.json
    api_key_env: MY_GRAPH_KEY            # optional, sent as x-api-key
    user_field: configurable.user_id     # or context.<key> / metadata.<key>
    store_namespace: ["memories", "{user}"]   # must contain {user}
    trace_attribute: session.id          # valued with the thread id
```

## Storage (`storage/`)

One database holds what evalution learns and writes: SQLite in `<data_dir>/evalution.db` by default, Postgres when `$DATABASE_URL` names one ([deploy](deploy.md)). Every row belongs to a **workspace**; this server's own is `local`, the only one until people sign in.

- **Documents**: a JSON value under `(workspace, kind, key)`, written whole. Keys are paths (`<target>/profile.md`, `<target>/<set>/<case>`), so a kind lists by prefix. Text stays text.
- **Entries**: an ordered log under one key, only added to (a run's evidence).
- **Secrets**: kind `secret`, encrypted with `$EVALUTION_SECRET_KEY` (a Fernet key) or `<data_dir>/secret.key`, made on first use and readable by its owner only. The page only ever sees the last four characters.

`Database.open` gives one pool per database per process; `Targets.docs` is the workspace's view, which every store above reads and writes through. Calls block, so async code runs slow ones in a thread. A database made by a newer evalution is refused. Files in the data folder from before the database are left as they are and not read. Tests come in and go out as files only through `evalution sets import | export` ([case-format.md](case-format.md)); a sets folder is never read otherwise.

## Traces

Everything reads spans through one interface, `TraceSource` (`traces/base.py`: `pages`, `spans`, `session`, `trace`, `trace_url`, `project_exists`, `close`), with two implementations: `traces/phoenix.py` and the built-in `traces/builtin.py`. What spans mean is in `traces/view.py` alone ("Two conventions").

`traces.managed` says where evalution keeps traces itself:

| `traces.managed` | Traces land in | Reads |
|---|---|---|
| `true` (default) | evalution's own Phoenix | its `evalution-<target>` and `default` projects, then the app's own Phoenix |
| `builtin` | evalution's own file | its spans for the target and the `default` ones, then the app's own Phoenix |
| `false` | wherever the app sends them | the app's own Phoenix, as set on the target |

Why Phoenix stays the default for now: [ADR 0004](adr/0004-traces-behind-one-interface.md).

### Managed Phoenix

`evalution serve` starts Phoenix (the optional `arize-phoenix` package, pinned, in the `traces` group; Elastic License 2.0, see NOTICE) as a child process (`traces/managed.py`):

- data under `data/phoenix/`; the UI and OTLP over HTTP on 127.0.0.1 at `traces.port` (default 6007, leaving 6006 to an app's own Phoenix), or the next free port, which `serve` prints;
- Phoenix can't turn gRPC off, so unless `traces.grpc` names a port it listens on a random local one nobody is told about;
- `/healthz` is checked every few seconds; Phoenix is started again when it exits or stops answering three times in a row, and stopped when `serve` stops. `data/phoenix/serving.json` says where it runs, so `evalution run` in another process reads the same traces.

Not installed: `serve` prints one line on how to install it (`pip install 'evalution[traces]'` or `uv sync --group traces`) and uses each target's own Phoenix. `traces: {managed: false}` does that on purpose (the behaviour before evalution had its own).

**Projects.** Each target's spans go to project `evalution-<target>`. The app is told the name where it can be: `PHOENIX_PROJECT_NAME` for Phoenix's `register()`, else the OTLP resource attribute `openinference.project.name` (`OTEL_RESOURCE_ATTRIBUTES`). An app that can't name it lands in `default`. A target's traces are read from `evalution-<target>`, then `default`, then the app's own Phoenix when the target has one ("Use my existing Phoenix" in Details), merged by span id (`traces/managed.py`, `reader`). Tries are found by session and trace id, so the default project mixing apps is harmless there; the profile reads all three, so the app's own Phoenix gives it the history of real traffic.

**Tagging eval traffic.** Every try's session carries its run: the langgraph adapter plays as user `eval-<run>` (its threads carry metadata `evalution_user`, its runs `evalution_run`); the http adapter's session id is `evalution-<run>-t<try>-<random>` and each request carries `X-Evalution-Run` and `X-Evalution-Try`, for an app that copies them onto its spans as `evalution.run` / `evalution.try`, which then decide whose spans they are ([judging.md](judging.md), "Traces"). The digest, profile and drift leave that traffic out.

### The built-in source

With `traces: {managed: builtin}` nothing else runs and no vendor package is needed. The server takes spans itself:

- **`POST /v1/traces`**, standard OTLP over HTTP: `application/x-protobuf` or `application/json`, optionally `Content-Encoding: gzip`, so `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:8080` is all an exporter needs. Answers follow the OTLP spec (the request's encoding, `partial_success` for rejected spans, a `Status` body on an error, 503 when the store fails). Without `builtin` the route answers 404.
- **Which target.** The resource attribute **`evalution.target`** (`OTEL_RESOURCE_ATTRIBUTES=evalution.target=<target>`). `openinference.project.name` works too, with or without the `evalution-` prefix, so an app already pointed at managed Phoenix only changes its endpoint. A span naming neither lands in `default`, which every target reads, as with managed Phoenix: tries are found by session and trace id. There is no project header.
- **Input limits.** It answers as the rest of the server does: only this computer, unless `serve --host` opened it on purpose. Bodies are capped at 32 MB on the wire and after gunzip (413). Any other content type is 415, which also means a web page can't post here without a preflight it would fail. Trace and span ids and times must be what OTLP says or the span is rejected; attribute count, key length, value length and nesting are capped. Span contents may hold prompts: they are never logged or echoed.
- **Storage.** `<data_dir>/traces/spans.db`, one SQLite file, owner-only. Retention runs on each write: spans received more than `traces.keep_days` ago (default 14, which is how far the digest looks back) go, then past `traces.keep_mb` (default 500) the oldest received go, plain spans before AI spans. A span sent again replaces itself. `evalution run` in another process reads the same file.
- **No trace page.** `trace_url` is `null`, so a try links to no trace; the page's own timeline is what there is.

### Two conventions

`traces/view.py` reads OpenInference attributes and the [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai) alike, from any source, so a `TraceView` is the same either way. Provider-specific logic stays out of it.

| | OpenInference | GenAI |
|---|---|---|
| Kind | `openinference.span.kind` | `gen_ai.operation.name` (`chat`, `generate_content`, `text_completion`: LLM; `execute_tool`: TOOL; `invoke_agent`: AGENT; ...) |
| Model | `llm.model_name` | `gen_ai.response.model`, `gen_ai.request.model` |
| Tokens | `llm.token_count.*` | `gen_ai.usage.input_tokens` / `output_tokens`, older `prompt_tokens` / `completion_tokens` |
| Tool | `tool.name`, `tool.parameters`, `output.value` | `gen_ai.tool.name`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` |
| Messages | `llm.input_messages`, `llm.output_messages`, `llm.tools` | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, older `gen_ai.prompt` / `gen_ai.completion`; on the span or on its `gen_ai.*` events |
| Session | `session.id` | `gen_ai.conversation.id` (asking for either finds both) |
| Cost | `llm.cost.total` | none defined: priced from model and tokens |

**Only AI spans count.** A span is an AI span when it has a recognised AI kind or `gen_ai.*` attributes other than a lone conversation id (apps may put that on any span); anything else (`GET /health`, `fastapi.endpoint`) is `UNKNOWN`. Those never appear in the trajectory or timeline, where an AI span's parent is its nearest AI ancestor, and don't count toward `max_spans` or the digest's page cap, so they can't crowd out AI spans (`MAX_READ` and `FLOOD_PAGES` still bound a read). They are still read, since one may carry the session or link two AI spans. A try or a discovery whose spans are all plain counts as no traces having arrived.

### Sending traces here

evalution can't restart someone's app, so when it connects one by folder it says what to change (`traces/setup.py`) and checks that spans arrive:

- An app traced with Phoenix's `register()` (found in its code): `PHOENIX_COLLECTOR_ENDPOINT` and `PHOENIX_PROJECT_NAME`, and a note when the code fixes `project_name`.
- An app traced with OpenTelemetry / OpenInference otherwise (packages in `pyproject.toml` / `package.json`, `instrument(` / `registerInstrumentations(` calls): the three `OTEL_*` lines.
- No tracing: tests still play and are graded on replies, but steps and context need tracing; the snippet for its language (Python `arize-phoenix-otel` + OpenInference with `register(...)`, JS `@arizeai/phoenix-otel` + `@arizeai/openinference-*`), with the instrumentation package for the LLM library it uses.

With the built-in source the endpoint is evalution's own server. The `OTEL_*` lines name the target as `evalution.target` and turn metrics and logs off, since only traces are taken, and an app with no tracing gets the plain OpenTelemetry SDK exporting OTLP (`opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`, JS `@opentelemetry/sdk-node` + `@opentelemetry/exporter-trace-otlp-proto`) instead of a Phoenix package.

## Connecting a target

The page connects a target without touching `evalution.yaml`. The usual way is by its project folder (`discover.py`):

- `POST /v1/targets/discover` `{path}` starts looking at the folder in the background and returns `{id, folder, status: "working"|"ready"|"failed", steps: [{text, state: "doing"|"done"|"problem"}], questions: [{key, text, hint}], target}`; `GET /v1/targets/discover/{id}` returns it again as it goes. Steps are plain sentences; `target` (once ready) is the proposed target in the shape `GET /v1/targets/{name}` returns, with secret keys only.
- In order, each adapter's `detect` (see "The adapter seam"): **LangGraph** by `langgraph.json`, or `langgraph` among its packages: the langgraph adapter, the assistant from the first graph in `langgraph.json` (else `agent`), the URL from a `langgraph dev|up --port` in the README or scripts, else a published port of a compose service that mentions langgraph, else 8123 when the project runs `langgraph up`, else `langgraph dev`'s 2024; tracing is read as for any app (`openinference-instrumentation-langchain`). **Any other app**: route definitions (FastAPI, Flask, Express, Next API routes) give the chat endpoint, the body fields it reads and its reply field; `PORT`, compose ports or the code give the port (else the framework's usual one); `phoenix.otel.register(project_name=...)`, `PHOENIX_COLLECTOR_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT` and OpenInference packages give Phoenix. When the routes don't say, the evals model (light) reads a compact digest: file list, README, package files, route code and the key names of `.env.example`. **Verify** with the check below (no chat message is sent). **Code**: the folder; everything in it that is source is watched (see "Knowing a target").
- **Send traces here**, when evalution keeps traces itself: the step "Send traces to evalution" and `traces: {traced, text, lines, command, arriving}` on the discovery (see "Traces"). `POST /v1/targets/discover/{id}/traces` checks again whether AI spans arrived in the last hour (in `evalution-<target>`). The page connects on its own once they arrive, or on Connect. In this mode the proposed target has a `phoenix` block only when the project names an existing Phoenix; `GET /v1/traces` returns `{managed, url}` (`url`: where apps send traces, evalution's own server with the built-in source).
- What it could not tell becomes at most two questions: "Which port does it run on?" (asked when the app doesn't answer on a guessed port) and "Which address takes chat messages?".
- `.env` is read only for the keys named above (`PORT`, `PHOENIX_*` for other apps). A secret found there (`phoenix_api_key`) stays in memory until the target is saved; it is never returned, logged or sent to the model.
- `POST /v1/targets/discover/{id}/accept` `{answers?, name?}` saves the target like `POST /v1/targets` (secrets stored, encrypted) and returns it; with `{target}` it saves that body instead (the page's Details), keeping the secrets found unless the body gives its own.

The technical way (`connect.py`), used by the page's Details and Settings:

- `POST /v1/targets` `{name, adapter: "http"|"langgraph", options, phoenix: {url, project, public_url?} | null, code?: {path, ignore?}, secrets: {key: value}}` validates the body (a slug name; http needs `send`, `reply`, `done`; langgraph needs `url`, `assistant_id`) and stores the block. Secret values (`phoenix_api_key`, `api_key` for langgraph, `authorization` for http) are stored secrets named `targets/<target>/<key>`, and the block names them through the usual `*_env` keys. A new target with no set gets an empty `main` set.
- `PUT /v1/targets/{name}` takes the same body; a secret left out is kept, `null` removes it. `DELETE /v1/targets/{name}` removes the block and its secrets; its sets, runs and profile stay on disk. Both refuse a target set in `evalution.yaml`.
- `GET /v1/targets/{name}` returns the editable target with the keys of its stored secrets, never their values. No route returns a secret, and none is logged.
- `POST /v1/targets/{name}/reach` tries each part without playing anything, and returns `{adapter: {ok, detail}, phoenix: {ok, detail, sessions_seen, has_llm_spans}, code: {ok, detail, files_matched}}`. Adapter: http `GET`s its `health` URL when set, else sends `OPTIONS` to the send URL; langgraph `GET`s `/ok`, then looks for the assistant (by id or graph name) in `POST /assistants/search`. Phoenix: the project exists, and how many recent conversations it holds. Code: the folder exists and how many source files it has. With a target body, it checks the unsaved form (its secrets are used for that check only).
- `POST /v1/targets/{name}/check` sends one real message through the saved target (`runs/preflight.py`) and returns `{ok, reply, seconds, tools, spans, traces, problems, warnings, next, can_redraft}`; a pass is remembered as a run's first check remembers it, and `GET /v1/targets` then says `checked` until the adapter or its settings change. Refused while a run plays.
- `POST /v1/targets/{name}/check/redraft` (`connect/probe.py`), after a failed message on an http target: the model gets the failure, the settings and what the app answered, with secrets taken out and capped, and proposes corrected settings, each tried with one more message, at most two rounds. Config only: routes must stay on the app's own address and must carry the message. It returns `{ok, options, tried}` and saves nothing; the page saves `options` with `PUT` once its owner presses the button.
- `PUT /v1/targets/{name}/isolation` `{required}` is what every new test of the app must start clean of, from what its adapter promises (`GET /v1/targets`: `starts_clean`, `isolation_note`, `required_clean`); by default nothing. The test writer is told, and a plan lacking it is flagged.
- `GET /v1/adapters` is what the form draws: each adapter's label, options, required options and secrets. A `PUT` that changes the adapter is kept as `adapter_change` on the target.

A conversation is a trace of OpenInference spans (AGENT, LLM, TOOL, ...) where one carries the session attribute. Other spans, such as plain HTTP server spans, are ignored. The digest reads back from now in growing windows, a page at a time, until it has the sessions it needs or hits a page cap. A project flooded with other spans still yields its conversations.

An address instead of a folder (`http://...`) skips the reading: the http adapter, with a first guess at the body and reply that the test message puts right, and a question when the address names no route. Addresses that can't work from here (0.0.0.0, loopback from inside a container, a local address sent through a proxy) are said in words, by `core/net.py`, as are failed calls. Listing a compatible gateway's models also tries the same server with `/v1` added or taken off when only the given address doesn't speak the protocol, and says so only then.

On the page: Connect is one screen of steps: App (one address-or-folder field, recent folders as chips, a Connect button, then the steps as they finish and any question inline), Model, Test message, Clean start, First test. Later steps open once the target is saved, and a failed one says what to do next. The test writer starts learning the app once there's both an app and a model; a link opens What it learned. A folded Details holds the technical form. The app picker holds Connect an app; the app's connection (edit, check, remove) is folded at the bottom of What it learned.

## The Target protocol (`adapters/base.py`)

```python
class Target(ABC):
    name: str
    capabilities: ClassVar[Capabilities]

    async def open(self, run: str) -> Session: ...                       # required
        # an isolated session for this run (langgraph: a fresh user; http: a new session id)
    async def send(self, session: Session, message: str, *, files: list[Path] = (),
                   stop_after: float | None = None, deadline: float) -> Turn: ...   # required
        # returns when the turn is done: reply text, started/ended, trace ids seen
    async def close(self, session: Session) -> None: ...                 # required; end of run
        # keeps what the session left in the app, unless the adapter declares `deletes`

    # With honest defaults; an adapter replaces what it declares.
    async def reset(self, session: Session) -> None: ...                 # between tries of a case
    async def state(self, session: Session, check: dict) -> StateResult: ...
    async def probe(self, session: Session, check: dict) -> StateResult: ...
        # the same check, read once without waiting, before a try's first message
    def sections(self, request: dict) -> list[Section]: ...
    async def break_conversation(self, session: Session) -> None: ...
        # a new conversation for the same person, keeping their memory and files
    async def cancel(self, session: Session) -> None: ...
        # stop the work in flight, child work the adapter owns included

@dataclass(frozen=True)
class Capabilities:
    isolation: Isolation      # NONE | SESSION (a new id only) | IDENTITY (a new user)
    isolation_note: str       # what that means here, in one plain sentence
    reset: bool; conversation_break: bool; sections: bool
    files: bool; stop: bool; parallel: bool
    cancel: bool; blocks: bool; deletes: bool
    state_checks: frozenset[str]; probe: bool

@dataclass
class Session:  id: str; trace_attribute: str; trace_value: str; extra: dict
@dataclass
class Turn:     reply: str; started: float; ended: float; trace_ids: list[str]; error: str | None
                failure: Failure | None; ended_as: TurnEnd  # ANSWERED | BLOCKED | FAILED
```

- The **trajectory** is never asked of the adapter. `traces/phoenix.py` reads every span whose `trace_attribute` equals `trace_value` (default `session.id`) between the try's start and end. From those spans it builds the calls and timeline, and the context of each LLM span (sections from `adapter.sections(request)` when declared, else one `turns` section).
- An `expect_state` key the target doesn't answer can't be judged (`not_supported`), and nor can one its adapter says it could not read (`StateResult.observed`). Its `state_checks` are the `expect_state` keys it answers (langgraph: below); the library's fit verdicts use them. Without `reset`, each try opens a new session. Without `conversation_break`, a case's `{"break": "conversation"}` turn fails the try, saying so. Without `files` or `stop`, a turn asking for them errors.
- **A try that starts with leftover state.** With `probe` declared, before a try's first message `runs/leftover.py` reads each of the case's `*_contains` state checks the adapter answers, once and without waiting. One that already holds can only be what an earlier try left, since a clean start holds nothing: the try isn't played, and reads as unjudged, `leftover_state`. `*_lacks` checks hold at any clean start and aren't read. A probe that fails is logged and the try plays as before. langgraph and letta declare it.
- **Side by side.** A target's `max_parallel` (1 to 16, default 1) lets a run play that many cases at once, when its adapter declares `parallel`, which isolation no longer implies: http and langgraph both do. Each slot opens its own session, `open("<run>-<slot>")`, and plays whole cases from the queue, a case's tries in order. With `max_parallel: 1` the run plays as before, one case after another in `open("<run>")`. Still one run at a time.
- `GET /v1/targets` and the test writer say capabilities as a sorted list of strings (`Capabilities.names()`): `reset`, `state` (it has `state_checks`), `sections`, `isolation` (anything but `NONE`), `parallel`, `conversation_break`, `cancel`, `blocks` (it can tell when work is waiting, so a test may expect that).
- **What an adapter may touch.** `cancel`, `close` and any cleanup act only on what this run's sessions made. Session ids and the identities they play as are unique to a run or a try (http: `evalution-<run>-t<try>-<random>`; langgraph: an eval user per run or slot), and no adapter searches more widely. By default nothing is deleted: http's `close` does nothing, and langgraph keeps its threads and store items unless the target sets `cleanup: true`, which it then declares as `deletes`. `reset`, where declared, is what clears a session between tries. `cancel` and `close` are also called for sessions an earlier process left open, rebuilt from `Session` fields, so neither relies on memory.
- **How a turn ended.** http reads it from `done.poll`: `failed_when`, then `blocked_when`, then `until`, each poll, in the same grammar, with the app's reason at `why`; `cancel: {method, url}` adds the capability. langgraph reads an interrupt as blocked and a run error as failed. See [judging.md](judging.md).
- Filler turns are written by evalution's own light model and sent like any message.
- A conformance test (`tests/test_adapter_conformance.py`) runs over every adapter: what is declared is what is implemented. `evalution adapter test` does more of that over a real target, for anyone's adapter ([adapters.md](adapters.md)).

### The adapter seam

All that evalution knows about an app or a vendor is in its adapter's module, `adapters/<name>.py`. Each exports `create(target) -> Target` and `INFO`, an `AdapterInfo` that core reads through `adapters.infos()`, which is this folder: adding the module adds the adapter, with no edit elsewhere (`tests/fixtures/adapters/echo.py` is one). `tests/test_no_vendor_in_core.py` fails when a vendor's name appears in code outside `adapters/`.

| `AdapterInfo` | What core does with it |
|---|---|
| `name`, `label` | The target's `adapter`; how the understanding page says it |
| `options`, `required`, `secrets` | What the page may send for it (`connect.parse`) |
| `check(client, block, lookup)` | The app's part of `POST /v1/targets/{name}/check`; `refused` is the discovery step when the app turned the secret down |
| `detect(folder)` | Whether a project folder is its kind of app, and what that tells, as a `Detection`. Highest `rank` first; `http` has none, and takes any app nothing else claims |
| `trace_setup(url, builtin)` | How to point it at evalution's traces, when that isn't what its code shows; `trace_project` is where its spans land if it can't name one |
| `own_traffic` | How its eval traffic is marked in traces (session prefixes, span attributes, `user.id` patterns), left out of digests and drift |
| `framework` | A package word and name for the understanding's stack |
| `validate`, `block`, `page_options`, `propose` | The shape of its options, when it isn't flat strings: checking them, writing them into a target's block, reading them back for the page, and proposing them from discovery |

Phoenix is a trace source, not an adapter: `traces/` and the `phoenix:` config key know it, and the runner sees a run's mirror only as `Targets.sink`, a `RunSink` (`results/store.py`).

### The langgraph adapter

Plain HTTP against the LangGraph Server API (`langgraph dev`, `langgraph up`, LangGraph Platform), no SDK. Capabilities: isolation `IDENTITY`, `reset`, `state_checks`, `probe`, `conversation_break` (a new thread for the same user, store kept; `session.id` and `trace_value` move to it), `stop`, `parallel`.

- **open(run)**: user `eval-<run>`; `POST /threads {metadata: {evalution_user}}`. The session is the thread; spans are found by `trace_attribute` (default `session.id`) = the thread id. OpenInference's LangChain instrumentation sets `session.id` from the `thread_id` LangGraph puts in the run metadata; an app traced otherwise sets it itself (`using_session(thread_id)`).
- **send**: `POST /threads/{id}/runs {assistant_id, input: {messages: [{role: user, content}]}, config: {configurable: {user_id}}, metadata}`, then `GET /threads/{id}/runs/{run}/join` (capped at the deadline); Stop is `POST /threads/{id}/runs/{run}/cancel?action=interrupt`. The reply is the last AI message's text in the returned values; `__error__` and `__interrupt__` become `Turn.error`. No files.
- **state** (`state_checks`): `memory_contains` / `memory_lacks` (`POST /store/items/search {namespace_prefix}` for the user's namespace: an item contains the text, or none does, waiting up to 30 s), `checkpoint` (`GET /threads/{id}/state` values hold `summary`, `running_summary` or `context.running_summary`), `messages_trimmed` (the thread keeps fewer user messages than were sent on it), `state_contains` (`"<json path> <text>"` or text, in the thread values).
- **reset**: every store item under the user's namespace deleted (`DELETE /store/items {namespace, key}`), then a new thread. **cancel**: the thread's runs still going (`GET /threads/{id}/runs`, then `POST .../cancel?action=interrupt`). **close**: keeps what the run made; with `cleanup: true`, the user's threads (`POST /threads/search {metadata}`, `DELETE /threads/{id}`) and store items deleted. The namespace must contain `{user}`, so only the eval user's items are ever deleted.

## API and page

The routes are in `server/evalution/api/`, their JSON shapes in `web/src/lib/schema.ts`, with:

- `target` added to sets and runs: `GET /v1/targets`, `GET /v1/targets/{target}/sets`;
- the other routes keep their paths, with `?target=` or a body `target` where a set is named.

No login and no admin identity model: `evalution serve` listens on 127.0.0.1 and refuses clients from other computers (403). `serve --host <address>` opens it to others on purpose, and prints a warning that anyone who can reach that address can use it. The page is served by the server from `web/dist`. It still answers only under the address it was given (on all addresses, this computer's own names) and the names in `allowed_hosts` or `$EVALUTION_ALLOWED_HOSTS`: a web page can reach a server under a name of its own, and there is no login to stop it reading what comes back. A stored secret stays with the hosts it was given for: an edit that adds a host to a target's addresses forgets it unless it's typed again. `serve` holds a shared lock on the data folder as long as it runs, and `restore` an exclusive one, so neither starts under the other.

## Knowing a target: the profile

When a target is added, or on "refresh", the agent builds the profile, `<name>/profile.md`: like everything kept on a target, a document of kind `profile` keyed `<name>/<doc>` (see Storage). It is plain text the admin can read and edit, and the agent reads it every time it writes a test for that target.

**Sources:**

1. **Traces:** recent spans of the target (last N sessions, eval traffic left out; see "Traces" for where they are read). From them the agent takes:
   - tool names, with argument shapes and frequency (offered and called tools named alike, without an MCP prefix);
   - the usual trajectory shapes per ask (span kinds, depth, delegated work), and how many asks used no tools;
   - the main agent's system prompt and its sections, from LLM spans (helpers' prompts counted apart);
   - typical time, tokens and cost per ask: one trace, one message and everything it set off. A session (a conversation, which may live for days) only counts conversations; case limits come from the per-ask figures.

   A summarising pass in `traces/profile.py` does this, so the agent reads a compact digest, not raw spans.
2. **Code:** optional, from `code: {path: ..., ignore: ["generated/**"]}` in the target config. It is read-only, and no folder layout is assumed: every file from git (tracked, plus untracked and not ignored), or a walk when the folder is not a checkout. Left out: tests, lockfiles, build output, tool and credential folders and files, binaries, files over 1 MB, whatever `ignore` names, and always secrets files (`.env*`, keys). At most 2,000 files. An `include` in an older `targets.yaml` is not read. The agent reads the same files, with size caps, and nothing git ignores.
3. **The admin:** a short "what this app is for, what matters" kept at the top of the profile.

**Output sections:** what the app does; tools (name, purpose, key args); state it keeps; what a good reply looks like; risky areas; proposed starter tests. The starter tests are only proposals, added through the normal plan → approve → add flow.

**Evidence.** Every line but headings ends with where it comes from: `[trace]` (the trace digest), `[code: path]` (that file) or `[admin]`. The profile is rendered from the understanding (see "The understanding"): `write_understanding` refuses, saving nothing, a node or claim without evidence, or a node tool that is neither in the traces' tools (the tools offered to the model, else those called) nor in a code file the node cites. What the agent did not see stays out.

**Versions.** Every write (the agent's, or the admin's edit) is kept in `<name>/profile_versions.json`, the newest 10, each with who wrote it, when, and the hash of the fingerprint kept. `GET` / `PUT /v1/targets/{target}/profile` return `{text, version: {version, at, by, fingerprint} | null, changes: [{kind: "added"|"removed", text}], drift}`, `changes` being the lines changed since the version before. The page shows the profile with its tags set apart quietly, and "Changes since last version" folded under it.

**Agent tools:**

- `read_profile` (the rendered profile), `read_understanding`, `write_understanding`
- `read_trace_digest(target, sessions=50)`
- `read_code(path)`, limited to the watched files
- `list_code(glob)`

## Drift: noticing when the target changes

The profile carries a **fingerprint**:

- the tools offered to the model, with their parameter names (tools merely seen called are not compared: a small sample misses some);
- the main agent's usual system prompt hash, every variant seen, and the hash of each section;
- trajectory shape counts (delegation, depth, typical tool sequence);
- for a connected repo, the last commit read, and the hashes of the watched files.

**When it's checked:**

- before every run (cheap: digest of the last few sessions, plus `git rev-parse` of the repo);
- once a day;
- on `POST /v1/targets/{target}/changed`, which the git hooks or CI call: the code part only, in the background (see "Keeping what it knows current"); the whole check for an app with no code or no fingerprint yet.

A trace difference counts only once two checks in a row see it (`drift.json` keeps it as `pending` in between); a code change counts at once. A prompt counts as changed only when the main agent's usual prompt is none of the variants seen before.

**Drift found:**

- `<name>/drift.json` lists each change (`tool_added`, `tool_removed`, `args_changed`, `prompt_changed`, `shape_changed`, `code_changed` with the files).
- The profile shows "out of date".
- Tests whose expectations name a changed or removed tool, argument or prompt fact are marked `stale: <reason>`, and `CaseRow.stale` returns it.
- For code changes, the agent reads the diff of the watched files (scoped like `read_code`) and writes a short behaviour summary into the drift entry.

**After that:**

- The agent proposes profile updates, edits to stale tests and new tests for new behaviour, all through plan → approve.
- The analysis tags a failure on a stale expectation as `case`, citing the drift entry.

A fingerprint taken under other watching rules (from before everything was watched, or `code.ignore` changed) has its code part taken again at the next check, so that does not read as every file changing.

**Agent tools:** `read_drift(target)`, `diff_code(target, since)` (watched files only) and `note_drift(change, summary)` for the behaviour summary.

The fingerprint is `<name>/fingerprint.json`, taken whenever the agent writes the profile (which also clears the drift list); an admin's own edit of the profile leaves it as it was.

**Page:**
- "Changed since learned · 3" on the project card, and "May be stale" on each test it touches. Learn again catches up.
- A "may be stale" mark on affected tests.
- "Ask the agent to update" sends the drift to the agent.

### What a code change means (`impact.py`)

Drift compares with what the profile was written from. The change card on Home compares with what was **last tested**.

- **The tested state.** When a run starts, the code's commit and hash map are kept in `<name>/tested.json` (`runs`: the last 3 starts), and the run document gets `commit` and `code` (the hash of the map). The first run's state is the first `tested`; an app with runs from before this starts from the code at its first check.
- **Looking.** `GET /v1/targets/{t}/health` starts a background check, at most every 5 minutes or when a run has ended, and answers without waiting; `checking` says one is going, and the page asks again until it ends. The check is the code part of drift (no traces read), then the comparison with `tested`.
- **Interpreting.** Follows the app's updates setting (ADR 0005): in `manual`, the default, no call is made and the card is the changed files and all the tests until the owner presses *Read this change* (`POST /v1/targets/{t}/change/read`); `health.code_change.read` says whether the model read it. In `auto`, one call to the light model per distinct change: the diff of the changed files (secrets files never; new untracked files from their start; capped at 60,000 characters, or just the file list when the folder is not a checkout or the commit is gone), the profile and the tests. It answers `{summary, areas, tests, uncovered}`; test ids are `<set>/<case>` and only real ones are kept. Answers are kept in `<name>/impact.json` by fingerprint: the changed files with their hashes, and the test ids, so a new test gets the change read again. No model, or a call that fails or does not read, gives the changed files and all the tests; only the no-model case is asked again later.
- **Clearing.** A test leaves the card once a finished run judged it on the code as it stands now (a failure counts; it shows in the attention list). When no test is left and no area without a test waits, the card goes and `tested` moves to the current code. A full run that finished moves `tested` to the code it started on, by itself. A change the model says touches no test and no area clears at once. An area with no test keeps the card until any run is made on that code.
- **Nothing runs by itself.** The card's button goes through the estimate and its token with `cases`, then the usual confirm. An area with no test opens the test writer with an ask in its reply field, not sent.

`health.code_change` is `{summary, areas, uncovered, files, file_count, read, tests: [{set, case, name}]} | null`. A run is one test set, so affected tests in several sets are run a set at a time, the open one first.

**Regressions.** A test failing in its newest run that passed in its last run on another commit comes first in `health.attention`, naming both short commits, when the two can be compared (`rules.comparable`: same test version, rules, rules its checks were made under, judge model and pass marks). A run without a `commit` says nothing either way, and a test no one could judge isn't failing.

### Keeping what it knows current (`knowledge.py`, `imports.py`, `hooks.py`)

The understanding names its evidence; `<name>/knowledge.json` keeps it honest as commits land (ADR 0005).

- **Links.** `claim:<id>` or `node:<id>` -> `{file: hash}`, from evidence of kind code, docs or prompt. Only files in `Code.hashes()` link, so a ref cannot name a secret or leave the folder; what does not resolve stays as written, unlinked. A partial ref (`a.py`) links when it ends exactly one file `read_code` read in that turn. Reads link nothing else: tying every read to every claim would mark all of them on any commit. Made when the agent writes, and on first sight of an older doc, with no model call.
- **Detecting (free).** `checked` is the commit last checked. Only `git diff checked HEAD` counts, never the working tree, and not during a rebase. A file whose hash is still the linked one is not a change for that claim. `stale` records `direct` or `indirect` with the files. Indirect uses `imports.graph`: Python through `ast`; JS/TS, Go and Markdown links by careful patterns resolved against files that exist; no edges for anything else or a file that does not parse. What a file imports is cached by hash in `<name>/imports.json`. A claim's files reach a change within `imports.HOPS` (2).
- **The setting.** `<name>/settings.json` `updates`: `manual` (default) or `auto`. `PUT /v1/targets/{t}/updates`.
- **Refreshing.** `knowledge.refresh` is the one path. Detection always runs; a call follows when asked (`POST /v1/targets/{t}/understanding/update`, everything marked), or in `auto` for the marks a new commit range gave. One light call, cached by fingerprint (the commits and the claims' words). It gets up to 60 marked claims, the capped diff of the changed watched files, and those claims' files as of HEAD (blobs, so a repo's filters do not run). Answers per claim are `still_true`, `rewrite` (text, evidence) or `removed`, plus at most 3 `new`.
- **Validation.** A rewrite must cite a watched file: a direct claim's own files or a changed one; an indirect claim's only a changed one. Otherwise it is dropped and the claim stays marked. New claims must cite a changed file. Sections go through `understanding.check`. Saved with `by: "refresh"`, which renders the profile; rewritten items carry `updated_from` (the commit).
- **The owner wins.** A claim or node with a fix or verdict is never changed: a `rewrite` or `removed` puts it in `flags`, shown as may be out of date until the owner steers it again. So are an indirect `removed`, and a node `removed` (the diagram hangs on nodes). A node's `rewrite` replaces its `sub`, never its title.
- **Failure.** An exception, an unreadable answer or a doc that fails validation changes nothing: `checked` stays, marks stay. In `auto` the same change is tried again only after a day or a new commit. A doc rewritten meanwhile wins; the answer is dropped.
- **Backlog.** Turning `auto` on moves `checked` to HEAD and keeps the base in `backlog_since`. The marks stay behind the button, whose diff starts there, and `impact.hold` keeps the change card's current change from being read by itself. Automatic calls only ever carry the marks of a new commit range.
- **Not touched.** A refresh leaves `fingerprint.json` and `drift.json` alone, so tests are marked stale as before and "Changed since learned" stays until Learn again.
- **Hooks.** `evalution hooks install|uninstall|status --target`, `GET/POST/DELETE /v1/targets/{t}/hooks`, the Settings toggle; nothing else installs them. `git rev-parse --git-path hooks` finds the folder. A block with `# >>> evalution <name> >>>` goes right after the shebang, so a later `exit` cannot skip it. New files are 0700 and carry a created marker; uninstall removes one only if nothing else is left. Links, non-shell scripts and hooks switched off are left alone and reported. A folder git would see in the working tree, or a hooks path set outside the repo's own config, is refused. Name and URL are validated strictly, and shell-quoted.
- **`/changed`.** Answers 202 at once. `Watcher.changed` waits for 5 quiet seconds (60 at most), so a burst is one check: code drift, knowledge, then the change card. The Home check and the daily one run the same refresh, as the fallback without hooks.

**Page.** `knowledge` on the understanding and on health: `{mode, out_of_date, flagged, can_update, update_usd, read_usd, last, changes, can_undo}`. `last` is the last update until `POST .../understanding/seen`; `changes` compares the last two versions; `POST .../understanding/undo` puts back the version before a refresh, as a new version with `by: "undo"`, and starts the links again from HEAD. Estimates are characters / 4 at the light model's input price plus a fixed answer size, `null` when the model has no price.

## Stateful test library

evalution leads with assistants that keep state (ADR 0002), so it ships ready-made tests for it: `library/stateful/<id>.json` (`library_dir` in the config moves it), read by `library.py`.

**A template** is metadata plus a case skeleton:

```json
{"title": "Forgets on request",
 "checks": "One sentence on the behaviour.",
 "why": "Why it matters.",
 "needs": ["state", "traces", "conversation_break", "memory"],
 "est_turns": 4,
 "case": {"name": "...", "turns": [{"message": "Something to remember: {{fact}}"},
                                   {"expect_state": {"memory_contains": "{{fact_word}}"}}, "..."],
          "reply": {"rubric": ["Doesn't mention {{fact_word}}"]}}}
```

- `case` is the usual case format with `{{placeholders}}` from a fixed list (`library.PLACEHOLDERS`): values taken from the profile (`topic`, `ask`, `task`, `filler_topic`), made-up test data that fits the app (`fact` and its distinctive `fact_word`, `fact_question`, `new_fact`, `distractor`, `preference` and its visible `effect`, `name`, `role`), and numbers (`n` generated messages, `seconds`). A string that is only a number placeholder becomes the number.
- `needs` names what a target must have: the adapter's `state`, `conversation_break` or `sections`, readable `traces` (for `context_*` checks), and what the app keeps, as its profile shows: `memory`, `compaction`. Loading checks every template: known fields and placeholders, a valid case once filled with stand-ins, and `needs` covering what the case uses (a break turn, state checks, context checks).
- Every check is one the case format already has: state checks the adapter answers, `context_contains` / `context_lacks`, and rubric lines the Grader can check from the reply.

The ten: compaction keeps an early decision; memory recalls a fact after many messages; a preference carries over to a new conversation; a correction replaces the earlier fact; forgets on request (gone from memory, from the context, and from answers); a withdrawn instruction no longer applies; compaction drops a withdrawn fact; a task resumes in a new conversation; recall picks the right earlier conversation among look-alikes; a long chat keeps the person's name and role.

**Fit**, per target (`library.fit`):

- `not_applicable` ("Not for this agent") when the profile mentions none of the state the template is about (memory, compacting long chats);
- `needs` when the adapter lacks a capability, a state check the case uses (from the adapter's `state_checks`, when it lists them), traces, or the target has no profile yet; the badge names the first ("Needs state checks", "Needs new conversations", "Needs a profile");
- `fits` otherwise.

`GET /v1/library?target=` returns `{data: [{id, title, checks, why, needs, est_turns, fit: {verdict, label, missing, reason}}]}`.

**The agent** starts from the library when it proposes stateful coverage. `list_library` gives the templates with their fit. `adapt_template(id)` refuses one that doesn't fit, else shows its skeleton and what each placeholder takes; `adapt_template(id, values)` fills it: a value from the profile is `{value, evidence}`, the evidence quoting a profile line that carries its tag (`[trace]`, `[code: path]`, `[admin]`), and the result is a valid case (`source: "library: <id>"`) with that evidence beside it. Then the usual plan → approve → save.

**The page** shows the library only through Suggested tests (see "The understanding"): it is the test writer's knowledge, not a list of its own. `GET /v1/library` stays for the fit badges.

## The understanding (project card, diagram, paths, claims)

The profile becomes structured: `<name>/understanding.json` is the source of truth. `<name>/profile.md` is rendered from it, for people and for the agent's prompt. Design: mock-up `understood.html`.

```ts
interface Understanding {
  version: number; written_at: number; fingerprint: string;
  card: {
    name: string; one_line: string;
    stack: string[];                 // detected: "Python · FastAPI", "React web", "Claude models", adapter, traces
    status: "learning" | "up_to_date" | "out_of_date"; changes: number;
    numbers: { tools: number; tools_seen: number; state_kinds: string[];
               ask_seconds: number | null; ask_usd: number | null;
               tests: number; tests_passing: number | null;
               library_covered: number; library_total: number };
    watched: string[];               // "prompts", "tool code", "docs"
  };
  diagram: {
    nodes: { id: string; kind: "person" | "agent" | "tools" | "worker" | "state";
             title: string; sub: string; seen: boolean; evidence: Evidence[];
             verdict?: "right" | "wrong"; fix?: string }[];
    edges: { from: string; to: string; asks: number; seen: boolean }[];
  };
  paths: { share: number; steps: string[]; seen: boolean }[];   // seen=false: expected but never seen
  sections: { id: "what_it_is" | "can_do" | "good_reply" | "in_use"; title: string;
              note?: string;                                     // e.g. thin-traffic warning
              claims: { id: string; text: string; evidence: Evidence[];
                        verdict?: "right" | "wrong"; fix?: string }[] }[];
  priorities: { id: string; title: string; picked: boolean; source: "agent" | "admin" }[];
  starters: { id: string; title: string; what: string; why: string; usd_per_try: number;
              priority?: string; library?: string; selected: boolean;
              exists: boolean }[];   // the page's Suggested tests; exists: already a test

}
interface Evidence { kind: "prompt" | "code" | "traces" | "docs" | "admin"; ref: string }   // "AGENTS.md \"Delegation\"", "2 of 2 deck asks"
```

**How it's built:**

- **The engine computes:**
  - `card.numbers` and `card.stack` from detection, the digest, runs and library fit;
  - `diagram.edges.asks` and `seen` from traces, per ask;
  - `paths` from traces: the most common tool sequences per ask, mapped to plain step names, plus expected paths the agent names from code that are absent in traces.
- **The agent writes the rest** through `write_understanding(...)`: the one line, the nodes' titles and subtitles, the claims with evidence, the priorities and starters. The engine refuses claims without evidence, and tool names not offered (as for the profile today).

**Routes:**

- `GET /v1/targets/{t}/understanding`
- `POST /v1/targets/{t}/understanding/steer` with `{claim|node: id, verdict: "right"|"wrong"} | {claim|node: id, fix: text} | {priority: id, picked} | {priority: {title}} | {starter: id, selected}`. Each steer is stored; a fix or wrong becomes an `[admin]` line in the next profile render.
- `POST /v1/targets/{t}/understanding/plan` with `{starters: [ids]}` sends the agent "plan these", and the plans follow the normal approve flow. A starter that is already a test is refused (409, "Already a test: <title>").

**Suggested tests.** The starters are the page's Suggested tests, the only place the library shows. A starter is `exists: true`, and never `selected`, when the target already has a test made from its library test (`source: library: <id>`) or with its name (or the file name its name gives).
- `GET /v1/targets/{t}/card.md` returns the card as Markdown.

Steers are kept in `<name>/steers.json` and laid over the understanding on read. A verdict or fix holds while the agent leaves that claim's text (a node's title) as it was; once the agent rewrites it, it counts as honoured. Nodes carry the tools they stand for (`tools`, written by the agent, not returned); `step_labels` (tool → plain step name, `(no tools)` for an ask without tools) and `expected_paths` name the paths. The plan route takes an optional `session` (else it opens one on `set`, else the first set) and returns the agent session.

**Agent message after learning:** one line ("I've learned X. Check what I understood."). No profile dump in chat; the page shows What it learned.
