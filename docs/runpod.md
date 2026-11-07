# Running the worker on RunPod

This runbook takes you from a clean checkout to a worker on a RunPod GPU pod that the local
Studio app can use. The worker is one Docker image with no weights in it. A RunPod network
volume keeps the downloaded weights (about 7.8 GB) and the job folders between pods.

> **Status (M4, 2026-11-07).** Nobody has run this image on a GPU yet. Everything below was
> written and checked without a GPU or a RunPod account, and without Docker on the agent's
> machine. Each command is marked:
>
> - **[run]**: the agent ran it and saw it work, in the way noted.
> - **[not run]**: it needs Docker, a registry, RunPod or a GPU. The owner verifies it in M6.
>
> RunPod and NVIDIA facts were read from their docs on 2026-11-07. Sources are at the end.

## 1. What you need

| Item | Requirement |
| --- | --- |
| GPU | NVIDIA, with BF16 support and at least 24 GB VRAM. The YuE2 authors benchmarked an RTX 4090 (24 GB): 71 s for a 3.6-minute song, 11.18 GiB peak VRAM, and 14.08 GiB at maximum context. Other cards are untested |
| Host RAM | At least 24 GB available (model card) |
| Host driver | **570.124.06 or newer.** The image uses CUDA 12.8.1, because torch 2.10.0 (pinned by the yue2 wheel) is built against the CUDA 12.8 runtime. In RunPod, select CUDA **12.8 or newer** (see step 4). Older 525–575 drivers *may* work through CUDA minor-version compatibility, but this is untested and some features need the newer driver |
| Network volume | At least **50 GB**, in a Secure Cloud region that has 24 GB GPUs. Weights are about 7.8 GB; the rest is room for job folders and later model revisions |
| Container registry | GHCR or Docker Hub, your choice. The image is large: CUDA runtime plus torch with its CUDA libraries, likely more than 10 GB |
| Local tools | Docker (to build), `openssl`, and this repo set up with `make setup` |

## 2. Build and push the image

Run these from the repo root.

```bash
make build-worker                                   # [not run] builds studio-worker:<version>, target yue2
IMAGE=ghcr.io/<you>/studio-worker make build-worker # [not run] same, with your registry name
docker push ghcr.io/<you>/studio-worker:0.1.0       # [not run]
```

- `make build-worker` was run by the agent. It stops at `docker build` because Docker is not
  available on that machine, so the build itself has not been run.
- `TARGET=fake make build-worker` builds `studio-worker:<version>-fake` instead. That variant
  has no CUDA and no YuE2 and runs only the fake engine.
- `YUE2_WHEEL_VERSION=<v> YUE2_WHEEL_SHA256=<sha> make build-worker` overrides the pinned
  wheel. Always change both values together.

What the `yue2` target does (`worker/Dockerfile`):

1. Starts from `nvidia/cuda:12.8.1-runtime-ubuntu24.04`, which provides Python 3.12.
2. Installs `huggingface-hub==0.36.2`.
3. Downloads `yue2_infer-0.1.5-py3-none-any.whl` from `m-a-p/YuE2-3B` at a pinned repo
   revision, and checks its sha256. The build fails on a mismatch.
4. Installs the wheel, the worker's locked requirements and the two local packages in one
   resolve, so any version conflict fails the build.
5. Sets `ENGINE=yue2`, `HF_HOME=/workspace/hf`, `JOBS_DIR=/workspace/jobs` and `PORT=8000`,
   and starts `uvicorn studio_worker.main:app --host 0.0.0.0 --port $PORT`.

Checked without Docker:

- **[run]** The requirement export step works.
- **[run]** The full install set resolves for Linux x86_64 and Python 3.12: torch 2.10.0,
  CUDA runtime 12.8.90, transformers 4.57.6, numpy 2.2.6 and soundfile 0.13.1, with no
  conflicts.
- **[run]** Every YuE2 call and keyword the engine uses exists in the wheel's source.
- **[run]** The image's install step was reproduced outside the repo, with uv 0.13.5 (the
  image's version), against a copy laid out as in the image (`/src/contracts` and
  `/src/worker`, without tests). Without `--no-sources` it fails, because the packages point
  at the repo's uv workspace; the Dockerfile therefore passes `--no-sources`. With it, the
  `fake` set installs and the `yue2` set (wheel included) resolves.
- **[run]** The worker installed that way was started with the image's command and passed
  the smoke script.
- **[run]** With `ENGINE=yue2` and no `yue2` package installed, the worker still starts:
  `/healthz` answers, the model state becomes `error` and submits get 503.

