# ruff: noqa: E501 - the app is a shell script, kept as it runs
"""A tiny compose app on real Docker, for the isolation tests that need one (``eval-test-*``).

* ``db``: Postgres (``postgres:16-alpine``), its data in a named volume.
* ``app``: an HTTP app on port 8080, a shell script behind busybox ``nc`` in the same image (it
  has ``psql``). ``GET /health`` asks the database ``SELECT 1``; ``GET /write/<marker>`` inserts a
  row; ``GET /has/<marker>`` answers ``yes`` or ``no``. With ``MODE=fresh`` each request opens its
  own connection, as most apps' pools reconnect; with ``MODE=held`` every request goes through one
  ``psql`` started with the app and never reopened, so an app that loses it stays broken until it
  is restarted.
* ``worker``: ``alpine:3.20`` mounting the named volume ``notes`` at ``/data``, where it writes a
  few files the first time it starts.

Nothing is built or pulled: both images must be on this computer already.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import httpx

IMAGES = ("postgres:16-alpine", "alpine:3.20")

HANDLER = r"""#!/bin/sh
read -r method path rest
while read -r line; do
  line=$(printf '%s' "$line" | tr -d '\r')
  [ -z "$line" ] && break
done
reply() {
  printf 'HTTP/1.0 %s\r\nContent-Type: text/plain\r\nContent-Length: %s\r\nConnection: close\r\n\r\n%s' "$1" "${#2}" "$2"
}
ask() {
  if [ "$MODE" = held ]; then
    tag="done-$$-$(date +%s)-$RANDOM"
    timeout 2 sh -c "printf '%s\n' \"$1;\" \"SELECT '$tag';\" > /run/app/q" || return 1
    i=0
    while [ $i -lt 30 ]; do
      grep -q "$tag" /run/app/out && break
      sleep 0.1
      i=$((i + 1))
    done
    grep -q "$tag" /run/app/out || return 1
    said=$(grep -B1 "$tag" /run/app/out | head -n1)
    case "$said" in ERROR*|FATAL*) return 1 ;; esac
    printf '%s\n' "$said"
  else
    psql -X -A -t -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null
  fi
}
case "$path" in
  /health)
    if ask "SELECT 1" >/dev/null; then reply "200 OK" "ok"; else reply "503 Service Unavailable" "down"; fi ;;
  /write/*)
    m=${path#/write/}
    if ask "INSERT INTO items (body) VALUES ('$m')" >/dev/null; then reply "200 OK" "kept"; else reply "503 Service Unavailable" "down"; fi ;;
  /has/*)
    m=${path#/has/}
    n=$(ask "SELECT count(*) FROM items WHERE body = '$m'") || { reply "503 Service Unavailable" "down"; exit 0; }
    if [ "$n" = 0 ]; then reply "200 OK" "no"; else reply "200 OK" "yes"; fi ;;
  *) reply "404 Not Found" "no such page" ;;
esac
"""

BOOT = r"""set -e
mkdir -p /run/app
printf '%s' "$HANDLER" > /run/app/h.sh
chmod +x /run/app/h.sh
psql -X -q -v ON_ERROR_STOP=1 -c "CREATE TABLE IF NOT EXISTS items (id serial PRIMARY KEY, body text)"
if [ "$MODE" = held ]; then
  rm -f /run/app/q /run/app/out
  mkfifo /run/app/q
  : > /run/app/out
  psql -X -A -t < /run/app/q > /run/app/out 2>&1 &
  tail -f /dev/null > /run/app/q &
fi
exec nc -lk -p 8080 -e /run/app/h.sh
"""

WORKER = r"""if [ ! -f /data/.made ]; then
  mkdir -p /data/sub
  echo kept > /data/a.txt
  echo inner > /data/sub/c.txt
  touch /data/.made
fi
exec sleep 2147483647
"""


def _compose_text(value: str) -> str:
    """*value* as compose reads it literally: ``$`` doubled, so nothing is interpolated."""
    return value.replace("$", "$$")


def compose(mode: str = "fresh", worker_mounts: tuple[str, ...] = ()) -> str:
    """The compose file, with the app in *mode* (``fresh`` or ``held``) and the worker mounting
    *worker_mounts* too (``./here:/there[:ro]``: folders shared with "the person's app")."""

    def q(text: str) -> str:
        return json.dumps(_compose_text(text))

    more = "".join(f"\n      - {q(m)}" for m in worker_mounts)

    return f"""\
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: throwaway
      POSTGRES_DB: app
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d app"]
      interval: 1s
      retries: 60
  app:
    image: postgres:16-alpine
    entrypoint: ["sh", "-c"]
    command: [{q(BOOT)}]
    init: true
    environment:
      MODE: {mode}
      PGHOST: db
      PGUSER: postgres
      PGPASSWORD: throwaway
      PGDATABASE: app
      HANDLER: {q(HANDLER)}
    ports:
      - "8080"
    volumes:
      - apphome:/var/lib/postgresql/data
    depends_on:
      db:
        condition: service_healthy
  worker:
    image: alpine:3.20
    command: ["sh", "-c", {q(WORKER)}]
    init: true
    volumes:
      - notes:/data{more}
volumes:
  pgdata:
  apphome:
  notes:
"""


def ready() -> bool:
    """Whether Docker runs here with both images already on this computer."""
    if shutil.which("docker") is None:
        return False
    try:
        if subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode:
            return False
        for image in IMAGES:
            found = subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, timeout=20
            )
            if found.returncode:
                return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True, timeout=120
    ).stdout


def sql(container: str, statement: str) -> str:
    return docker(
        "exec", container, "psql", "-U", "postgres", "-d", "app", "-X", "-q", "-A", "-t",
        "-v", "ON_ERROR_STOP=1", "-c", statement,
    ).strip()  # fmt: skip


def started(container: str) -> tuple[str, str]:
    """The container's id and when it last started: both the same means it wasn't restarted."""
    out = docker("inspect", "--format", "{{.Id}} {{.State.StartedAt}}", container).split()
    return out[0], out[1]


def get(url: str) -> httpx.Response:
    return httpx.get(url, timeout=10, trust_env=False)
