# 0001: Evals are their own project, wired to any traced agent app

> **Status:** Accepted 2026-11-01.

Matching, grading, analysis, the agent and the page only need two things from the app under test: a way to play a conversation, and the traces of what happened. None of it should live inside one app.

## Decision

**evalution is a standalone service with its own web app.** It holds the cases (sets), the runs, the agent sessions and the admin page. One evalution can test several apps, which it calls **targets**.

**A target is an app with an adapter and a trace source.**

- **The adapter plays a case's turns in an isolated session.** It returns the session and trace ids, and optionally it:
  - resets and cleans up the session;
  - checks app state (for example "a checkpoint was written");
  - names the sections of a model request (system prompt, memory, tools…).
- **The trace source is Phoenix.** Any app instrumented with OpenInference that sends spans to a Phoenix project gives evalution, by trace id or session id:
  - the trajectory (AGENT, TOOL and LLM spans with their timing)
  - what each model call was sent (`input.value` / `llm.input_messages` on LLM spans)
  - tokens and cost

  Grading, analysis, the agent's reviews and the page read only this, plus what the adapter returns.

**The first adapter is http:** any chat endpoint, configured with no code:

- the URL and method;
- where the message goes in the body;
- where the session id goes;
- where the reply comes from;
- how to tell a turn is done (the response itself, or polling a status URL).

**Model calls (Grader, filler, analysis, agent) use evalution's own model connection.** One OpenAI-compatible or Anthropic endpoint, set in its config. The app's model picks are never used.

**Cases are files in evalution's `sets/` folder, one folder per target.** That keeps them reviewable in pull requests.

## Considered options

- **Evals inside each app, with a plug-in seam.** Every app would have to run the same engine to be evaluated.
- **Hosted eval products.** They break "no hosted vendor required" and send prompts and answers off the machine.
- **Read only Phoenix and never play.** That's good for grading real traffic (a later mode), but it can't test a new case before it happens in the wild.

## Consequences

- One repo: `server/` (Python, FastAPI), `web/` (React), `sets/`, `docs/`.
