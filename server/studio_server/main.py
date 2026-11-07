"""Local server application factory.

``make dev`` runs ``uvicorn studio_server.main:create_app --factory``, which reads
settings from the environment and ``.env``. Tests pass explicit settings and route the
backend to an in-process worker app with ``transport``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from studio_server import __version__
from studio_server.config import ServerSettings
from studio_server.core.errors import install_error_handlers
from studio_server.core.logs import configure_logging, install_request_logging
from studio_server.core.runtime import ServerRuntime
from studio_server.core.webui import install_web_ui
from studio_server.features import FEATURES

log = logging.getLogger("studio_server")


def create_app(
    settings: ServerSettings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    start_poller: bool = True,
) -> FastAPI:
    if settings is None:
        settings = ServerSettings.from_env()
        configure_logging(settings.log_level)
    runtime = ServerRuntime.create(settings, transport=transport)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime.db.migrate()
        runtime.storage.clean_partials()
        for feature in FEATURES:
            if feature.on_startup is not None:
                await feature.on_startup(runtime)
        if start_poller:
            runtime.poller.start()
        log.info(
            "server started", extra={"version": __version__, "data_dir": str(settings.data_dir)}
        )
        try:
            yield
        finally:
            await runtime.poller.stop()
            await runtime.link.aclose()
            runtime.db.close()

    app = FastAPI(title="Studio server", version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    install_error_handlers(app)
    install_request_logging(app)
    for feature in FEATURES:
        app.include_router(feature.router)
    install_web_ui(app, settings.web_dir)  # last: its catch-all must not shadow /api
    return app
