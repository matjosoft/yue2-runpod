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

## M2 (2026-11-07)

Contract and scope (owner-approved):

- **A submit the worker rejects, or cannot be reached for, keeps no record.**
  - `POST /api/generations` stores the record, submits, and on a definitive failure deletes the record and returns the error envelope. That way the UI keeps the typed input (AT-3).
  - HTTP status: the worker's 422, 429 and 503 pass through. `unauthorized`, `worker_unreachable`, `incompatible_worker` and any other backend failure return 502. `not_configured` returns 409.
- **The server validates the envelope only.**
  - `title` is 0 to 200 characters, and `operation` must be in the cached capabilities.
  - Params are validated by the worker. Its 422 is relayed, with `params.<field>` renamed to `request.<field>`.
  - The seed is filled generically: if the operation's `params_schema` has an integer `seed` and none was sent (or it was `null`), the server picks one within the schema's `minimum` and `maximum`. No song-specific code is involved.
- **The worker check has seven states**: S-5's five, plus `incompatible` (API major differs) and `error` (the model failed to load).
- **Deleting a generation that is `queued`, `running` or `downloading` returns 409 `conflict`** until cancel exists (M5).
- **`make run-fake` exists from M2** (`scripts/run_fake.sh`).

Server API details the PRD leaves open:

- **`POST /api/generations`** takes `{title?, operation, request}` and returns 201 with the generation. `request` has the same shape as `Generation.request`, so a stored request can be sent again unchanged.
- **`GET /api/generations?limit=&cursor=`** returns `{items, next_cursor}`, newest first.
  - The limit defaults to 20, with a maximum of 100.
  - The cursor is opaque to clients; it is the last item's id. Ids are `gen_` plus a monotonic ULID, so id order is creation order.
- **Settings.**
  - `GET /api/settings` returns `{worker_url, token_set}`.
  - In the `PUT` body, an omitted field keeps the saved value, `null` clears it (the environment value then applies again), and a value replaces it.
  - The URL must be `http(s)://`. The token is 1 to 512 characters; the worker enforces its own minimum.
- **Check and health.**
  - `POST /api/worker/check` always answers 200, with `{state, error, worker: {api_version, worker_version, model_state} | null}`.
  - `GET /api/health` is `{ok: true, worker: <last check>}`. It never calls the worker: the poller loop refreshes the check every 5 s (`check_interval_s`), which keeps health under the 200 ms target and the pill within U-5's 10 s.
  - Until the first check, the state is `unreachable` with the message "not checked yet".
- **`GET /api/capabilities`** returns the cached copy. It fetches once if nothing is cached, and stays available when the worker goes away (S-6). A settings change drops the cache.
- **Errors in the server API** use `ServerErrorInfo`, whose `code` is `ErrorCode | ClientErrorCode`. The client codes are a separate enum, so the worker's OpenAPI never lists codes the worker cannot send. Default `retryable` is `true` for `worker_unreachable` and `download_failed`, plus the worker's defaults.
- **Default title**: `Untitled YYYY-MM-DD HH:MM` in the server's local time.

Backend and poller:

- **Backend types the PRD uses without defining them:**
  - `JobRef` is `{job_id}`.
  - `JobView` is backend-neutral: `state`, `stage`, `stages`, `progress`, `timings`, `params_resolved`, `error` and `artifacts`.
  - `BackendError(info, http_status)` is raised for every failure, with the worker's own envelope when it sent one.
- **`MusicBackend`** is the PRD's protocol. The only change is `params: dict` written as `dict[str, Any]`, because mypy strict requires it. Closing connections uses a separate `Closeable` protocol, so `MusicBackend` itself is unchanged.
- **Retries.** At most 3 retries, with backoff from `retry_base_s` (0.5 s) doubling each time. `Retry-After` is not honoured, so a submit to a worker that is still loading answers in about 3.5 s, not 30. A proxy's non-JSON 502, 503 or 504 becomes `worker_unreachable`.
- **Errors while polling.**
  - A worker 404 for a known job fails the generation with `not_found`.
  - These count toward `STUDIO_UNREACHABLE_GRACE_S` instead of failing at once: `worker_unreachable`, `not_configured`, `unauthorized`, `queue_full`, `not_ready` and `internal`.
  - The grace timer is per generation and kept in memory, so it restarts on a server restart.
