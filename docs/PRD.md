# PRD: AI music platform MVP (YuE2 backend on RunPod)

Nov 7, 2026 · @Mattias Kastensson

## 1. Overview and goals

Studio (working name) turns a style description and lyrics into a finished song. YuE2-3B generates it on a rented RunPod GPU, and the owner controls it from a web app on their own machine.

This PRD is written for an AI coding agent. Read it in order; section 13 holds the working rules, and section 14 lists what is still unknown.

**Why this shape.** YuE2 needs an NVIDIA GPU with BF16 and 24 GB VRAM, so inference runs remotely. The UI, library, files and settings stay local. The MVP's real job is to prove that path end to end and to fix an architecture that later studio features can plug into without rewrites.

**Goals**

- G1 End to end: style + lyrics in, a playable and downloadable 48 kHz stereo FLAC out.
- G2 Remote GPU, local control: the model runs on a RunPod pod; UI, library and files live on the owner's machine.
- G3 Extensible by construction: new operations, backends and UI features are additions, not rewrites (section 7).
- G4 Reproducible: every generation stores its full request, including the seed, and every file the model emits.
- G5 Operable by one person: a runbook, a visible health status, and no SSH needed.

**MVP is done when**

- [ ] On a RunPod pod with a 24 GB GPU running the published worker image, `scripts/smoke_worker.py` generates a song and validates the output.
- [ ] From a clean clone, one documented command starts the local server and UI. The owner enters the worker URL and token in Settings, submits style + lyrics, sees stage progress, plays the song, and downloads the FLAC.
- [ ] The generation is still in the library, and still playable, after a browser reload and after a server restart.
- [ ] The whole stack runs without a GPU in fake mode, and `make test` passes.
- [ ] `README.md`, `docs/architecture.md`, `docs/runpod.md`, `docs/adding-features.md` and `docs/yue2-api-notes.md` exist and are accurate.

## 2. Scope

The MVP builds exactly one capability, `song.generate`, and the plumbing around it. Everything else is designed for but not built.

**In scope**

| Area | MVP deliverable |
| --- | --- |
| Worker | Docker image for RunPod that runs YuE2-3B behind an HTTP job API with bearer auth, plus a fake engine for GPU-free runs |
| Local server | Python service: settings, job submission and polling, artifact download, SQLite library |
| Web UI | Create page, library, settings, worker status |
| Operation | `song.generate` from `style` + `lyrics`, with optional `seed`, planning mode (`cot`), `cfg_scale` and, added after M5, a supplied ABC score (`abc`) |
| Score (added after M5, owner request) | The result shows the generated ABC score. "Edit score" loads it into the Create form, where it can be edited and sent as `abc` for a new song. A new song can also start from a pasted score, full (melody and chords, `cot=full`) or melody only (`cot=melody`) |
| Quality | Automated tests, a GPU smoke script, a RunPod runbook |

**Out of scope, and where each piece plugs in later**

| Later feature | Extension point |
| --- | --- |
| Score-only planning (a score without audio), then render; score lineage (`parent_id`) | New worker operations over the staged API; a score feature module (server + web). Editing a score and generating from it shipped after M5 as the optional `abc` param |
| Exact replay from stored tokens | Stored request and artifacts (G4); a new operation that takes artifacts as input |
| Covers (transcribe, then re-sing) | New operation plus an auxiliary model in the worker |
| Stems, mastering, VST chains, piano roll, karaoke timing | Local feature modules, or worker operations when GPU-bound |
| LoRA use and training | New operations plus a model registry in the worker |
| Other backends: RunPod serverless, local yue2.cpp, ComfyUI | A new `MusicBackend` implementation |
| Automatic pod start/stop, idle shutdown | A new server feature module plus a RunPod API client |
| MCP or agent control surface | A new server feature module exposing the same services |
| Instrumental-only, audio prompts, best-of-N, batch | New params or operations; no schema break |

**Non-goals:** multi-user accounts, public hosting, mobile, commercial use (see the model licence in section 11), and streaming audio while it generates.

## 3. Users and scenarios

The only user is the owner: a technically capable solo creator with their own RunPod account and a local machine that cannot run YuE2. No accounts or roles are needed.

| # | Scenario | Expected outcome |
| --- | --- | --- |
| S1 | First-time setup: start the pod from the runbook, paste its URL and token into Settings | The status pill turns green (ready) |
| S2 | Make a song: enter style and lyrics, press Create | Stage progress shows; the song plays and downloads when done |
| S3 | Iterate: reuse a past generation's settings, change the seed or lyrics | A new entry appears beside the old one in the library |
| S4 | The pod is stopped, or still loading the model | The UI says which, keeps the typed input, and explains why Create cannot run yet |
| S5 | A generation fails, for example out of GPU memory | A plain-language error with a retry button; nothing is lost |
| S6 | The browser is closed or the local server restarts mid-generation | The job keeps running remotely and shows as finished on return |

## 4. Functional requirements

Every P0 requirement must be met for the MVP; P1 should be met; P2 is optional. Each row carries its own acceptance check.

### Worker (runs on RunPod)

| ID | Requirement | Pri | Acceptance |
| --- | --- | --- | --- |
| W-1 | Load the YuE2 pipeline once at startup (`m-a-p/YuE2-3B`, device `cuda`, default VAE) and report readiness | P0 | `/v1/status` shows `model.state` = `loading`, then `ready`; no reload between jobs |
| W-2 | `POST /v1/jobs` accepts `{operation, params, client_request_id}` and returns 202 with a job | P0 | Unknown operation gives 422; same `client_request_id` returns the same job, never a second run |
| W-3 | Run one GPU job at a time from a FIFO queue capped at `MAX_QUEUE` (default 4) | P0 | A fifth queued job gets 429 `queue_full`; two jobs never overlap on the GPU |
| W-4 | Job status exposes state, stage, timestamps, timings, resolved params and error | P0 | State moves `queued`, `running`, then `succeeded`, `failed` or `canceled`; stage names match the operation's declared stages |
| W-5 | Publish an artifact manifest and download endpoint; keep everything `save_artifacts` writes | P0 | Manifest lists name, role, media type, size, sha256; downloads match the hash |
| W-6 | Bearer-token auth on everything except `/healthz` | P0 | Missing or wrong token gives 401; comparison is constant-time |
| W-7 | `GET /v1/capabilities` lists operations with JSON Schemas, plus engine and model identities | P0 | Registering a new operation makes it appear with no other code change |
| W-8 | Structured errors with stable codes (section 6) | P0 | Every failure path returns the documented envelope |
| W-9 | Fake engine (`ENGINE=fake`) that emits a short valid 48 kHz stereo FLAC and walks through all stages | P0 | Full test suite runs on a machine with no GPU |
| W-10 | Cancel: a queued job cancels at once; a running job is cancelled between stages | P1 | Cancelling a running job ends it as `canceled` and discards its output |
| W-11 | Delete finished job files after `JOB_TTL_HOURS` (default 24), counted from when the job finished | P1 | An expired job returns 404 within a minute of expiry; queued and running jobs are never deleted (changed after M5) |
| W-12 | Report `idle_seconds` in status | P1 | Value resets when a job starts |
| W-13 | Optional idle auto-stop of the pod | P2 | Design only in MVP; see section 8 |

