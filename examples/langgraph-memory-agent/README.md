# LangGraph memory agent

A tiny LangGraph agent to try evalution's `langgraph` adapter on: a `remember` tool that saves facts about the person in the store under `["memories", <user_id>]`, and a `summarise` node that folds all but the last six messages into `summary`. It uses any OpenAI-compatible model; the defaults point at a local [Ollama](https://ollama.com), so no paid model is needed.

## Run it

```sh
cd examples/langgraph-memory-agent
cp .env.example .env            # model and Phoenix settings
ollama pull llama3.2            # or point OPENAI_BASE_URL / MODEL at another model
uv run langgraph dev --no-browser
```

`langgraph dev` serves the graph `agent` at http://127.0.0.1:2024, with an in-memory checkpointer and store.

## Traces

`agent.py` registers Phoenix's OpenTelemetry exporter and OpenInference's LangChain instrumentation. With the `.env` above its spans go to evalution (`evalution serve`, on port 8080) for the target `memory-agent`, named by the project `evalution-memory-agent`. OpenInference puts the thread id on every span as `session.id`, which is how evalution finds a try's spans.

## Connect it

In evalution, connect the folder `examples/langgraph-memory-agent`: discovery reads `langgraph.json` and proposes the `langgraph` adapter at `http://127.0.0.1:2024`, assistant `agent`. Or by hand in `evalution.yaml`:

```yaml
targets:
  memory-agent:
    adapter: langgraph
    url: http://127.0.0.1:2024
    assistant_id: agent
```

State checks this app supports: `memory_contains` (a fact the `remember` tool saved), `checkpoint` (a `summary` was written), `messages_trimmed` (older messages were removed) and `state_contains` (e.g. `"$.summary Lisbon"`).
