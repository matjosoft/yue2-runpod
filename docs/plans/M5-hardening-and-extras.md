# M5 plan: hardening and extras

## Context

M5 (PRD §13): Cancel (W-10, S-9), TTL cleanup (W-11), retry logic, the error-message table, `docs/adding-features.md`, `docs/architecture.md`. Exit: AT-7 and AT-10 pass; every agent-verified acceptance test (AT-1 to AT-12) passes.

What the code looks like now (from the survey):
- **Cancel is missing everywhere.**
  - Worker: there is no cancel route. The executor never passes a `cancel_event` into `OperationContext`, so `ctx.cancel_requested()` is always false.
  - Already in place: `JobQueue.remove()` (`worker/studio_worker/jobs/queue.py:38`), the store transitions (`jobs/store.py:27-33`), `OperationCanceled` → `canceled`, and output discarded through `store.clear_artifacts` (`jobs/executor.py:220-239`). `song.generate` checks for cancel before every stage (`operations/song_generate.py:45-49`).
  - Server: no route. `HttpWorkerBackend.cancel` exists (`server/studio_server/core/backend/http_worker.py:158`) but nothing calls it.
  - Web: no Cancel control.
- **TTL is missing.** `job_ttl_hours` is configured (`worker/studio_worker/config.py:30`), but nothing deletes expired jobs.
- **Retry is mostly done (M2).** It covers 429/502/503/504 and transport errors, up to 3 retries, with backoff of 0.5, 1 and 2 s. Gaps:
  - The sleep can't be injected.
  - There are no tests for 504, for the backoff schedule, or for download-level retries.
- **The error-message table is done.** `web/src/components/errorMessages.ts` has all 11 PRD strings, and `web/tests/errors.test.tsx` checks every code.
- **AT-10 is only half done.** There is a worker-level `test.echo` test (`worker/tests/test_api_capabilities.py:68`), but no server-level one.
- `docs/architecture.md` and `docs/adding-features.md` don't exist.

Your decisions: **add a Cancel button in the web app** (the gap in PRD §9 is recorded in decisions.md). **Deleting an in-progress generation keeps returning 409**: cancel first, then delete.

## 1. Steps

The test is written first in each step and run red, then the implementation follows. Everything uses `FakeEngine` with `stage_gate` or `load_gate` (no sleeps, no GPU). Commands:
- worker: `uv run --locked pytest worker/tests/<file> -q`
- server: `uv run --locked pytest server/tests/<file> -q`
- web: `pnpm -C web test`

### Worker

**Step 1. Executor cancel (W-10 core).**
- Tests in `worker/tests/test_executor.py`:
  - (a) A queued job behind a gated job cancels at once: state `canceled`, `finished_at` set, removed from the queue, and the engine never runs it.
  - (b) A running job (stage gate held in `plan`), once cancel is called and the gate released, ends `canceled`. Only `plan` ran, `artifacts == []`, and the `artifacts/` folder is empty.
  - (c) A terminal job raises `JobConflictError`.
  - (d) A second cancel on a running job is accepted (idempotent).
  - (e) A job canceled between pickup and the switch to `running` is skipped without logging an "executor error".
- Implement `JobExecutor.cancel(job_id) -> Job`:
  - queued → `queue.remove`, then `store.update(canceled, finished_at=utcnow())`
  - running → set a per-job `threading.Event`, now passed into `OperationContext(cancel_event=...)`
  - terminal → conflict
  - In `_run`, do the queued → running check under the executor lock and treat a concurrent cancel as a no-op.

**Step 2. Worker route `POST /v1/jobs/{id}/cancel`.**
- Tests in `worker/tests/test_api_jobs.py`:
  - 200 with the job object. The state is `canceled` for a queued job and still `running` for a running one.
  - 404 `not_found`, 409 `conflict` on a terminal job, 401 without a token, and the error envelope on each.
  - **AT-7 (worker):** queued cancels at once. Running ends `canceled` at the next stage boundary with an empty manifest, and the artifact download returns 404.
- Implement it in `worker/studio_worker/api/jobs.py`, following the `delete_job` pattern (`:76-85`).
- Run `make contracts`. The contract tests must pass.