### Local server (owner's machine)

| ID | Requirement | Pri | Acceptance |
| --- | --- | --- | --- |
| S-1 | `POST /api/generations` validates, assigns a seed if blank, stores a record, submits to the backend | P0 | Response returns the record with status `queued`; the seed is always filled in |
| S-2 | A background poller advances every non-terminal generation, and resumes after a server restart | P0 | Kill and restart the server mid-job: the generation still finishes and downloads |
| S-3 | On success, download all artifacts, verify size and sha256, store them under the data dir | P0 | Corrupted download is retried, then fails with `download_failed`; no partial file is visible |
| S-4 | Library: list, get, delete (delete removes local files and, best effort, the remote job) | P0 | Deleted generation's files are gone from disk |
| S-5 | Settings: worker URL and token; the token is write-only. `POST /api/worker/check` tests the connection | P0 | No endpoint ever returns the token; check reports `not_configured`, `unreachable`, `unauthorized`, `warming_up` or `ready` |
| S-6 | Cache and expose worker capabilities at `/api/capabilities` | P1 | UI hides features whose operation the worker lacks |
| S-7 | Serve audio and artifacts with HTTP Range support | P0 | Seeking in the player works |
| S-8 | SQLite with Alembic migrations from day one | P0 | `alembic upgrade head` on an empty data dir creates the schema |
| S-9 | Cancel a generation | P1 | Maps to worker cancel; status ends `canceled` |

### Web UI

| ID | Requirement | Pri | Acceptance |
| --- | --- | --- | --- |
| U-1 | Create form: title, style, lyrics, and an Advanced section (seed, planning mode, `cfg_scale`) | P0 | Empty style or lyrics blocks submit with an inline message |
| U-2 | Live progress: stage list with the current stage marked, elapsed timer, polling every 1.5 s | P0 | Progress survives a page reload |
| U-3 | Result: audio player, "Download WAV" (24-bit PCM, converted in the browser from the stored FLAC; changed after M5) and a download for each artifact, the FLAC included | P0 | Plays the stored file; downloads a 48 kHz stereo WAV and each stored file |
| U-4 | Library: newest first, play, delete with confirmation, Reuse settings | P0 (Reuse P1) | Reuse fills the form with the stored request, seed included |
| U-5 | Settings dialog and a worker status pill (not configured, unreachable, warming up, ready) | P0 | Pill updates within 10 s of a state change |
| U-6 | Errors shown in plain language with a details toggle | P0 | Each error code in section 6 has a message |
| U-7 | Example button that fills a sample style and lyrics | P1 | One click gives a valid, submittable form |
| U-8 | Keyboard-accessible controls; light and dark theme follow the OS | P2 | Tab order is logical; no contrast failures |

### Developer experience

| ID | Requirement | Pri | Acceptance |
| --- | --- | --- | --- |
| D-1 | `Makefile` targets: `setup`, `dev`, `run-fake`, `test`, `lint`, `contracts`, `build-worker` | P0 | Each runs on a clean clone following the README |
| D-2 | `scripts/smoke_worker.py --url --token` runs one real generation and validates it | P0 | Exits 0 only when a 48 kHz stereo FLAC and `score.abc` come back |
| D-3 | Docs listed in section 1 | P0 | Present, accurate, and each command in them has been run |

## 5. Architecture

The UI talks only to the local server, the server reaches the GPU through one adapter, and the worker runs jobs against YuE2.

&#91;embedded content: architecture · local app, one adapter, RunPod worker\]

The highlighted link is the only connection between the two machines: short HTTPS calls with a bearer token, polled every 2 s. Everything left of it can be tested without a GPU by pointing it at a fake-engine worker.

### Responsibilities

| Part | Owns | Must never |
| --- | --- | --- |
| Web UI | Forms, rendering, polling the local server | Talk to the worker, or hold the token |
| Local server | Settings, library database, artifact storage, poller | Run models |
| Backend adapter | Translating local calls into worker HTTP calls | Contain feature logic |
| Worker | Job queue, operation registry, engine, artifact manifest | Know about titles, libraries or the UI |
| Network volume | Hugging Face weight cache, job files | Hold secrets |

### Flow: generate a song

1. The UI posts the form to `POST /api/generations`.
2. The server validates it, fills in a seed if blank, stores the record as `queued`, and submits to the worker with a fresh `client_request_id`.
3. The worker validates the params, queues the job and returns 202.
4. The executor runs `song.generate` and reports each stage.
5. The server polls the job every 2 s and mirrors its state and stage into the record.
6. When the worker reports `succeeded`, the server sets `downloading`, fetches each artifact, verifies size and sha256, then sets `succeeded`.
7. The UI, polling every 1.5 s, shows the player.

The server's `queued` and `running` mirror the worker's states. `downloading` exists only on the server.

## 6. API contract

The worker exposes a generic job API in which `song.generate` is just the first operation; the local server exposes a library-oriented API to the UI. Pydantic models in `contracts/` are the single source of truth: the OpenAPI files and the TypeScript types are generated from them and never hand-edited.

### Worker API, version 1.1 (all JSON, all under `/v1` except `/healthz`)

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `GET /healthz` | none | Process is alive. Returns `{"ok": true}` only, nothing else |
| `GET /v1/status` | bearer | Model state, GPU, queue, uptime, idle seconds |
| `GET /v1/capabilities` | bearer | API and worker version, engine identity, operations with stages and JSON Schemas |
| `POST /v1/jobs` | bearer | Submit a job; 202 with the job object |
| `GET /v1/jobs` | bearer | List recent jobs (newest first, max 50); used to recover after a server restart |
| `GET /v1/jobs/{id}` | bearer | Job object |
| `POST /v1/jobs/{id}/cancel` | bearer | Cancel (see W-10); 409 if already terminal |
| `DELETE /v1/jobs/{id}` | bearer | Delete files of a terminal job; 409 if not terminal |
| `GET /v1/jobs/{id}/artifacts/{name:path}` | bearer | Download one artifact, with Range support |

Submit request:

```json
{
  "client_request_id": "0b8e5f0c-6a3e-4d62-9d0e-2f6f3d1f7a11",
  "operation": "song.generate",
  "params": {
    "style": "English, jazz-funk, warm lead vocal, Rhodes, bass and drums",
    "lyrics": "[Verse]\n...\n[Chorus]\n...",
    "seed": 12345,
    "cot": "full",
    "cfg_scale": 1.2
  }
}
```

`song.generate` params. The limits are app-side guardrails, configurable, not model facts. The schema uses `extra="forbid"` so typos fail loudly; new optional fields later are additive.

