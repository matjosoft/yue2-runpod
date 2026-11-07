# YuE2 API notes (`yue2_infer` 0.1.5)

Written in M0 from the wheel's source, read on 2026-11-07. Only `worker/studio_worker/engines/yue2.py`
may import `yue2`; it must use the calls recorded here. Where these notes contradict the PRD §7 facts
table, these notes win (the table has been updated to match).

**How this was obtained.** The wheel was downloaded and unpacked, and its source was read. Nothing was
installed or executed, and no GPU was used. Signatures and return types below are therefore **read from
source, not observed at runtime**. The package has no type annotations; types come from the code paths.
References are `module.py:line` inside the wheel.

## 1. Provenance

| Item | Value |
| --- | --- |
| HF repo | `m-a-p/YuE2-3B` (not gated; repo sha `8f24312f187763b9854adeec87e437e2b03bbff1` on 2026-11-07) |
| Wheel | `yue2_infer-0.1.5-py3-none-any.whl`, 66,117 bytes. An older `0.1.3` wheel also sits in the repo; ignore it |
| Wheel sha256 | `8801e2c0d969db02df78d2994150b4ccd86077d87c24fdb8509b1f6f31462641` |
| Import name | `yue2` (`__version__ = "0.1.5"`) |
| VAE repo | `m-a-p/YuE2-Vae` (repo sha `152733a19ad43aa67e367f9b5503ef8075bb5126` on 2026-11-07) |

Download command used (it needs no token):

```bash
uvx --from huggingface_hub==0.36.2 hf download m-a-p/YuE2-3B yue2_infer-0.1.5-py3-none-any.whl --local-dir <dir>
```

**Requires-Python** is `>=3.10`. The exact dependency pins from METADATA:

- `torch==2.10.0`
- `transformers==4.57.6`
- `huggingface-hub==0.36.2`
- `safetensors==0.7.0`
- `tiktoken==0.12.0`
- `numpy==2.2.6`
- `soundfile==0.13.1`
- `accelerate==1.13.0`

There is no torchaudio. The extras are `fast` (vllm 0.19.0, triton 3.6.0), `serve` (fastapi, uvicorn), `test` and `bench`. We need none of them. The wheel has a console script, `yue2 = yue2.cli:main`.

## 2. Public surface

`yue2/__init__.py:5-15` has no `__all__`. A lazy `__getattr__` exposes:

- `YuE2Pipeline`, `SymbolicPlan`, `SemanticResult` and `SongResult` (from `pipeline`);
- `YuE2Config` and `YuE2ForCausalLM`;
- `YuE2VAE` and `YuE2VAEConfig`.

`SongRequest` and `Sampling` live in `yue2.protocol`. The `identity()` helper lives in `yue2.storage`.

## 3. Calls the engine will use

### Loading: `YuE2Pipeline.from_pretrained` (`pipeline.py:167-189`)

```python
YuE2Pipeline.from_pretrained(model="m-a-p/YuE2-3B", *, vae="m-a-p/YuE2-Vae", revision=None,
    vae_revision=None, local_files_only=False, token=None, cache_dir=None, progress=True, **kwargs)
```

`model` and `vae` each accept a local directory, a Hub repo id, or a `save_pretrained` folder that contains `pipeline.json`.

Hub downloads use `snapshot_download` with an allow-list (`storage.py:43-54`), so the model card's audio and asset files are not fetched.

The remaining `**kwargs` go to `__init__` (`pipeline.py:122-165`):

```python
__init__(model_dir, vae_dir, *, device="auto", memory_budget_gib=24, backend="torch",
         generation_config=None, verify_hashes=True, vae_core_frames=None, quantization="none",
         offload_ar=False, progress=True)
```

Behaviours that matter to the worker:

