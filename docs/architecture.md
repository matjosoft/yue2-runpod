# Architecture

How Studio is put together, where the code lives, and how a generation moves through it. The specification is [PRD.md](PRD.md). Choices the PRD leaves open are in [decisions.md](decisions.md). To add an operation, a feature or a backend, see [adding-features.md](adding-features.md).

## The three parts

```
browser ──/api──▶ local server ──/v1 (bearer token)──▶ worker on RunPod (GPU)
 (web/)            (server/)                             (worker/)
```

| Part | Runs on | Responsibilities |
| --- | --- | --- |
| Web app (`web/`) | The owner's browser, served by the local server | Forms, the result card, the library, Settings and the status pill. It talks only to the local server. |
| Local server (`server/`) | The owner's machine | Settings (worker URL and token), the library database, downloaded files, the poller that follows worker jobs, and the only client of the worker API. |
| Worker (`worker/`) | A RunPod GPU pod (or locally with `ENGINE=fake`) | Loads the model once, queues jobs, runs one at a time, and serves the files it wrote. It keeps nothing long term: finished jobs are deleted after `JOB_TTL_HOURS`. |
| Contracts (`contracts/`) | Shared | Pydantic models for both APIs (`studio_contracts`), and the generated OpenAPI documents in `contracts/openapi/`. `web/src/api/schema.d.ts` is generated from `server.json`. |

The token never leaves the server: the browser never sees it, and no API response contains it.

## Worker: four layers

Dependencies point downward only. Only `engines/yue2.py` imports `yue2`. Nothing below the transport layer imports FastAPI (a test enforces this), so a serverless handler could reuse layers 2 to 4.

| Layer | Code | Knows about |
| --- | --- | --- |
| 1. Transport | `worker/studio_worker/api/`, `auth.py`, `errors.py`, `main.py` | HTTP routes, the bearer token, the error envelope, the app lifespan (loading jobs, the TTL sweep) |
| 2. Jobs | `worker/studio_worker/jobs/` (`store.py`, `queue.py`, `executor.py`) | Job folders and `job.json`, the FIFO queue, the single executor thread, cancel and expiry. Nothing about songs |
| 3. Operations | `worker/studio_worker/operations/` (`registry.py`, `base.py`, `song_generate.py`) | The registry, `OperationContext`, and `song.generate` |
| 4. Engines | `worker/studio_worker/engines/` (`base.py`, `fake.py`, `yue2.py`, `factory.py`) | The model. `FakeEngine` needs no GPU |

`/v1/capabilities` and job submission are built from the operation registry, so adding an operation changes no route.

## Server: core and features

| Code | What |
| --- | --- |
| `server/studio_server/core/backend/` | The `MusicBackend` protocol (`base.py`), the factory (`factory.py`) and `HttpWorkerBackend` (`http_worker.py`), which does retries and error mapping |
| `core/poller.py` | One asyncio task that advances every generation that is not finished |
| `core/generations.py` | The API view, seed filling, worker-state mirroring, submit and cancel. Shared by the poller and the `generations` feature |
| `core/worker.py` | Settings precedence, the backend for the current settings, the worker check and the capabilities cache |
| `core/db.py`, `core/storage.py` | SQLite (generic `generations` table) and files under `STUDIO_DATA_DIR` |
| `features/` | `settings`, `worker`, `generations`. Each exports a `Feature` (`features/base.py`) and may use `core`, never another feature |

## Web: registries

| Code | What |
| --- | --- |
| `web/src/features/index.ts` | The `FeatureModule` list (`create`, `library`, `settings`). The shell builds routes and navigation from it and hides a module whose `requires` the worker does not offer |
| `web/src/components/operationViews.ts` | Operation name → `Summary` and `Result` components. With the worker registry, it is the only place that branches on an operation name |
| `web/src/api/` | The generated types, the `openapi-fetch` client and the TanStack Query hooks |
| `web/src/components/errorMessages.ts` | The plain-language message for every error code |
| `web/src/components/CancelButton.tsx` | Cancel for queued and running generations, shared by the result card and the library |

## Generate flow

1. The browser posts `POST /api/generations` with `{title?, operation, request}`.
2. The server checks the operation against the cached capabilities, fills in a seed if the operation's schema has an integer `seed`, stores the record as `queued` with a new `client_request_id`, and submits `POST /v1/jobs`. If the worker refuses or cannot be reached, the record is deleted and the error is returned, so the form keeps its input.
3. The worker validates the params, queues the job, and returns it (202). Posting the same `client_request_id` again returns the same job.
4. The executor thread runs the operation. `song.generate` calls the engine stage by stage (`plan`, `semantic`, `synthesis`, `decode`, `save`), reporting each stage, and lists the files it wrote in the manifest with sizes and sha256.
5. The poller (every `STUDIO_POLL_INTERVAL_S`) reads the worker job and mirrors its state, stage, timings and error into the record. The browser polls the server every 1.5 s.
6. When the worker job succeeds, the record becomes `downloading`. The poller fetches the artifacts (at most 2 at once) into `.partial/`, checks size and sha256, renames them into place, and sets `succeeded`.

## Cancel flow (W-10, S-9)