| Field | Type | Rule |
| --- | --- | --- |
| `style` | string | Required, 1 to 1000 characters |
| `lyrics` | string | Required, 1 to 8000 characters, with section tags such as `[Verse]` and `[Chorus]` |
| `seed` | integer | Optional, 0 to 2147483647. If absent the worker picks one and reports it in `params_resolved.seed` |
| `cot` | enum | `full` (default), `melody`, `off` |
| `cfg_scale` | number | Optional, greater than 0 and at most 20 (the engine rejects values above 20; changed in M1). If absent, do not pass it to the engine (use the model default) |
| `abc` | string | Optional, 1 to 16000 characters, not blank (added after M5; API 1.1). An ABC score the model sings instead of planning one. `cot` must be `full` (the score is read as chord-annotated) or `melody` (melody only); with `off` it is a `validation_error`. Passed verbatim to the engine. The engine's real limit is in tokens (the whole prompt must fit in 15576 tokens); a score that overflows fails the job as `engine_error` |

Job object:

```json
{
  "id": "job_01JABCDEF",
  "client_request_id": "0b8e5f0c-6a3e-4d62-9d0e-2f6f3d1f7a11",
  "operation": "song.generate",
  "state": "running",
  "stage": "semantic",
  "stages": ["plan", "semantic", "synthesis", "decode", "save"],
  "progress": null,
  "params": { "style": "...", "lyrics": "...", "cot": "full" },
  "params_resolved": { "seed": 12345 },
  "created_at": "2026-11-07T10:00:00Z",
  "started_at": "2026-11-07T10:00:02Z",
  "finished_at": null,
  "timings": { "queue_s": 2.1, "plan_s": 9.4 },
  "error": null,
  "artifacts": []
}
```

Rules for the job object:

- `state` is one of `queued`, `running`, `succeeded`, `failed`, `canceled`. `stage` is `null` until running.
- `stages` is what the operation will report, in order. If the staged API turns out unusable (OQ-2), it is `["generate", "save"]`. The UI renders steps from this list and never hardcodes stage names.
- `progress` is a number from 0 to 1 within the current stage, or `null` (the MVP may always send `null`).
- `artifacts` is filled when the job succeeds: `{name, role, media_type, size_bytes, sha256}`. Roles: `primary_audio`, `score`, `plan`, `result`, `intermediate`, `other`. `name` is a relative POSIX path.
- Timestamps are ISO 8601 UTC.

Status and capabilities (abbreviated):

```json
{
  "uptime_s": 1234, "idle_seconds": 87,
  "model": { "state": "ready", "error": null },
  "gpu": { "name": "NVIDIA GeForce RTX 4090", "vram_total_mb": 24564, "vram_used_mb": 11500 },
  "queue": { "queued": 0, "running": null, "max": 4 }
}
```

```json
{
  "api_version": "1.1", "worker_version": "0.1.1",
  "engine": { "kind": "yue2", "package_version": "0.1.5", "models": { "lm": "m-a-p/YuE2-3B", "vae": "m-a-p/YuE2-Vae" } },
  "operations": [ { "name": "song.generate", "stages": ["plan", "semantic", "synthesis", "decode", "save"], "params_schema": { } } ]
}
```

### Errors

Every non-2xx response is `{"error": {"code": "...", "message": "...", "retryable": false, "details": {}}}`. Failed jobs carry the same object in `job.error`.

| Code | HTTP | Meaning | Retryable |
| --- | --- | --- | --- |
| `unauthorized` | 401 | Missing or wrong token | no |
| `not_found` | 404 | Unknown job or artifact | no |
| `conflict` | 409 | Action not allowed in the job's current state | no |
| `validation_error` | 422 | Bad params or unknown operation; `details` lists fields | no |
| `queue_full` | 429 | Queue at `MAX_QUEUE` | yes |
| `not_ready` | 503 | Model still loading or failed to load; `Retry-After` header set | yes |
| `oom` | job | GPU ran out of memory | maybe |
| `engine_error` | job | Any other engine exception; `details` has a short type and message, never a token | maybe |
| `worker_restarted` | job | Worker restarted while the job ran | yes |
| `internal` | 500 | Unexpected worker bug | maybe |

The server adds client-side codes to a generation's error: `worker_unreachable`, `incompatible_worker`, `download_failed`, `not_configured`.

### Local server API (used by the UI, all under `/api`)

| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | Server is up; includes a short worker summary (the check result from S-5) |
| `GET /api/settings`, `PUT /api/settings` | Worker URL and token; GET returns `token_set: true or false`, never the token |
| `POST /api/worker/check` | Test the saved settings and return the check result |
| `GET /api/capabilities` | Cached worker capabilities |
| `POST /api/generations` | Create and submit a generation |
| `GET /api/generations` | List, newest first, with `limit` and `cursor` |
| `GET /api/generations/{id}` | One generation |
| `POST /api/generations/{id}/cancel` | Cancel |
| `DELETE /api/generations/{id}` | Delete record and local files |
| `GET /api/generations/{id}/artifacts/{name:path}` | Stored artifact, with Range support |

Generation object:

```json
{
  "id": "gen_01JABCDEF",
  "title": "Untitled 2026-11-07 10:00",
  "operation": "song.generate",
  "request": { "style": "...", "lyrics": "...", "seed": 12345, "cot": "full" },
  "status": "running",
  "stage": "semantic",
  "stages": ["plan", "semantic", "synthesis", "decode", "save"],
  "error": null,
  "worker_job_id": "job_01JABCDEF",
  "timings": { "queue_s": 2.1 },
  "artifacts": [ { "name": "audio.flac", "role": "primary_audio", "media_type": "audio/flac", "size_bytes": 31457280, "url": "/api/generations/gen_01JABCDEF/artifacts/audio.flac" } ],
  "created_at": "2026-11-07T10:00:00Z",
  "updated_at": "2026-11-07T10:00:09Z"
}
```

`status` is `queued`, `running`, `downloading`, `succeeded`, `failed` or `canceled`. `downloading` exists only on the server: the worker job has succeeded but the artifacts are not yet verified locally.

If the worker answers 404 for a job the server still tracks (for example, the job expired under W-11 while the server was off, or the volume was wiped), the generation fails with `not_found` ("The worker no longer has this job") instead of polling forever. Deleting such a generation still works: the remote delete is best effort (added after M5).

### Rules both sides follow

- Every request is short. Nothing waits for a generation inside one HTTP call, because RunPod's proxy may cut long requests. Progress is by polling (worker every 2 s from the server, server every 1.5 s from the UI); SSE can be added later without changing the job object.
- Artifact paths are never joined from user input. Accept a name only if it is in the job's manifest; reject `..`, absolute paths and backslashes.
- Retry transient failures (network errors, 502, 503, 504, and 429) with exponential backoff, at most 3 times. Never retry a 4xx other than 429.
- Versioning: `api_version` is major.minor. The server calls `/v1/capabilities` on every worker check and refuses a worker whose major differs (`incompatible_worker`).

## 7. Engine facts, interfaces and extensibility

The extension points below are mandatory deliverables, not nice-to-haves: they cost little now and a lot to retrofit. YuE2-specific code is confined to one file, because the model's API is new and still changing.

### What is known about YuE2 (checked against the official repo docs, 2026-11-07; updated from the 0.1.5 wheel source in M0)

Details, line references and the open-question findings are in `docs/yue2-api-notes.md`. "Source" below means read from the wheel's code, not observed on a GPU.

