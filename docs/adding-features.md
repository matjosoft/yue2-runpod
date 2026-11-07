# Adding features

How to extend Studio without touching its core. The extension points are fixed by PRD section 7; [architecture.md](architecture.md) explains how the parts fit together.

## The rules (PRD §7)

- Never branch on an operation name outside the worker registry and the web `operationViews` registry.
- Every generation stores its request and its artifact manifest. Never discard them.
- Change contracts first, regenerate types, then change code.
- Keep generic tables and job objects free of song-specific fields. A feature that needs its own data adds its own table in its own migration.
- Build only the abstractions the PRD lists. Anything extra needs a note in [decisions.md](decisions.md).
- Only `worker/studio_worker/engines/yue2.py` may import `yue2`. Nothing in `jobs/`, `operations/` or `engines/` may import FastAPI.
- Server features use `studio_server.core`, never each other. Shared logic moves into core.

## 1. Contracts first

If the change touches an API shape (a new endpoint, a new field, a new error code), edit the Pydantic models in `contracts/studio_contracts/` first, then regenerate:

```bash
make contracts   # contracts/openapi/{worker,server}.json and web/src/api/schema.d.ts
```

`make test` fails if the generated files are out of date. Changes must be additive: new optional fields, new endpoints, new operations. A new error code also needs a message in `web/src/components/errorMessages.ts`; `tsc` and `web/tests/errors.test.tsx` fail until it has one.

A new operation needs no contract change: its params schema travels in `/v1/capabilities`.

## 2. A worker operation

An operation is a class with a `name`, a `params_model`, `stages()` and `run()`. Register it with `@register_operation` and import its module from `worker/studio_worker/operations/__init__.py`. Routes, the job store, the executor and `/v1/capabilities` pick it up without changes.

The test-only `test.echo` operation shows the whole shape:

```python
from typing import ClassVar

from pydantic import BaseModel

from studio_worker.engines.base import Engine
from studio_worker.operations.base import OperationCanceled, OperationContext
from studio_worker.operations.registry import register_operation


class EchoParams(BaseModel):
    text: str


@register_operation
class Echo:
    name: ClassVar[str] = "test.echo"
    params_model: ClassVar[type[BaseModel]] = EchoParams

    def stages(self, engine: Engine) -> list[str]:
        return ["echo", "save"]

    def run(self, ctx: OperationContext, params: BaseModel) -> None:
        assert isinstance(params, EchoParams)
        ctx.stage("echo")
        if ctx.cancel_requested():
            raise OperationCanceled("save")
        ctx.stage("save")
        (ctx.job_dir / "echo.txt").write_text(params.text, encoding="utf-8")
        ctx.add_artifact("echo.txt", "result", "text/plain")
```

What the context gives you:

- `ctx.stage(name, progress=None)`: report the current stage. It must be one of `stages()`.
- `ctx.job_dir`: the job's `artifacts/` folder. Write files there and list each one with `ctx.add_artifact(relpath, role, media_type)`; size and sha256 are computed for you.
- `ctx.resolved(**values)`: report values the worker chose, such as the seed actually used.
- `ctx.cancel_requested()`: check it between stages and raise `OperationCanceled` to stop. The executor marks the job `canceled` and deletes what it wrote.
- Raise `EngineOOM` (from `engines/base.py`) for out-of-memory; any other exception becomes `engine_error`.

If the operation needs new model calls, put them behind a protocol in `engines/base.py` (as `SongEngine` does for `song.generate`) and implement it in `engines/fake.py` and `engines/yue2.py`. Tests use `FakeEngine` only; never require a GPU.

The proof that this works end to end is AT-10: `test.echo` is registered inside the tests only (with `registered(...)` from `operations/registry.py`), in `worker/tests/test_api_capabilities.py` and `server/tests/test_acceptance.py`.

## 3. A server feature module

Create `server/studio_server/features/<name>/__init__.py` that exports `feature = Feature(name=..., router=..., on_startup=...)`, and add it to `FEATURES` in `server/studio_server/features/__init__.py`. `main.py` mounts every router in that list.

- Use `Runtime` (from `core/runtime.py`) in route signatures to reach the database, storage, the worker link and settings.
- Raise `ApiError` (from `core/errors.py`) so errors use the envelope.
- Logic that more than one feature (or the poller) needs goes into `core/`.
- New tables come with a new Alembic migration in `server/studio_server/migrations/versions/`.

A new operation needs no server feature: `POST /api/generations` takes any operation the worker offers, and the poller is generic.

## 4. A web feature module and an operation view

A web feature is a `FeatureModule` (`web/src/features/types.ts`):

```ts
export const scoreFeature: FeatureModule = {
  id: "score",
  requires: ["song.plan"],               // hidden when the worker lacks it
  routes: [{ path: "score", element: <ScorePage /> }],
  nav: { label: "Score", to: "/score", order: 4 },
};
```

Add it to `FEATURES` in `web/src/features/index.ts`. The shell builds the routes and navigation from that list.

To show a new operation's results in the library and the result card, add an entry to `operationViews` in `web/src/components/operationViews.ts`:

```ts
export const operationViews: Record<string, OperationView> = {
  "song.generate": { Summary: SongSummary, Result: SongResult },
  "test.echo": { Summary: EchoSummary, Result: EchoResult },
};
```

Without an entry, a generic view lists the files. Status, stages, errors, Cancel, Retry and Delete are shared and need nothing.

## 5. A new backend

A backend is a class that implements `MusicBackend` (`server/studio_server/core/backend/base.py`) and raises `BackendError` for every failure. Register it in `BACKENDS` in `core/backend/factory.py` under a new `kind`. Nothing else in the server changes. If it holds connections, also implement `aclose()` (the `Closeable` protocol).

Test it against the same scenarios as `server/tests/test_backend.py`.

## Before you finish

```bash
make lint
make typecheck
make test
```