- **A record with no worker job** (the server stopped between storing and submitting) is resubmitted by the poller with the same `client_request_id`. The worker treats that as the same job. Records whose submit is still in flight in a request handler are skipped.
- **Downloads.**
  - At most 2 at once, across all generations.
  - A file that fails the size or sha256 check is fetched again up to 3 times, then the generation fails with `download_failed`.
  - If every failure was a lost connection, the grace period applies instead.
  - Verified files that were already stored are kept.
  - Downloads run inside the tick, so a long download delays polling of other generations. That is acceptable for one user.
- **`artifacts_json` holds the full worker manifest** (with sha256) plus a `stored` flag per file. The API lists only stored files, each with its `url`. With `STUDIO_DOWNLOAD_INTERMEDIATES=false`, `intermediate` files stay in the manifest but are not downloaded.

Storage and database:

- **Synchronous SQLAlchemy 2 on SQLite** (WAL, `busy_timeout` 5 s).
  - Timestamps are stored as fixed-width ISO 8601 UTC text, so they sort as text.
  - Startup runs `alembic upgrade head` from code. `server/alembic.ini` lets the CLI do the same, reading the database path from `STUDIO_DATA_DIR`.
- **`studio.db` is chmod 0600 on POSIX**, because it holds the token in plain text. On Windows no permissions are set.
- **Downloads go to `<generation>/.partial/`**, are verified, and are moved into place with `os.replace`. Leftover `.partial` folders are removed at startup. `.partial` is never a valid artifact name.

Module layout:

- **Core has more modules than the PRD's four.** Besides `db`, `storage`, `poller` and `backend/` there are:
  - `worker.py`: settings precedence, the backend for the current settings, the check and the capabilities cache;
  - `generations.py`: the API view, seed filling, state mirroring and submit;
  - `runtime.py`, `errors.py`, `logs.py` and `ids.py`.

  The poller and two or more features share each of these, and features may not import each other.
- **`Feature` lives in `features/base.py`.** Each feature is one module (`features/<name>/__init__.py`).
- **The server never imports `studio_worker`.** A test enforces this. So `UlidFactory`, the JSON log formatter, the error handlers and the Range check are small copies of the worker's.
- **The server lowers the `httpx` logger to WARNING**, because it would otherwise log every poll.

Tooling:

- **mypy runs twice** (`make typecheck`): once with the configured files, then once on `server/tests`. Both test folders have a `conftest.py`, and mypy cannot hold two top-level modules with the same name.
- **Server tests use the real worker app with `FakeEngine`, in-process.** Server test helpers live in `server/tests/server_support.py`, and `server/tests` is on pytest's `pythonpath`. `SwitchableTransport` sits between server and worker to simulate an outage, corrupt downloads and count parallel downloads. AT-5 stops the server through its lifespan (a clean stop, not a `kill -9`), then starts a new app on the same data dir.
- **`python -m studio_server`** runs the server on `STUDIO_HOST`:`STUDIO_PORT`. `make run-fake` uses it.

## M3 (2026-11-07)

Web app structure:

- **`operationViews` has this shape** (the PRD names it but gives no type):
  - `Record<string, { Summary, Result }>`; both components take `{ generation }`.
  - `Summary` is one line of operation-specific content in a library row (for a song, the first 80 characters of the style). `Result` is the body of a succeeded generation.
  - `viewFor(op)` returns a generic fallback (a file list) for an operation with no view.
  - The generic parts of the result card (queued, running, downloading, failed, canceled, Reuse, Retry) and of the library row (title, status, time, player, Reuse, Delete) are shared. The player is picked by artifact role `primary_audio`, never by file name.