1. The browser posts `POST /api/generations/{id}/cancel`. Only `queued` and `running` generations can be canceled; any other status (including `downloading`) is 409 `conflict`.
2. The server calls `POST /v1/jobs/{job_id}/cancel`, then reads the job once and mirrors it.
   - A record with no worker job yet, or whose job the worker no longer has (404), is marked `canceled` locally.
   - A worker 409 (the job has just finished) is returned as 409; the poller then mirrors the real outcome.
   - If the worker cannot be reached, the error is returned and the status does not change.
3. On the worker, a queued job is taken off the queue and marked `canceled` at once. A running job gets its cancel flag set; the operation checks `ctx.cancel_requested()` before each stage and stops there. The job ends `canceled`, and its `artifacts/` folder is emptied.
4. So the answer for a queued job says `canceled`, and for a running job still says `running`. The UI shows "Canceling…" and keeps polling until the status turns `canceled`.
5. Delete still refuses a generation in progress (409): cancel first, then delete.

A cancel cannot interrupt a stage that is already running: with the real model, one stage can take a minute or more (docs/decisions.md, M4 and M5).

## TTL cleanup (W-11)

The worker deletes finished jobs (`succeeded`, `failed`, `canceled`) once `finished_at` is `JOB_TTL_HOURS` (default 24) in the past. It sweeps once at startup and then every 60 s, so an expired job can stay visible for up to a minute. Queued and running jobs are never deleted. An expired job's id returns 404, and its `client_request_id` can be used again.

If the server asks about a job the worker no longer has, the generation fails with `not_found` ("The worker no longer has this job"). Deleting such a generation still works, because the remote delete is best effort.

## Retries and backoff

`HttpWorkerBackend` retries network errors and HTTP 429, 502, 503 and 504, at most 3 times, waiting 0.5 s, 1 s and 2 s (`ServerSettings.retry_base_s`, doubled each time; not an environment variable). This applies to every call, including artifact downloads and cancel. No other 4xx is retried. `Retry-After` is ignored. A proxy's non-JSON 502, 503 or 504 becomes `worker_unreachable`.

The poller adds its own patience on top: while the worker is unreachable (or a few other errors that can clear up), a generation waits up to `STUDIO_UNREACHABLE_GRACE_S` before it fails with `worker_unreachable`.

## State machines

Worker job (`jobs/store.py`, `ALLOWED`):

```
queued ──▶ running ──▶ succeeded
  │           ├──────▶ failed
  │           └──────▶ canceled
  ├──────────────────▶ canceled   (cancel while queued)
  └──────────────────▶ failed     (operation no longer registered; worker restart)
```

A job found `queued` or `running` at startup becomes `failed` with `worker_restarted`. Terminal states never change again.

Generation (server):

```
queued ──▶ running ──▶ downloading ──▶ succeeded
  │           │             └────────▶ failed   (download_failed, worker_unreachable)
  │           ├──────────────────────▶ failed   (the worker's error, or not_found)
  │           └──────────────────────▶ canceled
  └──────────────────────────────────▶ canceled / failed
```

The generation follows the worker job, except that worker `succeeded` becomes `downloading` until the files are local. The poller re-reads the record before every write (after a worker answer, and before failing a generation), so a cancel or delete that lands while it waits for the worker is never overwritten. A submit that finds its record canceled or deleted when it returns cancels the job it created.

## Acceptance tests

| AT | Where |
| --- | --- |
| AT-1 Happy path | `server/tests/test_acceptance.py::test_at1_happy_path`, `web/e2e/app.spec.ts` |
| AT-2 Validation | `web/tests/create.test.tsx`, `server/tests/test_generations.py`, `web/e2e/app.spec.ts` |
| AT-3 Worker unreachable | `web/e2e/app.spec.ts`, `web/tests/create.test.tsx`, `server/tests/test_generations.py` |
| AT-4 Wrong token | `server/tests/test_acceptance.py::test_at4_wrong_token`, `web/tests/settings.test.tsx` |
| AT-5 Server restart mid-job | `server/tests/test_acceptance.py::test_at5_server_restart_mid_job` |
| AT-6 Queue full | `worker/tests/test_api_jobs.py::test_at6_queue_full`, `server/tests/test_generations.py` |
| AT-7 Cancel | `worker/tests/test_api_jobs.py::test_at7_cancel_queued_and_running_jobs`, `server/tests/test_acceptance.py::test_at7_cancel` |
| AT-8 Idempotency | `worker/tests/test_api_jobs.py::test_at8_same_client_request_id_creates_one_job` |
| AT-9 Persistence | `server/tests/test_acceptance.py::test_at9_persistence_and_delete` |
| AT-10 Extensibility | `worker/tests/test_api_capabilities.py::test_a_new_operation_needs_no_core_change`, `server/tests/test_acceptance.py::test_at10_new_operation_end_to_end` |
| AT-11 Worker restart mid-job | `worker/tests/test_api_jobs.py::test_at11_worker_restart_fails_interrupted_jobs`, `server/tests/test_acceptance.py::test_at11_worker_restart_then_retry`, `web/tests/create.test.tsx` (Retry) |
| AT-12 Warm-up | `worker/tests/test_api_status.py`, `server/tests/test_worker_check.py`, `web/tests/settings.test.tsx` |
| AT-13, AT-14 | Owner, on a GPU (M6): `scripts/smoke_worker.py` and [runpod.md](runpod.md) |

Run them all with `make test`, or one part at a time:

```bash
uv run --locked pytest worker/tests -q
uv run --locked pytest server/tests -q
pnpm -C web test
```
