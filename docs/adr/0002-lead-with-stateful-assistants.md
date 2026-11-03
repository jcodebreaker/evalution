# 0002: Lead with stateful assistants

> **Status:** Accepted 2026-11-01 by the owner, after an independent strategy review.

Run sets as files, matchers, an LLM rubric grader, run history and failure analysis are table stakes; LangSmith, Braintrust, Phoenix experiments, promptfoo and Inspect all have them. What no listed tool does out of the box is test assistants that **keep state**:

- facts surviving long chats and compaction
- memory being written and recalled
- what the model is actually sent (context assembly)
- isolated per-run identities so state never leaks

## Decision

evalution leads with stateful assistants:

- **A library of ready-made stateful tests:** compaction keeps early decisions, memory recalls a fact after N steps, forget-on-request, context lacks stale facts, the person's preferences persist. The agent adapts each one to a target from its profile.
- **A second stateful adapter** for a common memory-keeping agent framework, so the wedge is not one app.
- **State checks and context-assembly views stay first-class** in the case format and the page.

The generic HTTP adapter stays basic (single JSON request and reply) until the wedge is proven. Streaming, full history and presets come later.
