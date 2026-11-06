# 0004: Traces behind one interface, with a built-in OTLP source

> **Status:** Accepted 2026-11-01 by the owner. Its default is superseded by [0008](0008-built-in-traces-only.md): managed Phoenix is gone.

evalution could only read traces from Phoenix, and only in OpenInference's attribute names. Its callers typed against the `Phoenix` class, and `traces/phoenix.py` mixed Phoenix's REST with what spans mean. So an app without Phoenix got no steps, and an app instrumented with the OpenTelemetry GenAI conventions got an empty trajectory. Managed Phoenix is also Elastic License 2.0: fine to self-host, not fine to offer as a service to others, which limits how evalution itself can ever be offered while it depends on it.

## Decision

- **One interface.** Callers type against `TraceSource`. `traces/phoenix.py` only translates Phoenix's REST; the neutral span and the `TraceView` live in `traces/base.py` and `traces/view.py`. Mirroring runs to Phoenix (`PhoenixStore`) stays Phoenix-only and optional.
- **A built-in source, no vendor.** `traces: {managed: builtin}` makes evalution's own server take standard OTLP over HTTP at `POST /v1/traces`, into one SQLite file with an age and a size cap. Its dependencies are `protobuf` and `opentelemetry-proto`, nothing under the Elastic License. OTLP rather than a format of our own, so any OpenTelemetry exporter works with one line of configuration.
- **Two conventions, one normaliser.** `traces/view.py` reads OpenInference and GenAI attributes alike, from any source. Only AI spans reach the trajectory and timeline, for Phoenix targets too.
- **Managed Phoenix stays the default for now.** It has what the built-in source doesn't: a page to read a trace on (`trace_url`, the links from a try), and the experience of many real installs. The built-in source is opt-in until it has been used on real apps. Discovery keeps reading a project's own Phoenix when it has one.

## Consequences

- With the built-in source a try has no trace link: the page's timeline is all there is.
- SQLite in one file means spans are read by filtering a time window in Python, as with Phoenix. That is enough for a local tool, not for a shared service.
- Making `builtin` the default later is one line of config, plus deciding what replaces the trace link.