- **No explicit `device` parameter on `from_pretrained`.** It goes through `**kwargs`. `"auto"` picks cuda, then mps, then cpu (`:134-135`).
- **Process-global torch settings.** On construction it sets deterministic cuDNN, TF32 off and highest matmul precision (`:139-144`).
- **GPU memory cap.** On CUDA it caps the process with `set_per_process_memory_fraction(min(budget-2GiB, total-2GiB)/total)` (`:158-165`).
- **BF16 is required on CUDA.** Otherwise it raises `RuntimeError("The unquantized preset requires CUDA BF16 support")` (`:159-160`).
- **Full weight hash on every construction.** It SHA-256s every `*.safetensors` file (`storage.py:105-122`, about 7.8 GB) and checks it against `weights_manifest.json` when `verify_hashes=True`. A mismatch raises `ValueError`. This lengthens warm-up on each worker start.
- **Lazy weight loading.** Weights are not loaded into the GPU at construction. The LM loads as BF16 on first use (`_load_model`, `:210-223`). The VAE runs in FP32 and loads at first `decode`.
- **Progress goes to stderr** (`progress.py:72-85`). The worker should pass `progress=False` and report its own stages.

### Request validation: `SongRequest` (`protocol.py:80-109`)

`__call__` and `plan` turn their keyword arguments into `SongRequest(style, lyrics, cot="full", seed=831001, abc=None, cfg_scale=None, id="song")`. Validation:

- **`cot`** must be in `{"off","melody","full"}`. Otherwise `ValueError`.
- **`style` and `lyrics`** must be `str`. Otherwise `TypeError`. `tags=` is an alias for `style`, and the two must not disagree (`pipeline.py:225-231`).
- **`seed`** must be an `int` in `[0, 2**63)`. If it is omitted the default is **831001**. **`seed=None` raises `ValueError`**, so the worker must always pass an int.
- **`id`** must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,179}`.
- **`abc`**, when given, must be non-empty and needs `cot` to be `melody` or `full`.
- **`cfg_scale`** may be `None` or a finite number in **`[0, 20]`**. `None` means the default guidance, which is 1.0, or 1.01 when `cot="off"` (`:104-106`).

Sampling defaults (`protocol.py:21-55`):

| Stage | temperature | top_p | top_k | repetition penalty | window | min_tokens | max_tokens |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ABC | 0.7 | 0.9 | 30 | 1.005 | 100 | 32 | 4096 |
| Semantic | 1.0 | 0.95 | 100 | 1.2 | 50 | 200 | 9000 |

Context is fixed at 24576. A request whose prefix plus `max_tokens` exceeds it raises `ValueError` ("no implicit truncation", `sampling.py:62-67`).

The prompt text is assembled verbatim as `f"{instruction}\n[Tags]\n{style}\n[Lyrics]\n{lyrics}\n"` (`protocol.py:108-109`).

### One call: `pipe(...)` (`pipeline.py:378-398`)

```python
pipe(style=None, lyrics=None, *, tags=None, abc_sampling=None, semantic_sampling=None,
     cancelled=None, on_token=None, **SongRequest_fields) -> SongResult
