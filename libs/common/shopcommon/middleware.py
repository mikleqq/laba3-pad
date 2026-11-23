"""Request context middleware: request id, access logging, last-resort error handler."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .errors import error_body
from .logging import log_extra, request_id_var, service_var

logger = logging.getLogger("shopcommon.http")

REQUEST_ID_HEADER = "X-Request-ID"
_QUIET_PATHS = {"/health", "/ready"}


def _is_dependency_failure(exc: BaseException) -> bool:
    """Network-level DB errors (host down, connection refused) are raised by the driver
    as OSError / asyncpg errors, not as SQLAlchemy exceptions."""
    seen = 0
    while exc is not None and seen < 5:
        if isinstance(exc, OSError) or type(exc).__module__.startswith("asyncpg"):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, service_name: str) -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        rid_token = request_id_var.set(request_id)
        svc_token = service_var.set(self.service_name)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:  # noqa: BLE001 - last line of defence, never leak a stack trace
                if _is_dependency_failure(exc):
                    logger.error("dependency unavailable", extra=log_extra(error=f"{type(exc).__name__}: {exc}"))
                    response = JSONResponse(
                        status_code=503, content=error_body("database_error", "Database is temporarily unavailable")
                    )
                else:
                    logger.exception("unhandled error")
                    response = JSONResponse(status_code=500, content=error_body("internal_error", "Internal server error"))
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id
            if request.url.path not in _QUIET_PATHS:
                logger.info(
                    "request completed",
                    extra=log_extra(
                        method=request.method,
                        path=request.url.path,
                        status=response.status_code,
                        duration_ms=duration_ms,
                    ),
                )
            return response
        finally:
            request_id_var.reset(rid_token)
            service_var.reset(svc_token)
