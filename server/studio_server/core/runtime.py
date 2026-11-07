"""Everything a request handler or the poller needs, built once per app."""

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Request

from studio_server.config import ServerSettings
from studio_server.core.db import Database
from studio_server.core.poller import Poller
from studio_server.core.storage import Storage
from studio_server.core.worker import WorkerLink


@dataclass
class ServerRuntime:
    settings: ServerSettings
    db: Database
    storage: Storage
    link: WorkerLink
    poller: Poller

    @classmethod
    def create(
        cls, settings: ServerSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> "ServerRuntime":
        db = Database(settings.db_path)
        storage = Storage(settings.data_dir)
        link = WorkerLink(db, settings, transport=transport)
        poller = Poller(db, storage, link, settings)
        return cls(settings=settings, db=db, storage=storage, link=link, poller=poller)


def get_runtime(request: Request) -> ServerRuntime:
    runtime = request.app.state.runtime
    assert isinstance(runtime, ServerRuntime)
    return runtime


Runtime = Annotated[ServerRuntime, Depends(get_runtime)]
"""Route parameter type: ``rt: Runtime``."""