- **Modules are hidden only when capabilities have loaded and lack the operation.** While `/api/capabilities` is loading or failing (first run, no worker yet), every module shows. Otherwise Create would disappear exactly when the user needs Settings.
- **Settings is a route (`/settings`) shown as a modal dialog**, so the `FeatureModule` shape stays as the PRD has it. The status pill links to it.
- **Test connection saves first, then checks**, because `POST /api/worker/check` tests the saved settings. Its result also updates the pill at once.
- **Save also re-checks the worker** (`POST /api/worker/check`) in the background, and the pill shows the result. The server does not refresh its cached check when settings change, so without this the pill would show the old state for up to about 15 s.
- **Save and Test stay disabled until the saved settings have loaded.** Until then the URL field is not known, and sending it blank would clear the saved URL.
- **A failed poll is shown.** The pill turns red ("App server unreachable") as soon as a health poll fails, even after earlier good answers. The result card and the library keep their last state and show the error above it.
- **The "http:// only for localhost" rule is enforced in the UI only.** Allowed hosts are `localhost`, `127.0.0.1` and `[::1]`. The server's `SettingsUpdate` still accepts any `http(s)://` URL; tightening it would be a contract change.
- **Error messages.**
  - `components/errorMessages.ts` is typed `Record<ErrorCode | ClientErrorCode | "server_unreachable", string>`, so `tsc` fails when a contract code lacks a message. A test also checks the enums in `contracts/openapi/server.json`.
  - The PRD §9 table has no text for `not_found`, `conflict` or `internal`, so these were added: "This could not be found. It may have been deleted.", "That isn't possible while the generation is in its current state." and "Something unexpected went wrong. Try again."
  - `server_unreachable` is UI-only, for when the local server itself does not answer.
- **Form and request mapping.**
  - Blank seed and blank CFG scale are left out of the request: the server fills a random seed, and the model default applies for CFG.
  - The UI checks only that style and lyrics are non-empty, and that seed and CFG scale are numbers. Lengths and ranges are the worker's rules, and its 422 is shown inline per field.
  - A blank title is left out, so the server's `Untitled <date time>` applies.
- **Drafts and Reuse.**
  - The draft (`localStorage`) is operation-neutral (`{title, request}`) and keeps raw text, so a half-typed number survives a reload.
  - "Reuse settings" writes the stored request into the draft and opens Create, with the title left blank. Retry sends the stored title, operation and request unchanged, so the seed is the same too.
- **The elapsed timer counts from `created_at`.** The generation object has no start time, so the timer includes queue time and survives a reload.
- **Library rows that are not terminal have Delete disabled**, because the server answers 409 until cancel exists (M5). The list polls every 1.5 s while any row is in progress.
- **The pill polls every 10 s, as U-5 says; the worst case is about 15 s.** The server refreshes its check every 5 s (M2), so a state change can take up to about 15 s to show, not 10 s as the M2 note said. Saving settings, or testing the connection, updates the pill at once. A change on the worker side, such as the pod stopping, still takes up to about 15 s to show.

Server and tooling:

- **The server serves the built UI** (PRD §9: "served by the local server in normal use"; no milestone assigned it).
  - `core/webui.py` adds a `GET /{path}` catch-all after the API routers. It returns a file from `web/dist` if one exists there, and otherwise `index.html`, so deep links work.
  - `/api/...` keeps its JSON 404. Paths that resolve outside the build folder are never served.
  - If the UI is not built, the route answers 404 with a hint.
  - The route is excluded from OpenAPI, so the contract is unchanged.
  - `ServerSettings.web_dir` (default `<repo>/web/dist`) is a settings field, not an environment variable.
  - A side effect: an unknown non-GET path now gets 405 instead of 404, still with the envelope.
- **`make run-fake` builds the UI first.** `scripts/run_fake.sh` uses `WORKER_API_KEY` from the environment when it is set, so the end-to-end test knows the token it types into Settings.
- **Playwright.**
  - `web/e2e/app.spec.ts` runs against its own `run-fake` stack on ports 18000 and 18080, with `FAKE_STAGE_DELAY_S=2` (about 10 s per song).
  - AT-1 follows the current stage until the song is done and requires it to move only forward, through at least 3 stages. It reloads mid-run and requires the stage list to come back.
  - It uses Chromium only, and `make test` runs it.
  - The browser cannot report a file's native sample rate, so "48 kHz stereo" is checked by reading the FLAC STREAMINFO header. "Plays" is checked in the browser (`duration > 0`, `currentTime > 0`, no media error).
