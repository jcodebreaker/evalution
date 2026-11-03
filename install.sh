#!/bin/sh
# Everything evalution needs, from a fresh clone or after pulling new code. Run from anywhere:
# ./install.sh. It pulls nothing, and leaves evalution.yaml, data/, sets/ and targets/ as they are.
set -eu
cd "$(dirname "$0")"
need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Needs $1: $2" >&2; exit 1; }
}
need uv "https://docs.astral.sh/uv/"
need npm "https://nodejs.org"
uv sync --project server --group traces
npm ci --prefix web
npm run build --prefix web
if [ -f evalution.yaml ]; then
  echo "Kept your evalution.yaml. To back up and bring stored runs up to date: evalution upgrade"
else
  cp evalution.example.yaml evalution.yaml
fi
echo
uv run --project server evalution doctor || true
echo
echo "Start it:              uv run --project server evalution serve"
echo "Or keep it running:    uv run --project server evalution service install"
