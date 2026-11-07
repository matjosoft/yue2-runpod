# Decisions

Short records of choices the PRD leaves open (PRD §13 working rules). Newest last.

## M0 (2026-11-07)

- **The repo root is the `studio/` folder.** The PRD §10 layout is applied at the root; there is no nested `studio/` directory.
- **Type checker: mypy `--strict`** with the pydantic plugin, over `contracts/`, `worker/`, `server/` (packages and tests) and `scripts/`. The PRD allows pyright or mypy. mypy needs only Python and has a pydantic plugin. `yue2.*` is marked untyped and missing, because it is installed only in the worker image.
- **Node package manager: pnpm**, with a single project in `web/` and no root `package.json`.
- **Node version support.**
  - Web tooling is held at majors that support Node `^20.19 || >=22.12`: Vite 7, `@vitejs/plugin-react` 5, Vitest 3, jsdom 26 and ESLint 9.
  - TypeScript is held at 5.9, because typescript-eslint and openapi-typescript do not yet support TypeScript 7.
  - Why not the newer majors: Vitest 5, jsdom 31 and ESLint 10 exclude odd-numbered Node releases such as 23.x, which is what this machine has.
  - Upgrade these majors once the owner is on Node 22 or 24 LTS.
- **The OpenAPI exporter lives in `scripts/export_openapi.py`**, not in `contracts/`.
  - The documents come from the FastAPI apps. A contracts package that imports the apps would invert the dependency direction.
  - `scripts/gen_api_types.sh` exports both documents and generates `web/src/api/schema.d.ts` from `server.json`, the only API the web app calls.
  - `--check` regenerates into a temp dir and diffs; `make test` runs it.
- **`yue2_infer` and torch are not workspace dependencies.** The wheel is not on PyPI, and it hard-pins `torch==2.10.0`, which would pull multi-GB CUDA libraries onto GPU-less dev machines. The worker Dockerfile (M4) installs it from the pinned file. See `docs/yue2-api-notes.md`.
- **Extra make target `typecheck`** (mypy and `tsc --noEmit`). CLAUDE.md names it, and `lint` calls it, so `lint` still covers everything PRD §10 lists.
- **Targets that cannot work yet are explicit stubs.** `run-fake` (needs M1 and M2) and `build-worker` (needs M4) exit 1 with a message that names the milestone.
- **Playwright is added in M3**, together with the first UI it can test. Until then `make test` runs pytest, Vitest and the contracts check.
- **Ruff never touches Markdown** (`extend-exclude = ["*.md"]`). This ruff version formats Python code blocks inside `.md` files, and the PRD's interface blocks must stay verbatim.
- **`make dev`** runs the server on `127.0.0.1:8080` with reload and Vite on `127.0.0.1:5173`. Vite proxies `/api` to the server.