- **Dependencies.**
  - React Router 7, TanStack Query 5, openapi-fetch 0.17, Tailwind CSS 4 (Vite plugin) and Playwright 1.65.
  - `@testing-library/jest-dom` is held at 6, because 7 requires Node 22 and the project supports Node 20.19.
  - The client calls `globalThis.fetch` on every request, so Vitest can stub it, and query `AbortSignal`s are not passed to fetch (jsdom and Node disagree on the type).

## M4 (2026-11-07)

- **Staged engine.** `Yue2Engine` runs the four stages and then rebuilds the `SongResult` exactly as `YuE2Pipeline.__call__` does. It uses only public names (yue2-api-notes §3, "Staged reconstruction"). The single-call `generate()` is also implemented and tested, so switching to it means changing the `staged` constructor default. No env var was added for this, because the PRD has none.
- **Cancellation inside a stage is not wired.** The `SongEngine` interface in PRD §7 has no way to pass a cancel callback, so the engine does not pass `cancelled=` to yue2. Cancellation stays at stage boundaries, which is what W-10 requires. Passing it through is an additive change for M5 or later.
- **Load options.**
  - `device="cuda"` (W-1) and `progress=False`.
  - `cache_dir=$HF_HOME/hub`, which is the hub cache `huggingface_hub` would use anyway.
  - `verify_hashes` is left at `True`, so every start re-hashes about 7.8 GB. It is the integrity record, and the cost is documented in docs/runpod.md.
  - `vae` is passed only when `YUE2_VAE_REPO` is set.
- **OOM detection without importing torch.** Any exception whose class (or a parent class) is named `OutOfMemoryError`, or whose message contains "out of memory", becomes `EngineOOM`, which the job reports as `oom`. Everything else propagates as `engine_error`.
- **Engine identity.** In capabilities, `models` shows `repo@revision` when a revision is pinned. When `YUE2_VAE_REPO` is unset, `vae` shows the package default, `m-a-p/YuE2-Vae`. `package_version` comes from the installed `yue2-infer` distribution, or `unknown`.
- **GPU in `/v1/status`** is read from `nvidia-smi`. It needs no framework import, is cached for 5 s, runs off the event loop, and gives `null` on any failure. It is enabled only when `ENGINE=yue2`.
- **`engine.json`.** The engine writes this extra artifact (role `other`) holding its `info()`: kind, `yue2-infer` version and model repos/revisions. PRD §11 wants the wheel version recorded in artifacts, and yue2's own files record only a hash of its source.
- **Artifact roles** follow the yue2-api-notes §7 suggestion. Unknown `.npy` files are `intermediate`; any other unknown file is `other`, because W-5 says to keep everything.
- **`STUDIO_DOWNLOAD_INTERMEDIATES` stays `true`.** The intermediates measured at their largest documented shapes total about 2.4 MB per job (yue2-api-notes OQ-4), far below the 100 MB threshold.
- **Image.**
  - Multi-stage `worker/Dockerfile`, with targets `fake` (`python:3.12-slim-bookworm`) and `yue2` (`nvidia/cuda:12.8.1-runtime-ubuntu24.04`, the default).
  - Requirements come from `uv export --frozen`, with uv 0.13.5 copied from `ghcr.io/astral-sh/uv`.
  - The wheel is downloaded with `hf download` at the pinned repo revision and checked with `sha256sum -c`.
  - Everything installs in one `uv pip install --no-sources` resolve. `--no-sources` is needed because the packages' `[tool.uv.sources]` point at the repo's uv workspace, which the image does not contain.
  - The container runs as root, which keeps writes to the RunPod volume simple.
  - The entrypoint is the PRD's `uvicorn studio_worker.main:app`, using the existing lazy `app`.
- **Smoke script.**
  - `scripts/smoke_worker.py` is async, so the tests can drive it in-process.
  - `--timeout` bounds the model warm-up and the job wait separately.
  - An optional `--min-audio-seconds` (default 10) lets it run against a fake worker. Defaults keep the PRD behaviour.
  - `scripts/` is on pytest's `pythonpath` so tests can import it.
