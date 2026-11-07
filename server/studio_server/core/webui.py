"""Serve the built web UI (PRD section 9: "served by the local server in normal use").

``GET /<path>`` returns the file from the build folder if it exists there, otherwise
``index.html``, so client-side routes such as ``/library/gen_...`` survive a reload. Paths
under ``/api`` are never answered with the UI: an unknown API path keeps its JSON 404.
The route is left out of the OpenAPI document, so the API contract does not change.
During development Vite serves the UI instead (``make dev``).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from studio_contracts.errors import ErrorCode
from studio_server.core.errors import ApiError


def install_web_ui(app: FastAPI, web_dir: Path | None) -> None:
    if web_dir is None:
        return
    root = web_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def web_ui(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        index = root / "index.html"
        if not index.is_file():
            raise ApiError(
                ErrorCode.not_found,
                "The web UI is not built. Run `pnpm -C web build` (make run-fake does it).",
                status_code=404,
            )
        if path:
            candidate = (root / path).resolve()
            # Only files inside the build folder; anything else gets the app shell.
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