## 3. Create the network volume

1. In the RunPod console, open **Storage** and create a network volume of at least 50 GB in
   a region that offers your GPU. **[not run]**
2. Network volumes are Secure Cloud only. A network volume can be attached only when a pod
   is deployed, and only to one running pod at a time.

## 4. Create a template and deploy a pod

1. Make a token. **[run]**

   ```bash
   openssl rand -hex 32
   ```

2. Create a pod template. **[not run]**
   - Container image: your pushed image.
   - Expose HTTP port **8000**. No SSH or TCP ports are needed.
   - Volume mount path: `/workspace`. The network volume replaces the pod volume there.
   - Container disk: the default is enough. It is wiped on every restart; nothing important
     lives there.
   - Environment variables: see the table below. Only `WORKER_API_KEY` is required. Enter it
     as a secret, not as plain text, if your account supports that.
3. Deploy a pod from the template on a 24 GB GPU (RTX 4090 or larger), attaching the network
   volume. Under **Additional filters → CUDA Versions**, select 12.8 and newer only.
   **[not run]**
4. Note the pod's HTTP proxy URL: `https://<pod-id>-8000.proxy.runpod.net`. A new pod gets a
   new id and therefore a new URL. **[not run]**

### Environment variables

| Name | Default | Purpose |
| --- | --- | --- |
| `WORKER_API_KEY` | required | Bearer token, at least 32 characters. The worker refuses to start without it. Never logged |
| `ENGINE` | `yue2` | `yue2` or `fake` |
| `YUE2_MODEL_REPO` | `m-a-p/YuE2-3B` | Model repo |
| `YUE2_VAE_REPO` | package default (`m-a-p/YuE2-Vae`) | Decoder repo. `m-a-p/YuE2-Vae-legacy` is the authors' benchmark decoder |
| `YUE2_MODEL_REVISION` | unset | Pin the model repo to a commit for reproducible output. Shown in `/v1/capabilities` as `repo@revision`. The weight hashes are always recorded in each job's `result.json` |
| `YUE2_VAE_REVISION` | unset | Same, for the decoder |
| `HF_HOME` | `/workspace/hf` | Weight cache, on the network volume. The weights go to `$HF_HOME/hub` |
| `HF_TOKEN` | unset | Only if the repos ever require a login. They do not today |
| `JOBS_DIR` | `/workspace/jobs` | Job folders |
| `MAX_QUEUE` | `4` | Queue cap. Extra submits get 429 `queue_full` |
| `JOB_TTL_HOURS` | `24` | How long finished job files are kept |
| `LOG_LEVEL` | `INFO` | JSON logs. Lyrics and style are never logged at INFO |
| `PORT` | `8000` | HTTP port. If you change it, change the exposed port too |
| `FAKE_LOAD_DELAY_S`, `FAKE_STAGE_DELAY_S` | `0` | `ENGINE=fake` only: simulated warm-up and stage time |

## 5. First start: what to expect

1. `/healthz` answers at once with `{"ok": true}`.
2. The model loads in the background, and `/v1/status` shows `model.state` = `loading`.
   - **First start:** the weights download to the volume (about 7.8 GB, so a few minutes).
   - **Every start:** the package then re-hashes all weight files (about 7.8 GB read from
     the network volume) to check them against the repo's manifest. This is deliberate,
     since it is the integrity record, so warm-up stays slow even when the weights are
     cached. The model weights move to the GPU on the first job, not at start.
   - Submits get 503 `not_ready` with `Retry-After` until loading finishes.
3. `model.state` becomes `ready`. If loading fails (for example no BF16 support, a hash
   mismatch, or no space), the state is `error` with a short message, and submits keep
   returning 503.
4. `/v1/status` also shows the GPU name and VRAM use, read from `nvidia-smi`. Expect
   `gpu: null` if `nvidia-smi` is not available in the container.

## 6. Connect the local app

1. Start the local app with `make dev`, or run the server alone.
2. Open **Settings** and paste the proxy URL and the token. The status pill shows "warming
   up" until the model is ready, then "ready". **[not run against RunPod]**

## 7. Run the smoke test

From your machine, in the repo:

```bash
uv run --locked python scripts/smoke_worker.py --url https://<pod-id>-8000.proxy.runpod.net --token <token>
```

- **[not run against RunPod]** The agent ran the same command against a local fake worker,
  adding `--min-audio-seconds 0.5` because the fake song is 1 s long. It passed.

What the script does:

1. Waits up to `--timeout` seconds (default 900) for the model to be ready.
2. Checks `/v1/capabilities`.
3. Submits a short two-section song with seed 12345.
4. Prints each stage as it happens.
5. Waits up to `--timeout` again for the job.
6. Downloads every artifact and checks its size and sha256.
7. Checks that `audio.flac` is 48 kHz stereo and longer than 10 s, and that `score.abc` and
   `result.json` are present.
8. Prints the timings, total artifact size and the `truncated` flags.

It exits non-zero at the first failure, with a one-line reason. It never prints the token.

**A note on the proxy.** RunPod's HTTP proxy closes any request that takes longer than
100 s (error 524). The worker API is built for this: every call is short, and progress comes
from polling. Artifact downloads are the only long calls. A song's FLAC is tens of MB, which
should download well within the limit on a normal connection, but this has not been measured.

## 8. Stop, terminate and costs

- **An idle pod still bills for its GPU.** Stop or terminate the pod when you are done.
  `idle_seconds` in `/v1/status` shows how long the worker has been idle.
- **Stop** releases the GPU and keeps the pod. **Terminate** deletes the pod and everything
  not on a network volume.
- **The network volume survives both.** A new pod can attach it and start without
  re-downloading the weights. It will still re-hash them; see section 5.
- **Whether a pod with a network volume can be stopped, or only terminated, has changed over
  time.** RunPod's docs said terminate-only until 2026-05. Check the console. Terminating is
  always safe here, because the weights and job folders live on the volume.
- **Costs:**
  - Network volumes bill continuously: about $0.07/GB/month under 1 TB, so roughly
    $3.50/month for 50 GB.
  - Delete the volume when you no longer need it. The next start then downloads the weights
    again.
- **Set a spend alert.** In the console, go to **Billing → Notifications** and set a
  low-balance alert. Also consider limiting auto-pay.
  - When the balance reaches zero, pods with a network volume are stopped and their data is
    kept.
  - If the volume's own charges stay unpaid, the volume can eventually be deleted.

## 9. Troubleshooting by error code

| What you see | Meaning and what to do |
| --- | --- |
| Pill stays "warming up"; `model.state` = `loading` for a long time | First download, or the weight re-hash. Watch the pod logs. Ten minutes or more on a first start is plausible |
| `model.state` = `error`, message mentions BF16 | The GPU has no BF16 support. Use a newer card (RTX 30xx/40xx, A-series, H-series) |
| `model.state` = `error`, `ValueError` about hashes or shards | The cached weights are incomplete or corrupt. Delete `$HF_HOME/hub/models--m-a-p--YuE2-3B` (or the `-Vae` folder) on the volume and restart |
| `model.state` = `error`, CUDA or driver message | The host driver is too old for CUDA 12.8. Redeploy with the CUDA 12.8+ filter |
| `unauthorized` (401) | The token in Settings is not the pod's `WORKER_API_KEY` |
| `not_ready` (503) | The model is still loading or failed to load. See the status message |
| `queue_full` (429) | `MAX_QUEUE` jobs are already waiting. Retry later |
| Job `oom` | The GPU ran out of memory. Use a 24 GB card, and make sure nothing else is using the GPU. Very long lyrics use more memory |
| Job `engine_error` | Any other model error. `details` has the exception type and message. A common cause is a prompt too long for the context (a `ValueError`): shorten the lyrics |
| Job `worker_restarted` | The worker restarted while the job ran (pod restart or crash). Submit again |
| `truncated` flags `true` | The stage hit its token limit before finishing (song or score cut short). The audio is still produced |
| Local app shows `worker_unreachable` | Wrong URL, the pod is stopped, or the pod got a new URL. Update Settings |
| 524 from the proxy | A request took longer than 100 s. Retry the download. Report it if it repeats |

## Sources (read 2026-11-07)

- CUDA 12.8 driver minimums and minor-version compatibility:
  https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html and
  https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html
- torch 2.10.0's CUDA dependencies: https://pypi.org/pypi/torch/2.10.0/json
- Pod stop and terminate, CUDA filter: https://docs.runpod.io/pods/manage-pods
  - Network-volume pods becoming stoppable: https://github.com/runpod/docs/pull/629
- HTTP proxy URL and the 100 s limit: https://docs.runpod.io/pods/configuration/expose-ports
- Network volumes: https://docs.runpod.io/storage/network-volumes
- Billing, alerts and zero-balance behaviour: https://docs.runpod.io/accounts-billing/billing
- GPU, RAM and benchmark figures: the `m-a-p/YuE2-3B` model card, revision
  `8f24312f187763b9854adeec87e437e2b03bbff1`