**Step 3. TTL cleanup (W-11).**
- Tests:
  - `worker/tests/test_store.py`: `JobStore.sweep_expired(now, ttl)` removes terminal jobs whose `finished_at <= now - ttl`. It removes the folder and the idempotency entry, so the same `client_request_id` then creates a new job. It keeps fresh jobs, and it never removes `queued` or `running` jobs, even with an old `created_at`.
  - API test (`test_api_jobs.py`): a finished job, after a sweep at "now + 25 h", gives 404 for `GET /v1/jobs/{id}` and for its artifact. A running gated job survives the same sweep.
  - Startup test: restarting the worker with a tiny `job_ttl_hours` removes an old finished job at startup.
- Implement:
  - `sweep_expired` in `jobs/store.py`.
  - In the `main.py` lifespan: one sweep right after `store.load()`, then an asyncio task that runs the sweep every 60 s through `asyncio.to_thread`, cancelled on shutdown.
  - Expose a `job_ttl_hours` parameter in the `start_worker` fixture (`worker/tests/conftest.py`).

### Server

**Step 4. Server cancel (S-9).**
- Tests in a new `server/tests/test_cancel.py`, using the in-process fake worker with gates. Extend `server_support.py` only if a gate isn't reachable from there.

  | Generation state | Result |
  | --- | --- |
  | queued | 200, status `canceled` in the response (the server re-reads the job after cancel) |
  | running | 200, still `running`; the poller then mirrors `canceled`, and nothing is downloaded or left under the data dir |
  | downloading, succeeded, failed, canceled | 409 `conflict` |
  | unknown id | 404 |
  | worker unreachable | relayed `worker_unreachable`; status unchanged |
  | worker answers 409 | 409 `conflict` |
  | worker answers 404 (job gone) | marked `canceled` locally |
  | no `worker_job_id` yet | marked `canceled` locally |

- Implement `POST /api/generations/{id}/cancel` in `server/studio_server/features/generations/__init__.py`:
  - Call `backend.cancel(ref)`, then try `backend.get_job(ref)` once and apply it with the existing `mirror_changes` (`core/generations.py`). If that read fails, ignore it; the poller catches up.
  - Shared logic goes in `core/` if it's needed there.
  - Delete stays 409 for in-progress generations; update the docstring there.
- **AT-7 (server):** add it to `server/tests/test_acceptance.py`.
- Run `make contracts`.

**Step 5. The worker forgets a job (TTL/expiry follow-up).**
- Tests:
  - (a) While a generation is queued or running, the worker answers 404 for its job. The poller then fails the generation with code `not_found` and a message saying the worker no longer has the job, instead of polling forever.
  - (b) `DELETE /api/generations/{id}` succeeds when the remote job is already gone, because the remote delete is best effort.
- Only implement what turns out red.

**Step 6. Retry hardening.**
- Tests in `server/tests/test_backend.py`:
  - 504 is retried.
  - The backoff schedule is exactly `[0.5, 1.0, 2.0]`, recorded through an injected sleep.
  - Download retries: 502 then success; a transport error then success; four failures → `worker_unreachable`; a 404 is not retried.
  - Cancel is retried on 503 and not retried on 409.
- Implement: add a constructor keyword `sleep: Callable[[float], Awaitable[None]] = asyncio.sleep` to `HttpWorkerBackend` and use it in `_backoff`. The `MusicBackend` protocol does not change. Fix anything else that turns out red.

### Web

**Step 7. Cancel button.**
- Vitest tests (`web/tests/create.test.tsx`, `library.test.tsx`, plus `fakeApi.ts`):
  - The button shows only for `queued` and `running`, not for `downloading` or terminal states.
  - A click posts to `/api/generations/{id}/cancel` and disables the button while pending.
  - A 409 shows the plain `conflict` message.
  - Library rows that are in progress get Cancel, and Delete stays disabled.
- Implement: a `useCancelGeneration` mutation in `web/src/api/queries.ts`, a Cancel button in `ResultCard.tsx` for the queued and running states (labelled "Cancel generation"), the same on in-progress `LibraryPage.tsx` rows, and an update to the stale M5 comment. No operation-name branching.

**Step 8. Error-message table audit.**
- Run `web/tests/errors.test.tsx`. It must still cover every code in the regenerated `server.json`.
- Add a test that an error returned by cancel renders through `ErrorMessage` with its Details toggle.
- No new codes are expected. If one appears, give it a message.

### Extensibility and acceptance