```

The body is exactly the staged sequence, with no hidden steps:

1. `plan()`
2. `generate_semantic()`
3. `synthesize()`
4. a cancellation check
5. `decode()`
6. `SongResult(audio, 48000, semantic, latents, config, self.weights, timing, request_id)`

The two hooks:

- **`cancelled`** is a zero-argument callable. It is polled before prefill and before every token (`sampling.py:68-69,105-107`), and again before the VAE stage (`pipeline.py:389-390`). It raises `InterruptedError`.
- **`on_token(phase, token)`** is called for each generated token. `phase` is `"abc"` or `"semantic"`.

### Staged calls

| Call | Signature | Returns |
| --- | --- | --- |
| `plan` (`pipeline.py:255-269`) | `plan(style=None, lyrics=None, *, tags=None, request=None, abc_sampling=None, cancelled=None, on_token=None, **SongRequest_fields)` | A `SymbolicPlan`. With `cot="off"` it returns an empty plan immediately. With `abc=` given it tokenizes that score instead of generating one |
| `generate_semantic` (`:271-283`) | `generate_semantic(plan, *, sampling=None, cancelled=None, on_token=None)` | A `SemanticResult`. `TypeError` if `plan` is not a `SymbolicPlan` |
| `synthesize` (`:285-304`) | `synthesize(semantic, *, cancelled=None)` | `np.ndarray`, float32, shape `[frames, 64]`, on CPU. There is one latent frame per semantic token (`nar.py:46`) |
| `decode` (`:320-356`) | `decode(latents, *, full=False, vae=None)` | `np.ndarray`, float32, shape `[samples, 2]` (stereo), clamped to [-1, 1]. The rate is 48 kHz (`modeling_vae.py:292`) but is **not returned**. Raises `ValueError` on non-finite audio |
| `close` (`:306-312`) | `close()` | `None`. Drops the model and VAE and empties the CUDA cache. `__enter__` and `__exit__` call it too |
| `effective_config` (`:358-376`) | `effective_config(request, abc_sampling=None, semantic_sampling=None)` | A `dict`. Public method |

The result types (`pipeline.py:19-118`) are plain dataclasses:

- **`SymbolicPlan`** has the fields `request: SongRequest`, `abc: str | None`, `abc_ids: list[int]`, `prefix: list[int]`, `timing: dict` and `truncated: bool`.
  - `save(dir)` writes `score.abc` (only when `abc` is set), `abc_tokens.npy`, `prefix.npy`, `plan.json` and `plan_manifest.json`, which holds the sha256 of each.
  - `SymbolicPlan.load(dir)` verifies those hashes. It raises `ValueError` if anything was edited, so a changed score must be passed as `abc=` instead.
- **`SemanticResult`** has the fields `plan`, `tokens: list[int]` (codec ids, offset removed), `timing: dict` and `truncated: bool`.
- **`SongResult`** has the fields `audio`, `sample_rate`, `semantic`, `latents`, `config`, `weights`, `timing` and `request_identity`. It also has:
  - the property `abc`;
  - the property **`truncated`, which returns `{"abc": bool, "semantic": bool}`**;
  - `save(path)`, which writes FLAC as PCM_24 or WAV as FLOAT. Any other suffix raises `ValueError`;
  - `save_artifacts(dir)`.

### Staged reconstruction (read in M4, `pipeline.py:378-398`, `storage.py:19-21`)

Re-read from the same wheel (sha256 checked) on 2026-11-07, again without running it. `__call__`
is, verbatim:

```python
request = self._request(style, lyrics, tags=tags, **kwargs)
config = self.effective_config(request, abc_sampling, semantic_sampling)
request_id = identity({"request": request.to_dict(), "config": config, "weights": self.weights})
start = time.perf_counter()
plan = self.plan(request=request, abc_sampling=abc_sampling, cancelled=cancelled, on_token=on_token)
semantic = self.generate_semantic(plan, sampling=semantic_sampling, cancelled=cancelled, on_token=on_token)
nar_start = time.perf_counter()
latents = self.synthesize(semantic, cancelled=cancelled)
nar_seconds = time.perf_counter() - nar_start
if cancelled is not None and cancelled():
    raise InterruptedError("Cancelled before VAE")
vae_start = time.perf_counter()
audio = self.decode(latents)
timing = {"abc": plan.timing, "semantic": semantic.timing, "nar_seconds": nar_seconds,
          "vae_seconds": time.perf_counter() - vae_start, "load": dict(self.load_timing),
          "e2e_seconds": time.perf_counter() - start}
