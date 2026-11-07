# Studio

Studio turns a style description and lyrics into a song. YuE2-3B generates it on a rented RunPod GPU, and you control it from a web app on your own machine. The specification is [docs/PRD.md](docs/PRD.md).

> **Status: milestone M0 (scaffold and spike).** The repository, tooling and contracts pipeline exist. The worker, server and web app are empty skeletons, and nothing generates music yet.

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
make setup      # install Python and Node dependencies
make lint       # ruff, mypy (strict), tsc --noEmit, ESLint
make test       # pytest, Vitest, and a check that generated contracts are up to date
make contracts  # regenerate contracts/openapi/*.json and web/src/api/schema.d.ts
make dev        # server on http://127.0.0.1:8080 (reload) + Vite on http://127.0.0.1:5173
```

`make run-fake` and `make build-worker` are placeholders. They become available in milestones M2 and M4.
