#!/usr/bin/env bash
# Build the worker image (PRD section 8). Run from anywhere; the build context is the repo root.
#
#   scripts/build_worker.sh                  # studio-worker:<version>, target yue2
#   TARGET=fake scripts/build_worker.sh      # studio-worker:<version>-fake, no CUDA/wheel
#   IMAGE=ghcr.io/<owner>/studio-worker scripts/build_worker.sh
#
# YUE2_WHEEL_VERSION and YUE2_WHEEL_SHA256 override the pinned wheel (change both together).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${TARGET:-yue2}"
image="${IMAGE:-studio-worker}"
version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/worker/studio_worker/__init__.py")"
if [[ -z "$version" ]]; then
  echo "build-worker: could not read __version__ from worker/studio_worker/__init__.py" >&2
  exit 1
fi

case "$target" in
  yue2) tag="$image:$version" ;;
  fake) tag="$image:$version-fake" ;;
  *) echo "build-worker: TARGET must be yue2 or fake, not '$target'" >&2; exit 1 ;;
esac

args=()
[[ -n "${YUE2_WHEEL_VERSION:-}" ]] && args+=(--build-arg "YUE2_WHEEL_VERSION=$YUE2_WHEEL_VERSION")
[[ -n "${YUE2_WHEEL_SHA256:-}" ]] && args+=(--build-arg "YUE2_WHEEL_SHA256=$YUE2_WHEEL_SHA256")

if ! command -v docker >/dev/null 2>&1; then
  echo "build-worker: docker is not installed or not on PATH" >&2
  exit 1
fi

echo "build-worker: building $tag (target $target)"
exec docker build -f "$root/worker/Dockerfile" --target "$target" -t "$tag" ${args[@]+"${args[@]}"} "$root"
