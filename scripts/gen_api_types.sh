#!/usr/bin/env bash
# Regenerate contracts/openapi/{worker,server}.json and web/src/api/schema.d.ts.
#
#   scripts/gen_api_types.sh          write the generated files in place (make contracts)
#   scripts/gen_api_types.sh --check  generate into a temp dir and fail if the committed
#                                     files differ (part of make test)
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"

generate() { # $1: OpenAPI output dir, $2: schema.d.ts output path (both absolute)
  uv run --locked python scripts/export_openapi.py --out "$1"
  pnpm -C web exec openapi-typescript "$1/server.json" --output "$2"
}

if [[ "${1:-}" == "--check" ]]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  generate "$tmp/openapi" "$tmp/schema.d.ts" >/dev/null 2>&1 \
    || { echo "contracts check: generation failed; run make contracts to see why" >&2; exit 1; }
  status=0
  diff -u contracts/openapi/worker.json "$tmp/openapi/worker.json" || status=1
  diff -u contracts/openapi/server.json "$tmp/openapi/server.json" || status=1
  diff -u web/src/api/schema.d.ts "$tmp/schema.d.ts" || status=1
  if [[ $status -ne 0 ]]; then
    echo "contracts check: generated files are out of date; run make contracts" >&2
    exit 1
  fi
  echo "contracts check: generated files are up to date"
else
  generate "$root/contracts/openapi" "$root/web/src/api/schema.d.ts"
fi