| Topic | Fact | Certainty |
| --- | --- | --- |
| Install | Wheel `yue2_infer-0.1.5-py3-none-any.whl` is downloaded from the HF repo `m-a-p/YuE2-3B` with `hf download`, then `pip install`ed. Pin this version (sha256 in the notes). It hard-pins `torch==2.10.0`, `transformers==4.57.6` and `huggingface-hub==0.36.2` | Source |
| Load | `from yue2 import YuE2Pipeline`; `YuE2Pipeline.from_pretrained(model="m-a-p/YuE2-3B", *, vae="m-a-p/YuE2-Vae", revision=None, vae_revision=None, local_files_only=False, token=None, cache_dir=None, progress=True, **kwargs)`. `device` (default `"auto"`) and other options pass through `**kwargs`. Weights (about 7.8 GB) download from the Hub on first use and are SHA-256 verified on every load. BF16 is required on CUDA | Source |
| One-call generation | `song = pipe(style=..., lyrics=..., cot=..., seed=..., cfg_scale=..., abc=..., cancelled=..., on_token=...)` returns a `SongResult` with `save("x.flac")`, `save_artifacts(dir)` and `truncated`, a dict `{"abc": bool, "semantic": bool}`. `seed` must be an int (default 831001; `None` is rejected). `cfg_scale` must be in [0, 20] | Source |
| Staged generation | `pipe.plan(**request)` returns a `SymbolicPlan` (`save(dir)`, `SymbolicPlan.load(dir)`); `generate_semantic(plan)` returns a `SemanticResult` (`tokens: list[int]`, `truncated`); `synthesize(semantic)` returns float32 latents `[frames, 64]`; `decode(latents)` returns float32 stereo audio `[samples, 2]` at 48000 Hz (rate not returned); `pipe.close()` at the end. One-call generation is exactly these four calls plus building a `SongResult` | Source |
| Artifacts | `save_artifacts` writes `audio.flac` (PCM_24), `score.abc` (when cot is not `off`), `abc_tokens.npy`, `prefix.npy`, `plan.json`, `plan_manifest.json`, `semantic.npy`, `latent.npy`, `request.json`, `config.json` (effective config) and `result.json` (identity, `truncated`, timings, weight and artifact hashes) | Source |
| Inputs | `style` is free text (genre, instruments, vocal character, language, tempo). `lyrics` is passed to the model verbatim; the package parses no tags. The official example uses `[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`, `[Interlude]`, `[Bridge]` and `[Outro]`. `cot` is `full`, `melody` or `off`. `abc` needs `full` or `melody`. The prompt plus the 9000-token semantic budget must fit in a 24576-token context, which gives songs of about 8 to 360 s (derived) | Source; limits derived |
| Concurrency | Not concurrent: the package says "one request at a time" and "do not share a pipeline concurrently". Reusing one pipeline across sequential requests is the package's own batch pattern | Source; memory growth unverified |
| Reproducibility | Pin `revision` and `vae_revision`. A different GPU, runtime version or sampling setting can change a seeded result | Documented |
| Hardware | Linux, NVIDIA GPU with BF16, 24 GB VRAM and 24 GB host RAM. The model card reports 71 s for 214.85 s of audio on an RTX 4090 with 11.18 GiB peak VRAM, and 14.08 GiB at maximum context | Model card figures; not measured by us |

**Rule:** only `worker/studio_worker/engines/yue2.py` may import `yue2`. In milestone M0 the agent downloads the wheel, reads its source, and records every call it uses, with real signatures and return types, in `docs/yue2-api-notes.md`. Where the notes contradict this table, the notes win and this PRD is updated.

### Worker design

Four layers; dependencies point downward only. Operations never import FastAPI, so a RunPod serverless handler can reuse them later.

1. Transport: FastAPI routes, auth, error envelope. Knows nothing about YuE2.
2. Jobs: store, FIFO queue, single executor thread. Generic; knows nothing about songs.
3. Operations: a registry; `song.generate` is the first entry.
4. Engines: `Yue2Engine` (real) and `FakeEngine` (tests, dev).

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
```

- `song.generate` uses the staged methods when `supports_staged` is true and reports one stage each (`plan`, `semantic`, `synthesis`, `decode`, then `save`). Otherwise it calls `generate()` and reports `generate`, then `save`.
- Operations register with a `@register_operation` decorator. `/v1/capabilities` is built from the registry, so a new operation needs no change to routes or core.
- Each job lives in `JOBS_DIR/<id>/` with an atomically written `job.json` and an `artifacts/` folder. On startup, any job found `queued` or `running` becomes `failed` with `worker_restarted`.
- The executor is one dedicated thread. Route handlers never block on the GPU.
- Cancellation is cooperative: operations check `ctx.cancel_requested()` between stages.
- **Approved after M5, not yet implemented:** cancellation inside a stage. `SongEngine.plan`, `generate_semantic`, `synthesize` and `generate` gain an optional keyword `cancelled: Callable[[], bool] | None = None`. `song.generate` passes `ctx.cancel_requested`, and `Yue2Engine` passes it to yue2's `cancelled=` and turns yue2's `InterruptedError` into a canceled job. `decode` takes no callback, because yue2 checks once before the VAE stage instead. The change is additive: an engine that ignores the callback still meets W-10. Until it is built, a long `semantic` stage runs to its end before a cancel takes effect.

### Local server design

Four core modules (`config`, `db`, `storage`, `backend`), a poller, and feature modules on top.

```python
class MusicBackend(Protocol):
    async def status(self) -> WorkerStatus: ...
    async def capabilities(self) -> Capabilities: ...
    async def submit(self, operation: str, params: dict, client_request_id: str) -> JobRef: ...
    async def get_job(self, ref: JobRef) -> JobView: ...
    async def cancel(self, ref: JobRef) -> None: ...
    async def delete_job(self, ref: JobRef) -> None: ...
    async def download_artifact(self, ref: JobRef, name: str, dest: Path) -> None: ...