**Step 9. AT-10 end to end through the server.**
- Test in `server/tests/test_acceptance.py`:
  - Register a test-only `test.echo` operation (stages `echo`, `save`; writes `echo.txt`) in the in-process worker with `registered(...)`.
  - `/api/capabilities` lists it.
  - `POST /api/generations` with `operation: "test.echo"` reaches `succeeded`, and the stages mirror correctly.
  - `/api/generations/{id}/artifacts/echo.txt` returns the text.
  - After unregistering, it is gone from capabilities.
- No core code may change for this to pass. If it fails because of song-specific logic in core (for example seed filling), stop and report it to you as a finding rather than quietly special-casing it.

**Step 10. Acceptance audit, AT-1 to AT-12.**
- Map each AT to its test, by grepping for `AT-` names.
- Fill any gap with a test, for example AT-11 "Retry creates a new one" at the server or UI level if it is missing.
- Tag the AT-7 and AT-10 tests with their IDs in their docstrings.

### Docs

**Step 11.**
- `docs/architecture.md`:
  - parts and responsibilities, the worker's 4 layers, the server core and features, the web registries
  - the generate flow, the cancel flow, TTL, and retry/backoff
  - the job and generation state machines
  - where code lives
  - an AT → test map
- `docs/adding-features.md`:
  - adding a worker operation (with the `test.echo` example)
  - contracts first, then `make contracts`
  - a server feature module, a web `FeatureModule` and an `operationViews` entry
  - a new `MusicBackend`
  - the §7 rules
- `README.md`: link both docs and set the status to M5.
- `docs/decisions.md` gets an `## M5` section covering:
  - the cancel response shapes
  - running cancel returning `running`
  - downloading → 409
  - Delete staying 409
  - the 60 s sweep and its up-to-60 s lag
  - the Cancel button added beyond §9
  - worker 404 → `not_found`
  - bare 503 → `worker_unreachable`
  - mid-stage cancel still not wired
- Remove the stale "M5" comments (`api/jobs.py:1`, `features/generations/__init__.py:3,166`, `LibraryPage.tsx:15`, `test_api_capabilities.py:69`).
- Run every command written in the docs.
- Run `worker/tests/test_docs.py` to see what it checks.

## 2. Contracts (fixed; from PRD §6 and §7; M5 adds no new models)

PRD §7, used unchanged:

```python
class Operation(Protocol):
    name: ClassVar[str]                        # "song.generate"
    params_model: ClassVar[type[BaseModel]]    # validated before the job is queued
    def stages(self, engine: Engine) -> Sequence[str]: ...
    def run(self, ctx: OperationContext, params: BaseModel) -> None: ...

class OperationContext:
    engine: Engine
    job_dir: Path
    def stage(self, name: str, progress: float | None = None) -> None: ...
    def add_artifact(self, relpath: str, role: str, media_type: str) -> None: ...
    def resolved(self, **values) -> None: ...   # e.g. the seed actually used
    def cancel_requested(self) -> bool: ...

class Engine(Protocol):
    kind: str
    def load(self) -> None: ...
    def info(self) -> EngineInfo: ...
    def close(self) -> None: ...

class SongEngine(Engine, Protocol):            # what song.generate needs
    supports_staged: bool
    def plan(self, p: SongParams) -> Any: ...
    def generate_semantic(self, plan: Any) -> Any: ...
    def synthesize(self, semantic: Any) -> Any: ...
    def decode(self, latents: Any) -> Any: ...
    def write_artifacts(self, out: Path, **parts) -> list[ArtifactSpec]: ...
    def generate(self, p: SongParams, out: Path) -> list[ArtifactSpec]: ...  # single call

class MusicBackend(Protocol):
    async def status(self) -> WorkerStatus: ...
    async def capabilities(self) -> Capabilities: ...
    async def submit(self, operation: str, params: dict, client_request_id: str) -> JobRef: ...
    async def get_job(self, ref: JobRef) -> JobView: ...
    async def cancel(self, ref: JobRef) -> None: ...
    async def delete_job(self, ref: JobRef) -> None: ...
    async def download_artifact(self, ref: JobRef, name: str, dest: Path) -> None: ...
```

```ts
export interface FeatureModule {
  id: string;
  requires?: string[];                          // operation names from /api/capabilities
  routes: RouteObject[];
  nav?: { label: string; to: string; order: number };
}
```

Endpoints, PRD §6:
- `POST /v1/jobs/{id}/cancel`: bearer. "Cancel (see W-10); 409 if already terminal"
- `DELETE /v1/jobs/{id}`: "Delete files of a terminal job; 409 if not terminal"
- `POST /api/generations/{id}/cancel`: "Cancel"

