# Deploying evalution

Hosted, evalution is one container (the page and the API, `server/Dockerfile`) and one Postgres. Sign-in and workspaces are in [hosting](hosting.md); this page is the database and the container.

## The database

Without `DATABASE_URL`, evalution keeps everything in SQLite, `data/evalution.db`. With it, in Postgres:

```sh
DATABASE_URL=postgresql://evalution:<password>@<host>:5432/evalution   # postgres:// works too
```

The driver is the `postgres` extra (`uv sync --project server --extra postgres`); the image has it. Any Postgres 14 or newer does, managed or your own. Other databases are refused.

Each start makes or updates the tables, all of it or none, in one transaction. Containers starting at once on the same database take turns, so a second one finds the work done. A database written by a newer evalution is refused: upgrade the image instead.

`evalution backup` copies the folders only, and says so. Back Postgres up with its own tools, e.g. `pg_dump`.

## With Docker Compose

`compose.yaml` runs the container, and with the `hosted` profile a Postgres next to it, its data in the `postgres` volume. Put the settings in `.env` beside it (git-ignored, never committed):

```sh
COMPOSE_PROFILES=hosted
POSTGRES_PASSWORD=<a long random password>
DATABASE_URL=postgresql://evalution:<the same password>@postgres:5432/evalution
# Encrypts each person's model key. Keep it: without it, stored keys can't be read.
# Make one: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
EVALUTION_SECRET_KEY=<key>
EVALUTION_ALLOWED_HOSTS=evals.example.com
EVALUTION_PUBLIC_URL=https://evals.example.com
EVALUTION_OIDC_ISSUER=https://issuer.example.com
EVALUTION_OIDC_CLIENT_ID=<client id>
EVALUTION_OIDC_CLIENT_SECRET=<client secret>
```

Then, as for a local one:

```sh
cp evalution.example.yaml evalution.yaml
mkdir -p data sets targets backups
docker compose up -d --build
```

The port stays on `127.0.0.1:8080`: put a proxy that serves HTTPS in front of it, under the name in `EVALUTION_PUBLIC_URL`. Health is `GET /v1/health`, which compose checks.

Without `COMPOSE_PROFILES=hosted`, `docker compose up` is the local, single-person evalution it was, on SQLite.

## Upgrading

```sh
(umask 077; docker compose exec -T postgres pg_dump -U evalution evalution > backups/evalution.sql)
docker compose stop evalution
docker compose build
docker compose run --rm evalution evalution upgrade
docker compose up -d
```
