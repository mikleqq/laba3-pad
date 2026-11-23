"""Unified error model and FastAPI exception handlers.

All errors are returned as::

    {"error": {"code": "not_found", "message": "...", "request_id": "...", "details": ...}}

Stack traces are never sent to the client; unexpected errors are logged and
returned as a generic ``internal_error``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import log_extra, request_id_var

logger = logging.getLogger("shopcommon.errors")


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    message: str = "Internal server error"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Any = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    message = "Bad request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication required"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Resource state conflict"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"
    message = "A dependent service failed"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "Service temporarily unavailable"


class UpstreamTimeoutError(AppError):
    status_code = 504
    code = "upstream_timeout"
    message = "A dependent service did not respond in time"


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details is not None:
        body["details"] = details
    return {"error": body}


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
}


def humanize_validation_error(err: dict) -> str:
    """Turn a pydantic error into a short Russian message for the customer."""
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    msg = str(err.get("msg", ""))
    field = str(err.get("loc", ("",))[-1])
    if kind == "missing":
        return "Обязательное поле"
    if kind == "value_error":
        if field == "email" or "email address" in msg:
            return "Введите корректный email, например anna@example.com"
        return msg.removeprefix("Value error, ")
    if kind == "string_too_short":
        return "Поле не может быть пустым" if ctx.get("min_length") == 1 else f"Минимум {ctx.get('min_length')} символов"
    if kind == "string_too_long":
        return f"Максимум {ctx.get('max_length')} символов"
    if kind == "too_short":
        return "Список не может быть пустым" if ctx.get("min_length") == 1 else f"Минимум {ctx.get('min_length')} элементов"
    if kind == "too_long":
        return f"Максимум {ctx.get('max_length')} элементов"
    if kind == "greater_than":
        return f"Значение должно быть больше {ctx.get('gt')}"
    if kind == "greater_than_equal":
        return f"Значение должно быть не меньше {ctx.get('ge')}"
    if kind == "less_than_equal":
        return f"Значение должно быть не больше {ctx.get('le')}"
    if kind in ("int_parsing", "int_type", "decimal_parsing", "float_parsing", "decimal_type"):
        return "Введите число"
    if kind in ("decimal_max_places", "decimal_max_digits"):
        return "Не больше двух знаков после запятой"
    if kind in ("string_type", "model_type", "dict_type", "list_type"):
        return "Неверный формат значения"
    if kind == "json_invalid":
        return "Некорректный JSON"
    return msg


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        level = logging.WARNING if exc.status_code < 500 else logging.ERROR
        logger.log(level, exc.message, extra=log_extra(error_code=exc.code, status=exc.status_code))
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"), "message": humanize_validation_error(err)}
            for err in exc.errors()
        ]
        logger.warning("validation failed", extra=log_extra(details=details))
        return JSONResponse(status_code=422, content=error_body("validation_error", "Проверьте введённые данные", details))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return JSONResponse(status_code=exc.status_code, content=error_body(code, message), headers=exc.headers)

    @app.exception_handler(SQLAlchemyError)
    async def handle_db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database error")
        return JSONResponse(status_code=503, content=error_body("database_error", "Database is temporarily unavailable"))