Progress(enabled=self.progress).complete(...)
return SongResult(audio, 48000, semantic, latents, config, self.weights, timing, request_id)
```

- `identity(value)` is `sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
  separators=(",", ":"), allow_nan=False).encode()).hexdigest()`. Import it as
  `from yue2.storage import identity`; `storage.py` imports only the standard library.
- `self.weights` is set in `__init__` (`:152-154`) as `{"mot": model_identity(...), "vae":
  model_identity(...)}`. `self.load_timing` is a public dict attribute.
- `SongResult` fields, in order: `audio, sample_rate, semantic, latents, config, weights, timing,
  request_identity`. `SymbolicPlan.request` is the `SongRequest`, which has `to_dict()`.
- `plan(style=..., lyrics=..., cot=..., seed=..., cfg_scale=...)` builds the `SongRequest` itself
  (`request = request or self._request(...)`), so `plan.request` carries `cfg_scale` into
  `generate_semantic` (`:274-282`). The ABC phase uses no CFG.
- `from_pretrained`'s VAE default is the literal `"m-a-p/YuE2-Vae"` (`:168`).

**Decision (M4): staged.** `Yue2Engine` calls the four stages and then builds the `SongResult`
exactly as above. It uses only public names (`effective_config`, `weights`, `load_timing`,
`SongRequest.to_dict`, `SongResult`, `yue2.storage.identity`). The artifact set is the same as
`pipe(...)`'s. `generate()` (single call) is also implemented, but is not used while
`supports_staged` is true.

### Saving: `SongResult.save_artifacts(dir)` (`pipeline.py:103-118`)

It writes these files into `dir`:

| File | Content |
| --- | --- |
| `score.abc` | The ABC score. Only present when cot is `full` or `melody` |
| `abc_tokens.npy`, `prefix.npy` | int32 token arrays |
| `plan.json` | Request, timing, truncated flag, prefix, ABC ids and ABC text |
| `plan_manifest.json` | sha256 of each plan file |
| `audio.flac` | 48 kHz stereo, PCM_24 |
| `semantic.npy` | int32 semantic tokens |
| `latent.npy` | float32 `[frames, 64]` |
| `request.json` | The `SongRequest`, including the seed actually used |
| `config.json` | `effective_config`, including `runtime_sha256` of the package source and `"validation_status": "unvalidated"` |
| `result.json` | Summary record, described below |

`result.json` has these fields:

- `status: "complete"`;
- `identity`, the sha256 of request, config and weights;
- `truncated: {abc, semantic}`;
- `sample_rate`;
- `audio_seconds`;
- `weights`, with per-file sha256 and bytes, plus `config_sha256`;
- `timing`, with `abc`, `semantic`, `nar_seconds`, `vae_seconds`, `load` and `e2e_seconds`;
- `artifacts`, with sha256 and bytes for every other file.

`yue2.storage.verify_result(dir, expected_identity=None)` (`storage.py:131-148`) re-checks a saved set.

## 4. Errors

The package defines **no exception classes of its own**. What can reach the worker:

| Raised | Where | Suggested job error code |
| --- | --- | --- |
| `torch.cuda.OutOfMemoryError` (a `RuntimeError` subclass, from torch) | Anywhere on the GPU. The package does not catch it | `oom` |
| `InterruptedError` | When the `cancelled()` callback returns true | none: the job ends `canceled` |
| `ValueError`, `TypeError` | Request validation, context overflow, non-finite audio, weight integrity | `engine_error` |
| `RuntimeError` | No BF16 support | `engine_error` at load, which makes the model state `failed` |
| `FloatingPointError` | Non-finite latents (`nar.py:197`) | `engine_error` |
| `FileNotFoundError` | Missing weights or local path (`storage.py:48,111`) | `engine_error` at load |

The OOM class name comes from torch, not from this wheel. M4 should catch it by type and also by message (`"out of memory"`) to be safe.

## 5. Concurrency and memory

- **Not concurrent.**
  - The CLI rejects `--concurrency != 1`: "do not share a pipeline concurrently" (`cli.py:145-146`).
  - `fast.py:6` says "the public pipeline currently submits one request at a time".
  - `nar.py:207` says "this model cannot serve concurrently".
  - The model card says "One song at a time".
  - **Conclusion: one executor thread, one pipeline per process.**
- **Process-global state.** The pipeline sets torch backend flags and the CUDA memory fraction for the whole process (`pipeline.py:139-165`).
- **Reuse across requests is the intended pattern.**
  - `yue2 batch` builds one pipeline and loops over many songs (`cli.py:152-160`).
  - Per request, KV caches and CUDA graphs are created and freed inside `generate_tokens` (`sampling.py:151-154`).
  - `decode` moves the LM to CPU, empties the cache and moves the VAE back to CPU afterwards (`pipeline.py:322-356`). The next request moves the LM back to the GPU (`_load_model`).
  - The VAE stays cached in host RAM.
  - Whether memory grows over many jobs **cannot be verified without a GPU**. That is for the smoke script in M6.

## 6. Open questions (PRD §14)

| ID | Status | Finding |
| --- | --- | --- |
| OQ-1 Lyrics syntax | Partly answered | The package does **no** tag parsing: lyrics are inserted verbatim (`protocol.py:109`). There are no tag lists, case rules or line limits in code; only the token budget applies. The official example `examples/tonight-awake.json` in the HF repo uses `[Intro] [Verse] [Pre-Chorus] [Chorus] [Interlude] [Bridge] [Outro]`, with an empty line between sections. The model card lists the languages `zh` and `en`. The PRD default stands: accept any text |
| OQ-2 Staged return types; same artifacts? | Answered | Types are in §3. There is no public "save staged results" helper. However, `__call__` is itself only the staged sequence plus `SongResult(...)` (`pipeline.py:378-398`). The worker can call the four stages and then build `SongResult(audio, 48000, semantic, latents, pipe.effective_config(req), pipe.weights, timing, identity(...))`, exactly as `__call__` does, and then call `save_artifacts`. The artifact set is identical. The only non-exported helper used is `yue2.storage.identity` (`storage.py:19-21`, `cli.py:115-117`). So the staged path is viable. The decision is in M4 **M4: staged path chosen**; the exact reconstruction is in §3, "Staged reconstruction" |
| OQ-3 Ranges; `truncated` | Answered | `cfg_scale` must be in [0, 20]; the engine rejects anything else. `seed` must be in [0, 2^63). **Song length (derived, not measured):** at 25 latent frames/s (48000/1920) and one frame per semantic token, the semantic token bounds of 200 to 9000 give about **8 to 360 s**. The model card's 214.85 s song is about 5370 tokens. **Lyrics length:** the semantic prefix (instruction, style, lyrics and up to 4096 ABC tokens) must fit within 24576 − 9000 = 15576 tokens, or the call raises `ValueError`. With `cot` full or melody, style plus lyrics must therefore stay under about 11.4k tokens. English at 8000 characters is well under that. Whether 8000 characters of CJK text fits is **not verified**; tokenization runs on CPU, so M4 can measure it. **`truncated`:** `{"abc": bool, "semantic": bool}`. A flag is true when that stage hit `max_tokens` without emitting its end token (`sampling.py:150`). The song is still produced and `status` stays `"complete"`; nothing is raised |
| OQ-4 Sizes | Answered (weights); derived (jobs) | YuE2-3B `model.safetensors` is 7,261,441,640 B, `qwen.tiktoken` is 2,561,218 B, and the configs are small. YuE2-Vae `model.safetensors` is 530,512,720 B. **Download total: about 7.8 GB**, so the 50 GB volume default is ample. **Per job (derived):** `latent.npy` is about 6.4 KB/s of audio (about 1.4 MB for 3.6 min), `semantic.npy` about 100 B/s, plus the FLAC (24-bit stereo, a few tens of MB). Intermediates are far below the 100 MB threshold. M4 measures this **M4 measurement (CPU, `np.save` at the largest documented shapes):** `latent.npy` [9000, 64] float32 = 2,304,128 B, `semantic.npy` [9000] = 36,128 B, `abc_tokens.npy` [4096] = 16,512 B, `prefix.npy` [15576] = 62,432 B, so about 2.4 MB of intermediates per job at most. `STUDIO_DOWNLOAD_INTERMEDIATES` stays `true`. Real FLAC sizes are not measured (no GPU) |
| OQ-5 Reuse / thread safety | Partly answered | Not thread-safe; use one executor thread (§5). Reuse across jobs is the package's own batch pattern. Memory growth is unverifiable without a GPU |
| OQ-6 CUDA / BF16 | Partly answered | The wheel pins `torch==2.10.0` with no CUDA variant. The default PyPI build for Linux x86_64 depends on `nvidia-*-cu12` 12.8.x (CUDA 12.8 runtime), cuDNN 9.10.2.21 and triton 3.6.0 (PyPI metadata, read 2026-11-07). BF16 is required on CUDA (`pipeline.py:159`). The minimum host driver for CUDA 12.8 is not determined here (M4/M6). The model card asks for a 24 GB GPU and 24 GB host RAM. It reports 11.18 GiB peak VRAM on an RTX 4090 (71 s for 214.85 s of audio) and 14.08 GiB at maximum context **M4:** torch 2.10.0's PyPI build depends on `nvidia-cuda-runtime-cu12==12.8.90`, so the image uses `nvidia/cuda:12.8.1-runtime-ubuntu24.04`. NVIDIA's minimum Linux driver is 570.26 for 12.8 GA and 570.124.06 for 12.8.1. Minor-version compatibility allows 525–579 drivers, with caveats; untested. Whether RunPod hosts actually work is unverified (docs/runpod.md) |
| OQ-7 RunPod stop/terminate, proxy URL | Not answered | Outside the wheel and model card. The owner verifies on RunPod |
| OQ-8 Licences | Partly answered | The weights for YuE2-3B, YuE2-Vae and YuE2-Vae-legacy are **CC BY-NC 4.0** (the wheel's `LICENSE` and the HF repo cards). Third-party code in `modeling_vae.py` is MIT (stable-audio-tools, NVIDIA SnakeBeta). The `yue2` package code itself carries **no stated licence**: its `LICENSE` says it covers the weights only, and METADATA has no License field. The repo licence and the container registry remain the owner's choice (default: no licence file, GHCR) |

## 7. Implications for M1 and M4

These are notes, not decisions.

- **Always resolve the seed in the worker** and pass it as an `int`. Record it via `ctx.resolved(seed=...)`.
- **When `cfg_scale` is absent**, omit it or pass `None`; both mean the model default.
- **Out-of-range `cfg_scale`.** The PRD contract only says "> 0", but the engine rejects values above 20. Unless the contract adds `le=20`, such requests pass validation and then fail as `engine_error`. This is raised with the owner.
- **Load with `progress=False`.** Pin `revision`/`vae_revision` to the repo shas in §1.
- **Cancellation.** `cancelled=ctx.cancel_requested` gives cancellation *within* a stage, not only between stages.
- **Artifact roles** (suggested; decide in M4):
  - `audio.flac` → `primary_audio`
  - `score.abc` → `score`
  - `plan.json` → `plan`
  - `result.json` → `result`
  - `semantic.npy`, `latent.npy`, `abc_tokens.npy`, `prefix.npy` → `intermediate`
  - `request.json`, `config.json`, `plan_manifest.json` → `other`
- **Warm-up.** `verify_hashes=True` re-hashes about 7.8 GB on each start. Keep it, since it is the integrity record, and accept a longer warm-up; or decide otherwise in M4.
- **Worker image.** Install the wheel from the file whose sha256 is in §1. Do not add it to the uv workspace lock, because it pulls CUDA torch.