```

- A factory maps `backend.kind` to an implementation. The MVP registers `http_worker`, which talks to the worker API in section 6. Future kinds (serverless, local yue2.cpp, ComfyUI) are new classes behind the same interface.
- Fake mode is not a second backend. It is the real worker app started locally with `ENGINE=fake`, so the whole HTTP path is exercised without a GPU.
- Feature modules live in `studio_server/features/<name>/` and export a `Feature` with a `name`, an `APIRouter` and an optional `on_startup` hook. `features/__init__.py` holds the list (`settings`, `worker`, `generations` in the MVP), and `main.py` mounts them. Features may use `core`, never each other; shared logic moves into `core`.
- Tables are generic. `generations` holds `id`, timestamps, `title`, `operation`, `request_json`, `status`, `stage`, `stages_json`, `error_json`, `worker_job_id`, `client_request_id`, `timings_json`, `artifacts_json`, `data_dir`. It has no song-specific columns. A later feature needing its own data adds its own table in its own migration.
- The poller is one asyncio task. Each tick it advances every non-terminal generation. On worker success it sets `downloading`, fetches artifacts (at most 2 at once) to a temp file, verifies size and sha256, renames atomically, then sets `succeeded`. On startup it simply resumes.
- If the worker is unreachable for longer than `STUDIO_UNREACHABLE_GRACE_S` (default 600), the generation fails with `worker_unreachable`.

### Web design

```ts
export interface FeatureModule {
  id: string;
  requires?: string[];                          // operation names from /api/capabilities
  routes: RouteObject[];
  nav?: { label: string; to: string; order: number };
}
```

- `web/src/features/index.ts` lists the modules (`create`, `library`, `settings` in the MVP). The shell builds routes and navigation from it and hides modules whose `requires` are not in the capabilities.
- An `operationViews` registry maps an operation name to its `Summary` and `Result` components. The library and the result panel render through it, so a new operation ships its own card without touching the library. The MVP registers `song.generate` only.
- The API client is generated from OpenAPI (`openapi-typescript` + `openapi-fetch`); server state uses TanStack Query.

### How later features plug in

| Feature | Worker | Server | Web | Contract change |
| --- | --- | --- | --- | --- |
| Score-first (edit ABC, re-render). **Partly built after M5:** `song.generate` takes `abc`, and the web edits and re-sends the score; what remains is planning a score without rendering audio | Operations `song.plan` (returns `plan.json`, `score.abc`) and `song.render` (takes a plan or ABC) | `scores` feature; generation gets an optional `parent_id` (new migration) | `score` feature: ABC editor route and nav entry | Additive: new operation schemas |
| Exact replay | `song.render` accepts stored tokens or latents as input | Sends stored artifacts as inputs | Replay button in the library card | Additive: an input-upload endpoint `POST /v1/uploads`; params may reference `{"upload": id}` |
| Covers | `song.cover` plus the transcription model in the engine | `covers` feature and source-audio upload | Cover route | Same upload endpoint |
| Stems, mastering | Operation `audio.stems`, or local processing | Feature module | Route | None |
| LoRA | Optional `lora` param on `song.generate`; operation `lora.train` | Model-registry feature | Route | Additive param |
| New backend | None | New `MusicBackend` class and factory entry | A choice in Settings | None |
| Pod start/stop, idle shutdown | None | `pods` feature using the RunPod API | Controls in the status pill | None |
| MCP or agent surface | None | `mcp` feature exposing existing services | None | None |

### Rules for the agent

- Never branch on an operation name outside the worker registry and the web `operationViews` registry.
- Every generation stores its request and its artifact manifest (G4). Never discard them.
- Change contracts first, regenerate types, then change code.
- Keep generic tables and job objects free of song-specific fields.
- Build only the abstractions listed here. Extra ones need a note in `docs/decisions.md`.
- Prove extensibility with a test: register a throwaway operation `test.echo` inside the test suite only, and show it passes through submit, status, capabilities and artifact download with no change to core code.

## 8. RunPod deployment

The worker ships as one Docker image with no weights inside. A RunPod pod supplies the GPU, and a network volume keeps the downloaded weights between runs.

### Image

- Name: `<registry>/studio-worker:<version>` in a registry the owner chooses (GHCR or Docker Hub). No secrets and no weights are baked in.
- Base: an NVIDIA CUDA runtime image on Ubuntu 24.04, which ships Python 3.12 (the repo README asks for 3.12). The agent chooses the CUDA version that the YuE2 wheel's PyTorch needs and checks it against RunPod host drivers (OQ-6).
- Install: `huggingface-hub==0.36.2`, then `hf download m-a-p/YuE2-3B yue2_infer-<ver>-py3-none-any.whl`, then `pip install` of that wheel. `<ver>` is the build arg `YUE2_WHEEL_VERSION`, default `0.1.5`. Then install `studio_contracts` and `studio_worker`.
- Entrypoint: `uvicorn studio_worker.main:app --host 0.0.0.0 --port 8000`, one process.
- `make build-worker` builds it. A second `fake` target (no CUDA, no wheel) is for tests.

### Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `WORKER_API_KEY` | required | Bearer token; the worker refuses to start if it is shorter than 32 characters |
| `ENGINE` | `yue2` | `yue2` or `fake` |
| `YUE2_MODEL_REPO` | `m-a-p/YuE2-3B` | Model repo |
| `YUE2_VAE_REPO` | package default | Decoder repo |
| `YUE2_MODEL_REVISION`, `YUE2_VAE_REVISION` | unset | Pin for reproducible output; recorded in artifacts |
| `HF_HOME` | `/workspace/hf` | Weight cache, on the network volume |
| `HF_TOKEN` | unset | Only if the repos ever require it |
| `JOBS_DIR` | `/workspace/jobs` | Job folders |
| `MAX_QUEUE` | `4` | Queue cap (W-3) |
| `JOB_TTL_HOURS` | `24` | Job cleanup (W-11) |
| `LOG_LEVEL` | `INFO` | JSON logs |
| `PORT` | `8000` | HTTP port |

### Startup behaviour

1. `/healthz` answers at once.
2. The model loads in a background thread; `/v1/status` shows `model.state` = `loading`.
3. While loading, `POST /v1/jobs` returns 503 `not_ready` with `Retry-After`.
4. When loading succeeds the state is `ready`. If it fails, the state is `error` with a short message, and submits keep returning 503.

### Runbook (the agent turns this into `docs/runpod.md`)

1. Create a RunPod network volume, at least 50 GB, in a region that has 24 GB GPUs. Confirm the size once the weights' total size is known (OQ-4).
2. Create a pod template: the image above, HTTP port 8000 exposed, the volume mounted at `/workspace`, the environment variables above.
3. Deploy on an NVIDIA GPU with BF16 and at least 24 GB VRAM. The authors benchmarked an RTX 4090; other cards are untested.
4. Generate a token with `openssl rand -hex 32`. Note the pod's HTTP proxy URL (it has the shape `https://<pod-id>-8000.proxy.runpod.net`; confirm in the console).
5. Paste the URL and token into Settings. The pill shows "warming up" until the model is loaded. The first start downloads the weights to the volume and is slow; later starts reuse them.
6. Run `scripts/smoke_worker.py --url <url> --token <token>`.
7. When finished, stop or terminate the pod according to RunPod's rules for pods with network volumes (verify in the RunPod docs). A new pod may get a new URL, which is why the URL is editable in Settings.

### Cost controls

- An idle pod keeps billing. The MVP relies on manual stop, a RunPod spend alert (the runbook says to set one), and `idle_seconds` in `/v1/status`.
- P2, design only: an optional `IDLE_SHUTDOWN_MINUTES` that stops the pod from inside the worker, and a later `pods` server feature that starts and stops pods through the RunPod API. The agent researches the API before implementing either.

### Security

- TLS ends at RunPod's proxy. The UI warns if the worker URL is `http://` and not localhost.
- Only port 8000 is exposed; no SSH is needed.
- The token is never logged, never baked into the image, and never returned by the local server.
- Lyrics and style are not logged at INFO level.

### What the agent can and cannot verify

The agent has no GPU and no RunPod account. It can build the `fake` image, run all tests, and write the Dockerfile and runbook. It cannot prove the real image starts or generates. Real-engine behaviour is verified by the owner in milestone M6, and the agent must say so instead of claiming it.

### Serverless later

Because operations do not import FastAPI, a `runpod_handler.py` can later call the same registry. A `runpod_serverless` backend would map `submit` and `get_job` to RunPod's run and status calls. None of this is in the MVP.

