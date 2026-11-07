#!/usr/bin/env bash
# Run a fake-engine worker and the local server together (make run-fake).
#
# The worker gets a fresh random API key (unless WORKER_API_KEY is set, as the Playwright
# test does) and a temporary jobs folder; the server is pointed at it through
# STUDIO_WORKER_URL and STUDIO_WORKER_TOKEN. The server also serves the built web UI
# (web/dist; make run-fake builds it first) at http://127.0.0.1:$STUDIO_PORT. Unless
# STUDIO_DATA_DIR is set, the server's library also lives in a temporary folder, so each
# run starts empty. Ctrl+C stops both and removes the temporary folders.
#
#   WORKER_API_KEY  worker token (default: random)
#   WORKER_PORT   worker port (default 8000)
#   STUDIO_PORT   server port (default 8080)
#   FAKE_STAGE_DELAY_S, FAKE_LOAD_DELAY_S   slow the fake model down (default 0.5 and 0)
set -euo pipefail
cd "$(dirname "$0")/.."

worker_port="${WORKER_PORT:-8000}"
tmp="$(mktemp -d)"
key="${WORKER_API_KEY:-$(uv run --locked python -c 'import secrets; print(secrets.token_hex(32))')}"

cleanup() {
  trap - EXIT INT TERM
  kill $(jobs -p) 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$tmp"
}
trap cleanup EXIT INT TERM

ENGINE=fake WORKER_API_KEY="$key" JOBS_DIR="$tmp/jobs" HF_HOME="$tmp/hf" \
  FAKE_STAGE_DELAY_S="${FAKE_STAGE_DELAY_S:-0.5}" FAKE_LOAD_DELAY_S="${FAKE_LOAD_DELAY_S:-0}" \
  uv run --locked uvicorn studio_worker.main:app --host 127.0.0.1 --port "$worker_port" &

STUDIO_DATA_DIR="${STUDIO_DATA_DIR:-$tmp/data}" \
  STUDIO_WORKER_URL="http://127.0.0.1:$worker_port" STUDIO_WORKER_TOKEN="$key" \
  uv run --locked python -m studio_server &

echo "run-fake: worker on http://127.0.0.1:$worker_port, server on http://127.0.0.1:${STUDIO_PORT:-8080}" >&2
# Stop both as soon as either exits.
wait -n
