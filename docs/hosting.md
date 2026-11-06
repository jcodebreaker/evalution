# Hosting

Locally, evalution has no login and one workspace. Set an OpenID Connect issuer and it is hosted:
people sign in, each works in workspaces they belong to, and the CLI and CI use workspace API
tokens. What needs this computer's folders (finding apps on disk, git hooks, reading an app's code)
is off.

## Settings

| Variable | |
|---|---|
| `EVALUTION_OIDC_ISSUER` | The issuer's URL, e.g. `https://login.example.com`. Setting it is what makes the server hosted. |
| `EVALUTION_OIDC_CLIENT_ID`, `EVALUTION_OIDC_CLIENT_SECRET` | The client registered with the issuer. |
| `EVALUTION_PUBLIC_URL` | Where people reach the server, e.g. `https://evals.example.com`. Its name is added to the names the server answers under. |
| `EVALUTION_SECRET_KEY` | A Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`). Encrypts model keys and seals sign-ins in progress. The server won't start hosted without it; keep it, or every stored key is lost. |
| `DATABASE_URL` | The Postgres database. |

Run it with `evalution serve --host 0.0.0.0` behind a proxy that terminates TLS.

## The issuer

Register a web client (authorization code, a client secret) with:

- redirect URI `<EVALUTION_PUBLIC_URL>/auth/callback`
- scopes `openid email profile`

Anything that speaks OpenID Connect discovery works; only the issuer's URL is configured. Sign-in
uses PKCE (S256), state and nonce, and accepts only id tokens signed with the issuer's published
public keys. No password is ever stored.

## Workspaces

Everyone gets a personal workspace at their first sign-in, theirs alone. Anyone can make more and
invite people with a link (`<public>/invite/<code>`, any number of uses, 7 days). Every member is
equal: anyone can rename the workspace, invite, or remove a member; removing yourself is leaving.
The last member can't leave, and a personal workspace can't be left or shared.

The page picks a workspace with the `X-Workspace` header; without it, the personal one. A workspace
someone isn't in answers 404.

## Tokens

`POST /v1/tokens {"name", "scope"?}` makes a token for the current workspace and shows it once
(Settings, Workspace, New token):

```sh
curl -H "Authorization: Bearer evt_..." https://evals.example.com/v1/targets
```

A token reaches its own workspace only, ignores `X-Workspace`, and can't manage tokens, members,
invites or model keys. Only its first characters are kept for display; the rest only as a hash.

Its scope is `all` (the default: everything above) or `traces`: it can only send spans
(`POST /v1/traces`) and gets 403 anywhere else. That is the one to put in an app's environment,
next to everything else the app holds.

## Traces

Apps send their traces to the workspace over standard OTLP/HTTP (protobuf or JSON, optionally
gzipped, 32 MB at most) at `<EVALUTION_PUBLIC_URL>/v1/traces`, with a workspace token. Any
OpenTelemetry exporter takes this from its environment; the header value is URL-encoded:

```sh
OTEL_EXPORTER_OTLP_ENDPOINT=https://evals.example.com
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20evt_...
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_RESOURCE_ATTRIBUTES=evalution.target=my-bot
OTEL_METRICS_EXPORTER=none
OTEL_LOGS_EXPORTER=none
```

`evalution.target` names the target the spans are for. The spans are kept in the database, each
workspace's apart, for `traces.keep_days` (default 30) and then dropped. A target can also read an
app's own Phoenix, set on the target. The page's Details show the endpoint.

## Model keys

Each person saves their own key (`PUT /v1/me/model-key`); it is encrypted, can be replaced or
deleted, and only its last four characters are ever shown. Work someone starts, a run included,
uses their key; work no one started (the daily drift check) or started with a token uses the key of
the workspace's earliest member who has one.

## Signed-in requests

The login cookie (`evalution_login`) is HttpOnly, SameSite=Lax, Secure on https, and lasts 30
days; signing out (`POST /auth/logout`) ends it on the server at once. A write signed in by the
cookie must carry `X-Requested-With: evalution`.
