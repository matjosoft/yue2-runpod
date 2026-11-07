# syntax=docker/dockerfile:1
#
# Studio worker image (PRD section 8). Build from the repository root:
#
#   make build-worker                        # target yue2: the real engine (default)
#   TARGET=fake scripts/build_worker.sh      # target fake: no CUDA, no wheel (tests, dev)
#
# No secrets and no weights are baked in. WORKER_API_KEY (and HF_TOKEN, if ever needed)
# are set on the pod; the weights download to HF_HOME on the network volume at first start.

ARG UV_VERSION=0.13.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --- Locked third-party requirements of studio-worker (no dev tools, no local packages) ---
FROM python:3.12-slim-bookworm AS requirements
COPY --from=uv /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY contracts/pyproject.toml contracts/pyproject.toml
COPY worker/pyproject.toml worker/pyproject.toml
COPY server/pyproject.toml server/pyproject.toml
RUN uv export --frozen --no-dev --package studio-worker --no-emit-workspace --no-hashes \
        --no-header -o /requirements.txt

# --- Target fake: FakeEngine only (ENGINE=fake); no CUDA, no wheel ---
FROM python:3.12-slim-bookworm AS fake
COPY --from=uv /uv /uvx /bin/
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN uv venv /opt/venv
COPY --from=requirements /requirements.txt /tmp/requirements.txt
COPY contracts /src/contracts
COPY worker /src/worker
# --no-sources: the packages' [tool.uv.sources] point at the repo's uv workspace, which is
# not in the image; studio-contracts is satisfied by the local path given alongside.
RUN uv pip install --no-sources -r /tmp/requirements.txt /src/contracts /src/worker \
    && rm -rf /src /tmp/requirements.txt
ENV ENGINE=fake \
    JOBS_DIR=/workspace/jobs \
    PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn studio_worker.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]

# --- The real engine: CUDA runtime, the pinned yue2 wheel, then the worker ---
# CUDA 12.8 because the wheel pins torch==2.10.0, whose PyPI build uses the CUDA 12.8
# runtime (nvidia-cuda-runtime-cu12==12.8.90). Host driver: 570.124.06 or newer
# (docs/runpod.md). Ubuntu 24.04 ships Python 3.12.
FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04 AS yue2
ARG YUE2_WHEEL_VERSION=0.1.5
ARG YUE2_WHEEL_SHA256=8801e2c0d969db02df78d2994150b4ccd86077d87c24fdb8509b1f6f31462641
ARG YUE2_REPO_REVISION=8f24312f187763b9854adeec87e437e2b03bbff1
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3.12-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /bin/
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN uv venv --python /usr/bin/python3.12 /opt/venv

# 1. Download the yue2 wheel from the model repo with the pinned hub client and check it
#    against the recorded sha256 (docs/yue2-api-notes.md section 1).
ARG WHEEL=yue2_infer-${YUE2_WHEEL_VERSION}-py3-none-any.whl
RUN uv pip install huggingface-hub==0.36.2 \
    && HF_HOME=/tmp/hf-build hf download m-a-p/YuE2-3B "${WHEEL}" \
        --revision "${YUE2_REPO_REVISION}" --local-dir /tmp/wheel \
    && echo "${YUE2_WHEEL_SHA256}  /tmp/wheel/${WHEEL}" | sha256sum -c - \
    && rm -rf /tmp/hf-build /tmp/wheel/.cache

# 2. One resolve over the wheel (torch, transformers, ...), the worker's locked
#    requirements and the two local packages, so any pin conflict fails the build.
COPY --from=requirements /requirements.txt /tmp/requirements.txt
COPY contracts /src/contracts
COPY worker /src/worker
RUN uv pip install --no-sources "/tmp/wheel/${WHEEL}" -r /tmp/requirements.txt \
        /src/contracts /src/worker \
    && rm -rf /src /tmp/wheel /tmp/requirements.txt

ENV ENGINE=yue2 \
    HF_HOME=/workspace/hf \
    JOBS_DIR=/workspace/jobs \
    PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn studio_worker.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
