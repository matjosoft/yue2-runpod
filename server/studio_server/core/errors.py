"""The error envelope on every non-2xx response of the local server (PRD section 6).

Modelled on worker/studio_worker/errors.py (the server must not import the worker).
Codes are the worker's ``ErrorCode`` plus the server's ``ClientErrorCode``.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from studio_contracts import ClientErrorCode, ErrorCode, ServerErrorEnvelope, ServerErrorInfo

log = logging.getLogger("studio_server.errors")

Code = ErrorCode | ClientErrorCode

HTTP_STATUS: dict[Code, int] = {
    ErrorCode.unauthorized: 502,  # the *worker* rejected the token; the UI is not at fault
    ErrorCode.not_found: 404,
    ErrorCode.conflict: 409,
    ErrorCode.validation_error: 422,
    ErrorCode.queue_full: 429,
    ErrorCode.not_ready: 503,
    ErrorCode.internal: 500,
    ClientErrorCode.not_configured: 409,
    ClientErrorCode.worker_unreachable: 502,
    ClientErrorCode.incompatible_worker: 502,
    ClientErrorCode.download_failed: 502,
}
"""HTTP status when the server reports a code itself (docs/decisions.md, M2)."""

RETRYABLE: dict[Code, bool] = {
    ErrorCode.queue_full: True,
    ErrorCode.not_ready: True,
    ErrorCode.worker_restarted: True,
    ErrorCode.oom: True,
    ClientErrorCode.worker_unreachable: True,
    ClientErrorCode.download_failed: True,
}
"""Default ``retryable`` per code; the worker's defaults plus the client codes."""


def error_info(
    code: Code,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
    retryable: bool | None = None,
) -> ServerErrorInfo:
    return ServerErrorInfo(
        code=code,
        message=message,
        retryable=RETRYABLE.get(code, False) if retryable is None else retryable,
        details=dict(details or {}),
    )


class ApiError(Exception):
    """Raised by routes and dependencies; rendered as the error envelope."""

    def __init__(
        self,
        code: Code,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.info = error_info(code, message, details=details, retryable=retryable)
        self.status_code = status_code or HTTP_STATUS.get(code, 502)
        self.headers = dict(headers or {})

    @classmethod
    def from_info(cls, info: ServerErrorInfo, status_code: int) -> "ApiError":
        return cls(
            info.code,
            info.message,
            details=info.details,
            retryable=info.retryable,
            status_code=status_code,
        )


def field_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """``details.fields`` for a 422: location and message only, never the input."""
    fields: list[dict[str, str]] = []
    for err in errors:
        loc = list(err.get("loc", ()))
        if loc and loc[0] == "body":
            loc = loc[1:]
        fields.append({"loc": ".".join(str(part) for part in loc), "msg": str(err["msg"])})
    return fields


def envelope_response(
    info: ServerErrorInfo, status_code: int, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    body = ServerErrorEnvelope(error=info).model_dump(mode="json")
    return JSONResponse(body, status_code=status_code, headers=dict(headers or {}))


def _code_for_status(status: int) -> Code:
    if status in (404, 405):
        return ErrorCode.not_found
    if status == 422:
        return ErrorCode.validation_error
    if status == 409:
        return ErrorCode.conflict
    return ErrorCode.internal


def install_error_handlers(app: FastAPI) -> None:
    async def on_api_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        return envelope_response(exc.info, exc.status_code, exc.headers)

    async def on_request_validation(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        info = error_info(
            ErrorCode.validation_error,
            "The request is invalid",
            details={"fields": field_errors(exc.errors())},
        )
        return envelope_response(info, 422)

    async def on_http_exception(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        code = _code_for_status(exc.status_code)
        message = "Not found" if code is ErrorCode.not_found else str(exc.detail)
        return envelope_response(error_info(code, message), exc.status_code, exc.headers)

    async def on_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"path": request.url.path})
        return envelope_response(error_info(ErrorCode.internal, "Internal server error"), 500)

    app.add_exception_handler(ApiError, on_api_error)
    app.add_exception_handler(RequestValidationError, on_request_validation)
    app.add_exception_handler(StarletteHTTPException, on_http_exception)
    app.add_exception_handler(Exception, on_unexpected)


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ServerErrorEnvelope, "description": "Error envelope"}
    for status in sorted(set(HTTP_STATUS.values()) - {500})
}
"""OpenAPI ``responses`` for routes, so the generated client knows the envelope."""