- **Licence wording.** The pinned `LICENSE` and model card give plain CC BY-NC 4.0. They contain no extra permission for creators to monetise outputs, although PRD §11 and the M3 About note mention one. `NOTICE.md` states what the licence file says and flags the difference for the owner.

## M5 (2026-11-07)

Owner decisions:

- **The web app has a Cancel button.** PRD §9's result-card table has none; the owner asked for one. It shows for `queued` and `running` only, on the result card ("Cancel generation") and on in-progress library rows. After a successful request it reads "Canceling…" until the polled status turns `canceled`. A refused cancel shows the plain-language error with its Details toggle. **Suggested PRD change:** add "Queued/Running: Cancel button" to the §9 result-card table.
- **Deleting an in-progress generation still returns 409 `conflict`.** Cancel first, then delete. In the library, Delete stays disabled on in-progress rows, and Cancel sits beside it.

API details the PRD leaves open:

- **Cancel answers 200 with the existing object.** `POST /v1/jobs/{id}/cancel` returns the `Job`; `POST /api/generations/{id}/cancel` returns the `Generation`. Both are additive endpoints with no new fields.
- **A running job's cancel answer still says `running`.** Cancel is cooperative and takes effect at the next stage boundary (W-10), so clients keep polling until `canceled`. A second cancel is accepted (idempotent). A queued job's answer already says `canceled`.
- **Cancel is 409 in every other status, including `downloading`.** In `downloading`, the worker job has already succeeded; only the download is left.
- **How the server maps worker answers to a cancel.**
  - Worker 404 (the job is gone), or no worker job yet: the record is marked `canceled` locally.
  - Worker 409 (the job finished a moment ago): 409 `conflict`; the poller then mirrors the real outcome.
  - Worker unreachable: the error is relayed (502 `worker_unreachable`) and the status does not change.
- **Nothing overwrites a cancel that lands while the server waits for the worker.**
  - The poller re-reads the record before it writes, both after a worker answer and before it fails a generation, and leaves it alone if it has become terminal.
  - The cancel handler mirrors the worker job only while the record is still `queued` or `running`, so it never writes an older state over a newer one.
  - If a cancel lands while a submit is in flight (in `POST /api/generations` or a poller resubmit), the submit then cancels the job it created. If the record was also deleted meanwhile, the job is canceled as well, and the create request answers 404.
- **A worker 404 while polling fails the generation with `not_found`** ("The worker no longer has this job"), as in M2. This now also covers jobs the TTL sweep removed while the server was off. **Suggested PRD change:** list this in §6.

Worker:

- **The TTL sweep runs once at startup (after loading jobs) and then every 60 s** (`SWEEP_INTERVAL_S` in `main.py`), off the event loop. An expired job can stay visible for up to 60 s after its TTL. **Suggested PRD wording for W-11:** "within a minute of expiry".
- **Expiry counts from `finished_at`.** Jobs marked `worker_restarted` at startup get `finished_at` = the restart time, so they live a full TTL from then. Removing an expired job also frees its `client_request_id`.
- **Cancel inside a stage is still not wired** (see M4). The fixed `SongEngine` interface cannot pass yue2's `cancelled=` callback, so a long `semantic` stage on the real model runs to its end before the job stops. **Suggested PRD change** (owner approval needed): an optional cancel callable on the staged engine methods.
- **A cancel that arrives after the executor took the job off the queue, but before it marked it `running`,** wins: the executor re-checks the state under the same lock and skips the job without logging an error.

Server:

- **Retries are unchanged from M2**, now with tests for 504, the exact backoff schedule (0.5, 1, 2 s), download retries and cancel. `HttpWorkerBackend` takes an injectable `sleep` for those tests; the `MusicBackend` protocol is unchanged. `Retry-After` is still ignored, and a bare 503 without an envelope maps to `worker_unreachable`, not `not_ready`.

Docs:

- **`docs/architecture.md` and `docs/adding-features.md`** are new. Milestone plans are kept in `docs/plans/`.