## 9. Local frontend

The UI is a single-page web app with two views, Create and Library, plus a Settings dialog and a worker status pill in the header. It is served by the local server in normal use and by the Vite dev server during development.

### Create view

Two columns on a wide screen, stacked on a narrow one. The form is on the left, the result card on the right.

| Field | Behaviour |
| --- | --- |
| Title | Optional; defaults to `Untitled <local date and time>` |
| Style | Multiline, required. Helper text: genre, instruments, vocal character, language, tempo |
| Lyrics | Multiline monospace, required. Helper text shows section tags such as `[Verse]` and `[Chorus]` |
| Score (ABC) (collapsed; added after M5) | Optional monospace ABC score. When it has content, a "Score type" choice sets `cot`: full score (melody and chords) or melody only; `off` is disabled. "Clear score" empties it. Blank means the model plans the score |
| Advanced (collapsed) | Seed (blank means random), planning mode (`full`, `melody`, `off`; default `full`), CFG scale (optional number) |
| Create | Primary button; disabled while submitting |
| Example | Fills a sample style and lyrics (below) |

The result card has one state at a time:

| State | What the card shows |
| --- | --- |
| Idle | A placeholder: "Your song will appear here" |
| Submitting | A spinner |
| Queued | "Waiting for the GPU", plus a Cancel button |
| Running | The `stages` list with the current stage marked, plus an elapsed timer; no promised duration. A Cancel button |
| Downloading | "Fetching your song" |
| Succeeded | Audio player, Download WAV (converted in the browser from the stored FLAC; changed after M5), the score as ABC text with an "Edit score" button (added after M5; shown when there is a `score` artifact), every stored file (the FLAC included) under "Files", Reuse settings |
| Failed | A plain-language message, a Details toggle (code and message), Retry (new generation, same request) |
| Canceled | A short note |

- Cancel (added after M5) shows only while the generation is queued or running. A running job stops at the next stage boundary, so after a successful request the button reads "Canceling…" until the status turns `canceled`. A refused cancel shows the plain-language message.
- The form draft is kept in `localStorage` and restored on load. This is a convenience only; the library is the record.
- The active generation id lives in the URL (`/create?gen=<id>`), so a reload resumes progress.
- On any error the form keeps what the user typed.

Example content. The style line is taken from the YuE2 README; the lyrics are original. The agent must check the tag syntax against the installed package and `docs/generation.md` (OQ-1) and fix the example and helper text if they differ.

```text
style:  English, jazz-funk, warm lead vocal, Rhodes, bass and drums
lyrics:
[Verse]
Streetlights hum along the avenue
Coffee steam and a rhythm just for two
We walk slow, we've got nowhere to be
The city plays a tune for you and me

[Chorus]
Tonight we're awake, tonight we're alive
The bass line carries us home
Tonight we're awake, we'll dance till light
And never feel alone
```

### Library view

- Newest first. Each row: title, first 80 characters of the style, status chip, created time, inline play, Reuse settings, Delete (with confirmation).
- A queued or running row also has Cancel. Delete stays disabled until the generation has finished: cancel first, then delete.
- A row opens `/library/:id`: the stored request (seed included), the artifacts with sizes, and timings.
- Empty state: a short line and a link to Create.
- Rows render through the `operationViews` registry (section 7).

### Settings dialog and status pill

- Settings: Worker URL (must be `https://`, or `http://` for localhost only), Token (password field; shows "saved" when set; leaving it blank keeps the saved one), Test connection, Save.
- Status pill: grey = not configured, red = unreachable or unauthorized, amber = warming up, green = ready. It polls `/api/health` every 10 s while the tab is visible.

### Plain-language messages

| Code | Message shown |
| --- | --- |
| `not_configured` | Add your worker URL and token in Settings. |
| `worker_unreachable` | Can't reach the GPU worker. Is the pod running? |
| `unauthorized` | The worker rejected the token. |
| `not_ready` | The model is still loading. Try again in a minute. |
| `queue_full` | The worker queue is full. Try again shortly. |
| `validation_error` | The field-level messages from `details` |
| `oom` | The GPU ran out of memory. Try shorter lyrics, or retry. |
| `engine_error` | The model failed to generate this song. |
| `worker_restarted` | The worker restarted during generation. Retry. |
| `download_failed` | The song was made but could not be downloaded. It stays on the worker for a limited time. |
| `incompatible_worker` | This worker version does not match the app. Update the worker image. |

### Stack and tests

React with strict TypeScript, Vite, React Router, TanStack Query (polling through `refetchInterval` while a generation is not terminal), Tailwind CSS. Tests: Vitest with Testing Library for components, Playwright for one end-to-end smoke test against `make run-fake`. Progress updates use `aria-live="polite"`, and every control has a label.

## 10. Repository layout, stack and configuration

One monorepo: three Python packages in a uv workspace, plus one Node app. The folder names below are the contract between milestones, so keep them.

```text
studio/
  README.md  Makefile  pyproject.toml  .env.example  NOTICE.md
  docs/            architecture.md  runpod.md  adding-features.md  yue2-api-notes.md  decisions.md
  contracts/       studio_contracts/   # Pydantic models, error codes, enums; exports OpenAPI
    openapi/       worker.json  server.json            # generated, committed
  worker/
    studio_worker/
      main.py  config.py  auth.py  errors.py
      api/         # routes: status, capabilities, jobs, artifacts
      jobs/        # store.py  queue.py  executor.py
      operations/  # base.py  registry.py  song_generate.py
      engines/     # base.py  yue2.py  fake.py          # only yue2.py imports yue2
    Dockerfile  tests/
  server/
    studio_server/
      main.py  config.py
      core/        # db.py  storage.py  poller.py  backend/{base.py,http_worker.py,factory.py}
      features/    # __init__.py (FEATURES list)  settings/  worker/  generations/
      migrations/  # Alembic
    tests/
  web/
    src/
      app/         # shell, router, providers
      api/         # generated schema.d.ts + client
      features/    # index.ts  create/  library/  settings/
      components/  operationViews.ts
    tests/  e2e/
  scripts/         smoke_worker.py  gen_api_types.sh  build_worker.sh
```

### Stack

| Layer | Choice |
| --- | --- |
| Language and tooling | Python 3.12, `uv`, `ruff`, `pyright` or `mypy` in strict mode on `contracts/`, `worker/` and `server/` |
| Services | FastAPI, Uvicorn, Pydantic v2, `httpx` |
| Data | SQLite via SQLAlchemy 2 with Alembic migrations |
| Python tests | `pytest`, `pytest-asyncio`, `httpx.ASGITransport` for in-process app-to-app calls |
| Web | React, strict TypeScript, Vite, React Router, TanStack Query, Tailwind CSS, `openapi-typescript`, `openapi-fetch` |
| Web tests | Vitest, Testing Library, Playwright |
| Packaging | Docker for the worker only; the server and UI run directly on the owner's machine (Linux, macOS or Windows) |

### Contracts workflow

`make contracts` exports the OpenAPI documents from the FastAPI apps into `contracts/openapi/` and regenerates `web/src/api/schema.d.ts`. The generated files are committed. The test suite fails if regenerating them changes anything.

