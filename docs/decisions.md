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

## M1 (2026-11-07)

Contract and scope (owner-approved):

- **`cfg_scale` is now `gt=0, le=20`.** The engine rejects values above 20 (yue2-api-notes §3), so a bad value now fails at submit with 422, not later as `engine_error`. PRD §6 is updated.
- **`DELETE /v1/jobs/{id}` is in M1; `POST /v1/jobs/{id}/cancel` is in M5** (W-10). `ctx.cancel_requested()` exists and `song.generate` checks it between stages, but nothing sets it until M5.

Worker API details the PRD leaves open:

- **`GET /v1/jobs` returns `{"jobs": [...]}`** (newest first, at most 50). `DELETE` returns 204 with no body.
- **Submit checks run in this order:**
  1. the body;
  2. the operation and its params (422);
  3. idempotency (a known `client_request_id` returns the existing job with 202, whatever its params or state);
  4. readiness (503 with `Retry-After: 10`);
  5. the queue cap (429).

  The idempotency index is rebuilt from disk at startup. Deleting a job frees its id.
- **The queue cap counts waiting jobs only.** The running job is not counted, so with `MAX_QUEUE=4` one job runs, four wait, and the next submit gets 429. This matches AT-6 ("`MAX_QUEUE` + 2 ... the excess").
- **Default `retryable` values.**
  - `true`: `queue_full`, `not_ready`, `worker_restarted`, `oom`.
  - `false`: `engine_error`, `internal`, and the 4xx codes.
- **Unmapped HTTP errors still get the envelope.**
  - 405 is reported as `not_found`.
  - A malformed `Range` gives 422 `validation_error`.
  - An unsatisfiable `Range` gives 416 with code `validation_error` and a `Content-Range` header.
  - Validation `details.fields` lists `{loc, msg}` only. The rejected input is never echoed back.
- **Model states are `loading`, `ready` and `error`**, the §8 wording. `status.gpu` is `null` until the real engine adds GPU probing (M4).
- **`client_request_id` is any string of 1 to 128 characters.** The server will send UUIDs.
- **Job ids are `job_` plus a ULID**, generated with the stdlib and monotonic, so ids sort by creation.
- **Job fields.**
  - `params` is the validated params, without absent optionals (so it has no `seed` unless one was sent).
  - The seed actually used goes to `params_resolved.seed`; the worker draws it from 0 to 2³¹−1.
  - The timing keys are `queue_s`, then `<stage>_s` for each stage, then `total_s`.

Worker internals:

- **`ctx.job_dir` is the job's `artifacts/` folder.** Operations write there, and `add_artifact` paths are relative to it. `job.json` sits one level up, where operations cannot reach it.
- **A failed or canceled job keeps no output.** Its `artifacts/` folder is emptied and its manifest is `[]`. `engine_error` details are `{type, message}`, with the message capped at 500 characters.
- **`ArtifactSpec` (a dataclass) and `EngineOOM` live in `engines/base.py`.** The PRD uses `ArtifactSpec` without defining it. The M4 engine must translate torch's OOM into `EngineOOM`, so that only `yue2.py` knows torch's exception type.
- **`error_info()` lives in `jobs/errors.py`**, which is FastAPI-free. That keeps `jobs/`, `operations/` and `engines/` free of FastAPI imports; a test enforces this.
- **Params limits are module constants** in `studio_contracts/song.py`, not runtime settings, so the published JSON Schema stays stable.
- **Model compatibility rules.** Request models (`SongParams`, `JobSubmit`) reject unknown fields. Response models ignore them, so later additive fields don't break an older server.
- **`studio_worker.main:app` is created lazily** (module `__getattr__`), so tests and the OpenAPI exporter can import the module without environment variables.
- **`ENGINE` defaults to `yue2`**, as in PRD §8. Until M4 that raises a clear error, so local runs set `ENGINE=fake`.
- **Two extra environment variables, for `ENGINE=fake` only:** `FAKE_LOAD_DELAY_S` and `FAKE_STAGE_DELAY_S`. M2 and M3 tests need them, for AT-5 and AT-12 over real HTTP.
- **Logs are JSON only.**
  - Uvicorn's loggers are routed through the JSON handler.
  - Its access log is disabled, because the request middleware already logs every request with an `X-Request-ID`.
  - Lyrics, style and the token are never logged; a test checks this.
- **Worker dependencies `numpy>=2.2,<2.3` and `soundfile>=0.13.1,<0.14`** are there for the fake engine's FLAC. The bounds match the yue2 wheel's pins, so the M4 image resolves. soundfile has no type information, so mypy ignores its imports.
- **Shared worker test helpers live in `worker/tests/worker_support.py`.** pytest's `pythonpath` includes `worker/tests`, because `--import-mode=importlib` makes `conftest` unimportable.
