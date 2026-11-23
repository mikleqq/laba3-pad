"""Business logic of the Auth Service."""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.errors import ConflictError, ForbiddenError, UnauthorizedError
from shopcommon.http import ServiceClient, raise_for_upstream
from shopcommon.logging import log_extra
from shopcommon.messaging import EventBus, safe_publish
from shopcommon.security import ROLE_ADMIN, ROLE_CUSTOMER, create_access_token

from .config import Settings
from .models import Credential
from .passwords import hash_password, verify_password
from .schemas import RegisterRequest

logger = logging.getLogger("auth_service")


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings, user_client: ServiceClient, bus: EventBus) -> None:
        self.session = session
        self.settings = settings
        self.user_client = user_client
        self.bus = bus

    async def get_by_email(self, email: str):
        return await self.session.scalar(select(Credential).where(Credential.email == email.lower()))

    async def register(self, data: RegisterRequest, role: str = ROLE_CUSTOMER) -> Credential:
        email = data.email.lower()
        if await self.get_by_email(email):
            raise ConflictError("User with this email already exists", code="email_taken")

        credential = Credential(
            email=email,
            password_hash=hash_password(data.password, self.settings.password_hash_iterations),
            role=role,
        )
        self.session.add(credential)
        try:
            await self.session.flush()
            # Synchronous call: the profile must exist before we report success.
            response = await self.user_client.request(
                "POST",
                "/internal/users",
                json={"id": credential.id, "email": email, "full_name": data.full_name},
            )
            raise_for_upstream(response, "user-service")
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("User with this email already exists", code="email_taken") from exc
        except Exception:
            await self.session.rollback()
            raise

        logger.info("user registered", extra=log_extra(user_id=credential.id, role=role))
        await safe_publish(
            self.bus,
            "user.registered",
            {"user_id": credential.id, "email": email, "full_name": data.full_name, "role": role},
        )
        return credential

    async def login(self, email: str, password: str) -> tuple[str, Credential]:
        credential = await self.get_by_email(email)
        if credential is None or not verify_password(password, credential.password_hash):
            logger.warning("failed login attempt", extra=log_extra(email=email.lower()))
            raise UnauthorizedError("Invalid email or password", code="invalid_credentials")
        if not credential.is_active:
            raise ForbiddenError("Account is disabled", code="account_disabled")
        token = create_access_token(
            user_id=credential.id,
            email=credential.email,
            role=credential.role,
            secret=self.settings.jwt_secret,
            algorithm=self.settings.jwt_algorithm,
            expires_minutes=self.settings.access_token_expire_minutes,
        )
        logger.info("user logged in", extra=log_extra(user_id=credential.id))
        return token, credential


async def ensure_admin(app) -> None:
    """Create the bootstrap administrator account (idempotent, retried in background)."""
    settings: Settings = app.state.settings
    data = RegisterRequest(email=settings.admin_email, password=settings.admin_password, full_name=settings.admin_full_name)
    for attempt in range(1, 11):
        async with app.state.db.sessionmaker() as session:
            service = AuthService(session, settings, app.state.user_client, app.state.bus)
            if await service.get_by_email(data.email):
                return
            try:
                await service.register(data, role=ROLE_ADMIN)
                logger.info("bootstrap administrator created", extra=log_extra(email=data.email))
                return
            except Exception as exc:  # noqa: BLE001 - user-service may still be starting
                logger.warning("admin bootstrap failed, retrying", extra=log_extra(attempt=attempt, error=str(exc)))
        await asyncio.sleep(3)
