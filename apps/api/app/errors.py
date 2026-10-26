"""The one error envelope every endpoint returns.

    {"error": {"code": "not_found", "message": "...", "details": null,
               "request_id": "9f2c..."}}

The dashboard's typed client keys off `error.code`, so adding a new failure mode
means adding a code, not a new response shape.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
    504: "upstream_timeout",
}


class ApiError(Exception):
    """Raise this for expected failures; the handler renders the envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


class DependencyError(Exception):
    """A telemetry dependency failed in a way the caller should surface as 502/504."""

    def __init__(self, dependency: str, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.dependency = dependency
        self.timeout = timeout

    def as_api_error(self) -> ApiError:
        return ApiError(
            504 if self.timeout else 502,
            "upstream_timeout" if self.timeout else "upstream_error",
            f"{self.dependency}: {self}",
        )


def error_payload(
    code: str,
    message: str,
    request_id: str | None,
    details: Any | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _jsonable_details(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Starlette validation errors carry non-serialisable `ctx` values."""
    cleaned: list[dict[str, Any]] = []
    for err in errors:
        cleaned.append(
            {
                "loc": [str(part) for part in err.get("loc", [])],
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
        )
    return cleaned


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("api_error", code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, _request_id(request), exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(DependencyError)
    async def handle_dependency_error(request: Request, exc: DependencyError) -> JSONResponse:
        log.warning("dependency_error", dependency=exc.dependency, detail=str(exc))
        api_error = exc.as_api_error()
        return JSONResponse(
            status_code=api_error.status_code,
            content=error_payload(
                api_error.code,
                api_error.message,
                _request_id(request),
                {"dependency": exc.dependency},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        detail = exc.detail if isinstance(exc.detail, str) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                code,
                detail or code.replace("_", " "),
                _request_id(request),
            ),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "validation_error",
                "Request validation failed",
                _request_id(request),
                _jsonable_details(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "internal_error",
                "Unexpected server error",
                _request_id(request),
            ),
        )