### Server configuration

Precedence, highest first: values saved through Settings (in the database), environment variables, defaults. Environment variables also load from `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STUDIO_DATA_DIR` | `~/.studio` | Holds `studio.db` and `generations/<id>/` |
| `STUDIO_HOST`, `STUDIO_PORT` | `127.0.0.1`, `8080` | Bind address; never default to `0.0.0.0` |
| `STUDIO_WORKER_URL` | unset | Default worker URL |
| `STUDIO_WORKER_TOKEN` | unset | Default token |
| `STUDIO_POLL_INTERVAL_S` | `2` | Poller tick |
| `STUDIO_UNREACHABLE_GRACE_S` | `600` | Time before an unreachable worker fails a generation |
| `STUDIO_DOWNLOAD_INTERMEDIATES` | `true` | Also download `intermediate` artifacts (tokens, latents). The agent measures their size in M4 and records it (OQ-4); if one job exceeds about 100 MB, default this to `false` and say so in the docs |
| `STUDIO_LOG_LEVEL` | `INFO` | JSON logs |

### Make targets

| Target | Does |
| --- | --- |
| `make setup` | Installs Python and Node dependencies |
| `make dev` | Runs the server with reload and the Vite dev server |
| `make run-fake` | Runs a fake-engine worker and the server together; the setup the end-to-end test uses |
| `make test` | Python tests, web tests, and the contracts-up-to-date check |
| `make lint` | `ruff`, type checker, ESLint, `tsc --noEmit` |
| `make contracts` | Regenerates OpenAPI and TypeScript types |
| `make build-worker` | Builds the worker image |

## 11. Non-functional requirements

The app must survive restarts and flaky networks without losing a generation, keep its token and data private, and stay within the model's licence.

| Area | Requirement |
| --- | --- |
| Reliability | Submits are idempotent through `client_request_id`. The server resumes polling after a restart. A worker restart fails running jobs with `worker_restarted`. Network calls retry as section 6 says. Downloads use a temp file, a sha256 check and an atomic rename, so no partial file is ever served |
| Performance | API calls other than artifact transfer answer in under 200 ms at the 95th percentile on a laptop. The authors report about 71 s for a 3.6-minute song on a warm RTX 4090 (approximate; re-verify). For a song of about 3 minutes, the owner's acceptance test expects completion within 5 minutes, and `timings` records every stage so slowness can be explained |
| Security | Bearer token of at least 32 random characters, compared in constant time. The token is never logged, never returned by an API, and entered through a password field. The server binds to `127.0.0.1` by default. Input lengths are limited (section 6). Artifact paths are validated against the manifest. Dependencies are pinned. No telemetry is sent anywhere |
| Privacy | All user data is stored locally. The worker keeps job files only until `JOB_TTL_HOURS`. Lyrics and style are not written to INFO logs |
| Observability | JSON logs with a request id on both services. `/v1/status` and `/api/health` give the current picture. Every job stores its timings |
| Portability | Server and UI run on Linux, macOS and Windows with no local GPU. Use `pathlib` throughout and avoid shell-specific scripts outside `Makefile` and `scripts/` (which also run under Git Bash or WSL, documented) |
| Compatibility | The yue2 wheel version is pinned and recorded in `/v1/capabilities` and in artifacts. API versions follow section 6 |
| Maintainability | Strict typing, `ruff` clean, and a test for every endpoint and every job state transition. Decisions the PRD leaves open go into `docs/decisions.md` in one short entry each |
| Licence | YuE2 weights are CC BY-NC 4.0 with an additional creator permission: individual creators may monetise their outputs, academic use is non-commercial, and companies need a licence from the authors. The app is therefore for personal use. `NOTICE.md` and an About note in the UI state this, name the model, and credit its authors. Never bundle weights in the image or the repo. The repository's own licence is the owner's decision (OQ-8); until then, add no licence file |

## 12. Test plan and acceptance

Everything except real inference is tested without a GPU. The real run is a scripted check the owner performs.

### Test layers

| Layer | Covers | Run by |
| --- | --- | --- |
| Worker unit | Param validation, registry, job store transitions, queue cap, auth, error envelope, idempotency, TTL cleanup | Agent, `make test` |
| Server unit | Write-only token, poller state machine, download verification, path safety, migrations | Agent, `make test` |
| Integration | Server and worker apps in one process over `httpx.ASGITransport`, with `FakeEngine` | Agent, `make test` |
| Contract | OpenAPI regenerates identically; `/v1/capabilities` schemas match the registry | Agent, `make test` |
| Web component | Form validation, every result-card state, every error message | Agent, `make test` |
| End to end | Playwright against `make run-fake`: configure, create, watch progress, play, reload, delete | Agent, `make test` |
| GPU smoke | `scripts/smoke_worker.py` against a real pod | Owner |

### Acceptance scenarios

| ID | Scenario | Verified by |
| --- | --- | --- |
| AT-1 | Happy path: with a fake worker, submitting style and lyrics walks the stages in order, ends `succeeded`, the 48 kHz stereo FLAC plays, and the library lists it | Agent |
| AT-2 | Validation: the UI blocks empty style or lyrics; the API returns 422 `validation_error` for an over-limit style | Agent |
| AT-3 | Worker unreachable: the pill goes red and Create shows the `worker_unreachable` message; the typed input is kept | Agent |
| AT-4 | Wrong token: 401 shows as "rejected the token"; the token appears in no response and no log line | Agent |
| AT-5 | Server restart mid-job: kill the server while a fake job runs, restart it, and the generation finishes and downloads | Agent |
| AT-6 | Queue full: submitting `MAX_QUEUE` + 2 jobs surfaces `queue_full` for the excess | Agent |
| AT-7 | Cancel: a queued job cancels at once; a running job ends `canceled` at the next stage boundary and leaves no artifacts | Agent |
| AT-8 | Idempotency: the same `client_request_id` posted twice creates one job | Agent |
| AT-9 | Persistence: reload and server restart keep the library; delete removes the files on disk | Agent |
| AT-10 | Extensibility: the throwaway `test.echo` operation passes submit, status, capabilities and artifact download with no change to core code | Agent |
| AT-11 | Worker restart mid-job: the generation fails with `worker_restarted`, and Retry creates a new one | Agent |
| AT-12 | Warm-up: with a delayed fake model load, status shows `loading`, submit returns 503 `not_ready`, the pill is amber, then green | Agent |
| AT-13 | Real generation: the smoke script succeeds; `audio.flac` is 48 kHz stereo and longer than 10 s; `score.abc` and `result.json` are present; the `truncated` flags are printed | Owner |
| AT-14 | Real failure handling: lyrics at the 8000-character limit end as `succeeded` (possibly truncated) or as `failed` with `oom` or `engine_error`; the worker stays alive and accepts the next job | Owner |

### The smoke script

`scripts/smoke_worker.py --url <url> --token <token> [--timeout 900]` runs these steps and exits non-zero on the first failure:

