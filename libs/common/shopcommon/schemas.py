"""Shared API schema building blocks (error model for OpenAPI, money type)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Optional

from pydantic import BaseModel, PlainSerializer

# Decimal internally (exact arithmetic), JSON number on the wire.
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


_DESCRIPTIONS = {
    400: "Bad request",
    401: "Missing or invalid access token",
    403: "Not enough permissions",
    404: "Resource not found",
    409: "Conflict with the current state of the resource",
    422: "Validation error",
    429: "Rate limit exceeded",
    502: "Dependent service failed",
    503: "Service or database unavailable",
    504: "Dependent service timeout",
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorResponse, "description": _DESCRIPTIONS.get(code, "Error")} for code in codes}
