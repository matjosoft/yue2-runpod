"""Export the worker and server OpenAPI documents as deterministic JSON.

Used by scripts/gen_api_types.sh (``make contracts``). It lives in scripts/ rather than
in contracts/ so that the contracts package never imports the apps (docs/decisions.md).
"""

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from studio_server.main import create_app as create_server_app
from studio_worker.config import WorkerSettings
from studio_worker.engines.fake import FakeEngine
from studio_worker.main import create_app as create_worker_app

ROOT = Path(__file__).resolve().parent.parent


def worker_app() -> FastAPI:
    # The schema does not depend on settings or engine. Explicit values keep the export
    # free of environment variables; the lifespan never runs, so nothing touches disk.
    settings = WorkerSettings(api_key="openapi-export-" + "x" * 32, engine="fake")
    return create_worker_app(settings, FakeEngine())


APPS: dict[str, Callable[[], FastAPI]] = {
    "worker": worker_app,
    "server": create_server_app,
}


def render(app: FastAPI) -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "contracts" / "openapi")
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    for name, factory in APPS.items():
        (out / f"{name}.json").write_text(render(factory()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