1. `GET /healthz`, then wait up to 15 minutes for `/v1/status` to show `model.state` = `ready`.
2. Check that `/v1/capabilities` lists `song.generate` and a compatible `api_version`.
3. Submit a short two-section song with a fixed seed, so the run is short and repeatable.
4. Poll to a terminal state, printing each stage change and its time.
5. Download every artifact and verify its sha256.
6. Check the FLAC with `soundfile` (48 kHz, 2 channels, longer than 10 s) and that `score.abc` exists.
7. Print timings, total artifact size and the `truncated` flags.

## 13. Milestones and working rules for the agent

Work in seven milestones, in order. Each one ends with passing tests, updated docs and a short report to the owner.

1. **M0, scaffold and spike.** Create the monorepo, tooling, `Makefile` and the contracts skeleton. Download the yue2 wheel and read its source. Write `docs/yue2-api-notes.md` with every call used, its real signature and return type, and what the source says about OQ-1 to OQ-5. Exit: `make setup lint test` runs clean; the notes are committed.
2. **M1, worker core.** Contracts for jobs, errors and capabilities; registry; job store; queue; executor; auth; routes; `FakeEngine`; `song.generate` on the fake engine. Exit: W-1 to W-9 and W-12 tested; AT-6, AT-8, AT-11, AT-12 pass.
3. **M2, server core.** Database and migrations, settings, backend and factory, poller, storage, generations API, Range serving. Exit: S-1 to S-8 tested; AT-1 (API level), AT-4, AT-5, AT-9 pass.
4. **M3, web app.** Shell, feature registry, Create, Library, Settings, status pill, `operationViews`, generated client, Playwright test. Exit: U-1 to U-7 done; AT-1 to AT-3 pass at UI level.
5. **M4, real engine and image.** `Yue2Engine` (staged if viable, otherwise single call), Dockerfile, `scripts/smoke_worker.py`, `docs/runpod.md`, `NOTICE.md`. Set the intermediates default from measured artifact sizes. Exit: docs complete and every build step checked as far as a GPU-less machine allows; real behaviour clearly marked as unverified.
6. **M5, hardening and extras.** Cancel (W-10, S-9), TTL cleanup (W-11), retry logic, the error-message table, `docs/adding-features.md`, `docs/architecture.md`. Exit: AT-7 and AT-10 pass; every agent-verified acceptance test passes.
7. **M6, owner acceptance.** The owner follows the runbook and runs the smoke script; the agent fixes what it finds. Exit: AT-13 and AT-14 pass and the checklist in section 1 is fully ticked.

### Working rules

- Read the whole PRD before writing code, and follow the milestone order. Do not skip M0.
- Never guess how YuE2 behaves. Read the wheel source or the official docs. Anything still unresolved goes to the owner as a question with options and your recommended default.
- Stay in scope. The later features in section 2 are not built, but every extension point in section 7 is required.
- Ask the owner before adding a paid service, choosing a repository licence, or making a breaking contract change after M2.
- Where this PRD is silent, choose the simplest option that keeps section 7 intact, and log it in `docs/decisions.md` in a few lines.
- Make small commits with one concern each, and add tests with every behaviour change. Never commit secrets or a real `.env`.
- At the end of each milestone report what is done, the test results, what only the owner can verify, and any deviation from this PRD.
- Never claim a result you did not observe. In particular, no GPU run has happened until the owner reports one.
- Keep the docs true: every command written in them must have been run.

## 14. Assumptions, open questions, risks and roadmap

Eight questions are still open, and the defaults below let work proceed without waiting for an answer.

### Assumptions

- One user, on their own RunPod account, starting and stopping the pod by hand.
- The pod is Linux on x86 with an NVIDIA 24 GB GPU that supports BF16, and it can reach Hugging Face.
- The owner's computer has no suitable GPU, and its operating system is unknown, so the local parts must be cross-platform.
- The model is YuE2-3B with the default decoder, used through the official `yue2` package.

### Open questions

| ID | Question | Default until answered |
| --- | --- | --- |
| OQ-1 | Lyrics syntax beyond `[Verse]` and `[Chorus]`: other tags, case sensitivity, language rules, line length | Use only `[Verse]` and `[Chorus]` in examples; accept any text |
| OQ-2 | Return types of the staged calls, and whether staged results can be saved as the same artifacts as `save_artifacts` | If not, use the single call and the stages `generate` and `save` |
| OQ-3 | Valid ranges for `cfg_scale`, lyrics length and song length; the exact meaning of `truncated`. Should the UI warn when a song is truncated? | Guardrails from section 6; show a small notice when `truncated` is true |
| OQ-4 | Total weight download size, and artifact size per job (tokens, latents) | 50 GB volume; download intermediates unless a job exceeds about 100 MB |
| OQ-5 | Is the pipeline safe to reuse for many jobs without memory growth? Is it thread-safe? | One executor thread; the smoke script reports VRAM before and after, and the owner decides if a restart policy is needed |
| OQ-6 | CUDA and driver compatibility on RunPod hosts; BF16 behaviour on 24 GB cards other than the RTX 4090 | Pick the CUDA version the wheel's PyTorch needs; document the tested GPU |
| OQ-7 | Whether pods with network volumes can be stopped or only terminated, and whether the proxy URL survives | Treat the URL as changeable; Settings stays editable |
| OQ-8 | Repository licence and container registry (GHCR or Docker Hub) | No licence file; GHCR |

### Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| YuE2 is new and its API may change | Breaks the engine adapter | Pinned wheel; all YuE2 code in one file; `docs/yue2-api-notes.md` |
| A pod left running | Ongoing cost | Spend alert in the runbook, `idle_seconds`, planned idle stop |
| Out of memory at long songs | Failed jobs | `oom` error code; the worker survives and takes the next job (AT-14) |
| Proxy cuts long requests | Lost progress | Short requests and polling only |
| Slow first start while weights download | Looks broken | "Warming up" state and a persistent volume |
| Non-commercial licence | Limits use | Personal use stated in `NOTICE.md`; no weights redistributed |
| The agent cannot test on a GPU | Real bugs found late | Fake engine for everything else; the owner runs M6 early |

### Roadmap after the MVP

1. Score-first workflow: plan operation, then render from the score. (Editing a score and generating from it shipped after M5.)
2. Exact replay from stored tokens, via the input-upload endpoint.
3. Pod lifecycle: start, stop and idle shutdown through the RunPod API.
4. Library polish: takes grouped under a song, comparison, tags.
5. Covers through transcription.
6. Stems, mastering and export with metadata.
7. LoRA use and training on a pod.
8. Additional backends: RunPod serverless, local yue2.cpp.
9. MCP surface for agent control.

### Sources

Pages opened on 2026-11-07. Benchmark figures (about 71 s for a 3.6-minute song, 11 to 14 GiB peak VRAM) come from search excerpts of the model card; re-verify them in M0.

- [YuE repository README](https://github.com/multimodal-art-projection/YuE)
- [YuE `docs/generation.md`](https://github.com/multimodal-art-projection/YuE/blob/main/docs/generation.md)
- [YuE2-3B model card](https://huggingface.co/m-a-p/YuE2-3B)
- [YuE2-Studio](https://github.com/timoncool/YuE2-Studio), the reference app whose features inform section 2
- [yue2.cpp](https://github.com/ServeurpersoCom/yue2.cpp), the optional future local backend
