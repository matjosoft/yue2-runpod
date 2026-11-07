"""Bearer-token auth for everything under ``/v1`` (W-6). ``/healthz`` is open."""

import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from studio_contracts import ErrorCode
from studio_worker.errors import ApiError

_bearer = HTTPBearer(auto_error=False, description="WORKER_API_KEY")


def tokens_match(given: str, expected: str) -> bool:
    """Constant-time comparison. Encoded first, because ``compare_digest`` rejects
    non-ASCII ``str``."""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


async def require_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected: str = request.app.state.runtime.settings.api_key
    if credentials is None or not tokens_match(credentials.credentials, expected):
        raise ApiError(
            ErrorCode.unauthorized,
            "Missing or wrong bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
