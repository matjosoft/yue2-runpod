"""The ``Feature`` record every feature module exports (PRD section 7)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter

from studio_server.core.runtime import ServerRuntime


@dataclass(frozen=True)
class Feature:
    name: str
    router: APIRouter
    on_startup: Callable[[ServerRuntime], Awaitable[None]] | None = None
