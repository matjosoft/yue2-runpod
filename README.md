# Studio

Studio turns a style description and lyrics into a song. YuE2-3B generates it on a rented RunPod GPU, and you control it from a web app on your own machine. The specification is [docs/PRD.md](docs/PRD.md).

> **Status: milestone M3 (web app).** The web app, the local server and the worker run end to end with a fake engine that needs no GPU. The real YuE2 engine and the worker image (M4) are not built yet.

## Layout

| Path | What |
| --- | --- |
| `contracts/` | Shared Pydantic models (`studio_contracts`) and the generated OpenAPI documents in `contracts/openapi/` |
| `worker/` | GPU worker (`studio_worker`). It runs on RunPod; from M1, a fake engine runs it without a GPU |
| `server/` | Local server (`studio_server`): settings, library and worker client |
| `web/` | React app (Vite, strict TypeScript) |
| `scripts/` | Contract generation and, later, the worker build and smoke scripts |
| `docs/` | PRD, YuE2 API notes and decisions |

## Prerequisites

- [uv](https://docs.astral.sh/uv/). It provides Python 3.12 if you don't have it.
- Node.js `^20.19` or `>=22.12` (an LTS release is recommended) and [pnpm](https://pnpm.io/) 10.
- `make` and `bash`. On Windows, use WSL or Git Bash.

## Commands

Run these from the repository root.

```bash
make setup      # install Python and Node dependencies, and Playwright's Chromium
make lint       # ruff, mypy (strict), tsc --noEmit, ESLint
make test       # pytest, Vitest, Playwright, and a check that generated contracts are up to date
make contracts  # regenerate contracts/openapi/*.json and web/src/api/schema.d.ts
make dev        # server on http://127.0.0.1:8080 (reload) + Vite on http://127.0.0.1:5173
make run-fake   # builds the web app; fake-engine worker on :8000 + server and UI on :8080
```

`make build-worker` is a placeholder until milestone M4.

## Running the worker with the fake engine

The fake engine walks through every stage and writes a short 48 kHz stereo FLAC, so the whole worker API can be tried without a GPU. `ENGINE=yue2` (the default) arrives in M4.

```bash
export WORKER_API_KEY=$(openssl rand -hex 32)   # at least 32 characters
ENGINE=fake JOBS_DIR=./.jobs uv run --locked uvicorn studio_worker.main:app --port 8000

curl localhost:8000/healthz
curl -H "Authorization: Bearer $WORKER_API_KEY" localhost:8000/v1/capabilities
curl -H "Authorization: Bearer $WORKER_API_KEY" -H 'Content-Type: application/json' \
  -d '{"client_request_id": "demo-1", "operation": "song.generate",
       "params": {"style": "jazz-funk", "lyrics": "[Verse]\nla la"}}' \
  localhost:8000/v1/jobs
```

`FAKE_LOAD_DELAY_S` and `FAKE_STAGE_DELAY_S` slow the fake model down, which is useful for seeing the `loading` state and stage progress. The worker's environment variables are listed in PRD section 8.

## Using the web app with the fake worker

```bash
make run-fake
```

Then open <http://127.0.0.1:8080>. The server is already pointed at the fake worker, so the status pill turns green, and Create → Example → Create makes a short test song (a tone, not music). Settings (the pill, or the nav link) is where you enter a real worker's URL and token later.

For UI work, `make dev` runs the server with reload on :8080 and Vite on <http://127.0.0.1:5173>, which proxies `/api` to the server. Point it at a worker in Settings, or run a fake worker on :8000 as shown above.

The end-to-end test (`pnpm -C web test:e2e`, part of `make test`) builds the UI and starts its own `run-fake` stack on ports 18000 and 18080, so it does not clash with a running `make dev` or `make run-fake`. It needs Chromium, which `make setup` installs; on a fresh Linux machine Chromium may also need system libraries (`sudo pnpm -C web exec playwright install-deps chromium`).

## Running the local server against the fake worker

`make run-fake` starts a fake-engine worker with a fresh random key (or `WORKER_API_KEY`, if set) and the local server already pointed at it. Both use temporary folders, so each run starts with an empty library; set `STUDIO_DATA_DIR` to keep one. Ctrl+C stops both.

```bash
make run-fake

curl -X POST localhost:8080/api/worker/check          # {"state": "ready", ...}
curl -H 'Content-Type: application/json' \
  -d '{"operation": "song.generate", "request": {"style": "jazz-funk", "lyrics": "[Verse]\nla la"}}' \
  localhost:8080/api/generations                       # 201, status "queued", seed filled in
curl localhost:8080/api/generations                    # the library, newest first
```

To point the server at another worker, use `PUT /api/settings` with `worker_url` and `token`, or set `STUDIO_WORKER_URL` and `STUDIO_WORKER_TOKEN`. The token is write-only: no endpoint returns it. The server's settings are listed in [.env.example](.env.example) and PRD section 10. It stores its database and downloaded files in `STUDIO_DATA_DIR` (default `~/.studio`). To create or upgrade the database by hand:

```bash
STUDIO_DATA_DIR=~/.studio uv run --locked alembic -c server/alembic.ini upgrade head
```
