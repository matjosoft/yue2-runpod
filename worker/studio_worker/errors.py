"""The error envelope on every non-2xx response (PRD section 6, "Errors"; W-8)."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from studio_contracts import ErrorCode, ErrorEnvelope, ErrorInfo
from studio_worker.jobs.errors import error_info

log = logging.getLogger("studio_worker.errors")

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.unauthorized: 401,
    ErrorCode.not_found: 404,
    ErrorCode.conflict: 409,
    ErrorCode.validation_error: 422,
    ErrorCode.queue_full: 429,
    ErrorCode.not_ready: 503,
    ErrorCode.internal: 500,
}

RETRY_AFTER_S = 10
"""``Retry-After`` on 503 ``not_ready``."""


class ApiError(Exception):
    """Raised by routes and dependencies; rendered as the error envelope."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.info = error_info(code, message, details=details, retryable=retryable)
        self.status_code = status_code or HTTP_STATUS[code]
        self.headers = dict(headers or {})
        if code is ErrorCode.not_ready:
            self.headers.setdefault("Retry-After", str(RETRY_AFTER_S))


def field_errors(
    errors: Sequence[Mapping[str, Any]], *, prefix: Sequence[str | int] = ()
) -> list[dict[str, str]]:
    """``details.fields`` for a 422: location and message only. The rejected input is
    never echoed back, because it may be a whole lyrics text."""
    fields: list[dict[str, str]] = []
    for err in errors:
        loc = [*prefix, *err.get("loc", ())]
        if loc and loc[0] == "body":
            loc = loc[1:]
        fields.append({"loc": ".".join(str(part) for part in loc), "msg": str(err["msg"])})
    return fields


def validation_error(
    message: str, err: ValidationError, *, prefix: Sequence[str | int] = ()
) -> ApiError:
    return ApiError(
        ErrorCode.validation_error,
        message,
        details={"fields": field_errors(err.errors(), prefix=prefix)},
    )


def envelope_response(
    info: ErrorInfo, status_code: int, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    body = ErrorEnvelope(error=info).model_dump(mode="json")
    return JSONResponse(body, status_code=status_code, headers=dict(headers or {}))


def _code_for_status(status: int) -> ErrorCode:
    if status == 405:
        return ErrorCode.not_found  # no such method on this path (docs/decisions.md)
    for code, code_status in HTTP_STATUS.items():
        if code_status == status:
            return code
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
        return envelope_response(error_info(ErrorCode.internal, "Internal worker error"), 500)

    app.add_exception_handler(ApiError, on_api_error)
    app.add_exception_handler(RequestValidationError, on_request_validation)
    app.add_exception_handler(StarletteHTTPException, on_http_exception)
    app.add_exception_handler(Exception, on_unexpected)


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope, "description": code.value}
    for code, status in HTTP_STATUS.items()
    if code is not ErrorCode.internal
}
"""OpenAPI ``responses`` for routes, so the generated client knows the envelope."""