The PRD gives no response body for either cancel. I will return **200 with the existing `Job` model** (worker) and **200 with the existing `Generation` model** (server). Both are additive endpoints with no new fields, and the choice is recorded in decisions.md.

Rules that still apply:
- Error envelope: `{"error": {"code", "message", "retryable", "details"}}`.
- Codes: `conflict` 409, `not_found` 404, `unauthorized` 401, `queue_full` 429, `not_ready` 503, `internal` 500. Job-level: `oom`, `engine_error`, `worker_restarted`. Server-side: `worker_unreachable`, `incompatible_worker`, `download_failed`, `not_configured`.
- Job `state` ∈ {queued, running, succeeded, failed, canceled}; generation `status` adds `downloading`.
- Retries: network errors, 502, 503, 504 and 429, with exponential backoff, at most 3 times. Never retry any other 4xx.
- W-10: "a queued job cancels at once; a running job is cancelled between stages", "ends as `canceled` and discards its output".
- W-11: "Delete finished job files after `JOB_TTL_HOURS` (default 24)", "An expired job returns 404; running jobs are never deleted".
- S-9: "Maps to worker cancel; status ends `canceled`".

## 3. Execution rules (after approval)

- After each step, run that step's tests and fix failures before moving on. After a contract-touching step (2, 4), run `make contracts` and the contract tests.
- At the end, run `make lint`, `make typecheck` and `make test`, and show the output: failures plus the final pass/fail summary per target.
- Then start **one** review subagent on `git diff` against the M5 entry in PRD §13, W-10, W-11, S-9, AT-7, AT-10 and the rest of AT-1 to AT-12. It reports **only correctness problems or missing requirements**: no style, no requests for extra abstractions. I fix real findings and rerun the affected tests plus `make test`.
- Commit with a descriptive message (`M5: cancel, TTL cleanup, retry hardening, docs`), using the Mattias Kastensson identity from memory and the Co-Authored-By line.
- Do not start M6.

## 3b. Context rules (after approval)

- Anything beyond the PRD sections listed (a library, an unfamiliar module, a long file) goes to an Explore subagent, and only a summary comes back. For edits, read only the line ranges I need, using the survey's references.
- Don't paste full test or install logs; show only failures and the pass/fail summary.
- I do all core implementation in this session; no splitting contracts and dependent code across subagents.
- Subagents only for (a) research that would read many files and (b) the final review.

## 4. Final report (at the end)

- What's done, step by step, with the test files added.
- The make output summary.
- The review findings and how each was handled.
- **What I couldn't verify:**
  - real YuE2 cancellation timing (a real stage can run a minute or more before the boundary check)
  - the sweep on a real RunPod volume
  - real network retry behaviour through the RunPod proxy
  - anything GPU-related (owner, M6)
- Suggested PRD changes, listed below.

## 5. Risks and unclear points in the PRD

- **Cancel response shape and status are unspecified.** I chose 200 plus the object. For a running job the immediate answer still says `running`, so the UI keeps polling.
- **Cancel only takes effect between stages.** The fixed `SongEngine` interface can't pass yue2's `cancelled=` callback, so a long `semantic` stage runs to its end before stopping. **Suggested PRD change** (only with your approval): an optional cancel callable on the staged methods.
- **Cancelling during `downloading` returns 409**, because the worker job has already succeeded. The PRD is silent on this.
- **An expired job can stay visible for up to 60 s** after its TTL, because of the sweep interval. **Suggested PRD wording for W-11:** "within a minute of expiry".
- **Jobs marked `worker_restarted` at startup get `finished_at` = restart time**, so they live a full TTL from then.
- **AT-10 at the server level may expose song-specific logic in core** (for example seed filling in S-1). If it does, I'll report it, not patch around it.
- **"Retry logic" in M5 overlaps with what M2 already built.** M5 adds tests and an injectable sleep. `Retry-After` stays ignored, and a bare 503 without an envelope maps to `worker_unreachable`, not `not_ready`.
- **PRD §9 has no Cancel control.** Per your choice I'm adding one. **Suggested PRD change:** add "Queued/Running: Cancel button" to the result-card table.
- **The poller has no defined behaviour when the worker loses a job (404)**, for example after a TTL expiry while the server was off. Step 5 defines it as a failure with `not_found`. **Suggested PRD change:** list it in §6.
