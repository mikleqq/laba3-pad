"""Authentication / authorization helpers (JWT + internal service token)."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import ForbiddenError, UnauthorizedError

ROLE_ADMIN = "admin"
ROLE_CUSTOMER = "customer"


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def create_access_token(
    *, user_id: int, email: str, role: str, secret: str, algorithm: str = "HS256", expires_minutes: int = 60
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "iss": "shophub-auth",
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, secret: str, algorithm: str = "HS256") -> CurrentUser:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm], issuer="shophub-auth")
        return CurrentUser(id=int(payload["sub"]), email=payload["email"], role=payload["role"])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Access token has expired", code="token_expired") from exc
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid access token", code="invalid_token") from exc


_bearer = HTTPBearer(auto_error=False, description="JWT obtained from POST /auth/login")


async def get_current_user(
    request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Missing bearer token")
    settings = request.app.state.settings
    return decode_access_token(credentials.credentials, settings.jwt_secret, settings.jwt_algorithm)


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise ForbiddenError("Administrator role required")
    return user


async def require_internal(
    request: Request, x_internal_token: Optional[str] = Header(default=None, include_in_schema=False)
) -> None:
    """Protects service-to-service endpoints (``/internal/*``)."""
    expected = request.app.state.settings.internal_token
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise ForbiddenError("Internal endpoint", code="internal_only")
