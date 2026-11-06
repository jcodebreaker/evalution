# 0008: Built-in traces only, in the database

> **Status:** Accepted 2026-11-05 by the owner. Supersedes the default in [0004](0004-traces-behind-one-interface.md).

evalution is now hosted as well as local: workspaces, sign-in, Postgres (docs/hosting.md). A managed Phoenix doesn't fit that. It is a second server per install, Elastic License 2.0 rules out offering it to others, and it has no idea of workspaces. The built-in OTLP source from 0004 had only one file per install and a Python-side time filter, which doesn't serve many workspaces either.

## Decision

- **No managed Phoenix anywhere, local included.** evalution starts no process for traces. An app's own Phoenix stays a source, set on the target.
- **Spans in the database.** A `spans` table beside the documents (SQLite locally, Postgres hosted), one row a span, apart by workspace, indexed for a try's window and deduplicated by trace and span id as exporters retry. Kept `traces.keep_days` (30) by when they arrived, pruned daily.
- **Apps send with a token, hosted.** OTLP/HTTP to `<public URL>/v1/traces` with a workspace token; a `traces` token can send spans and nothing else, so the one living in an app's environment can't read the workspace. Locally, no token, as before.

## Consequences

- Every target has a trace source, so a try always waits for spans; an app that sends none reads "No traces arrived", unjudged, as it did under managed Phoenix.
- A try has a trace link only when the app has its own Phoenix: the page's timeline is the trace.
- Old span files and old `traces.managed` settings are left alone; the settings load with a one-line note.
