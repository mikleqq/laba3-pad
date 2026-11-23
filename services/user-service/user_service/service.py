"""Business logic of the User Service."""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from shopcommon.logging import log_extra
from shopcommon.security import CurrentUser

from .models import Profile
from .schemas import ProfileCreate, ProfileUpdate

logger = logging.getLogger("user_service")


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: ProfileCreate) -> tuple[Profile, bool]:
        """Idempotent: repeating the same call returns the existing profile."""
        existing = await self.session.get(Profile, data.id)
        if existing is not None:
            if existing.email != data.email.lower():
                raise ConflictError("Profile id already used by another email")
            return existing, False
        if await self.session.scalar(select(Profile).where(Profile.email == data.email.lower())):
            raise ConflictError("Profile with this email already exists", code="email_taken")
        profile = Profile(id=data.id, email=data.email.lower(), full_name=data.full_name)
        self.session.add(profile)
        await self.session.commit()
        logger.info("profile created", extra=log_extra(user_id=profile.id))
        return profile, True

    async def get(self, user_id: int, requester: CurrentUser) -> Profile:
        if requester.id != user_id and not requester.is_admin:
            raise ForbiddenError("You can only view your own profile")
        profile = await self.session.get(Profile, user_id)
        if profile is None:
            raise NotFoundError(f"User {user_id} not found")
        return profile

    async def update(self, user_id: int, data: ProfileUpdate) -> Profile:
        profile = await self.session.get(Profile, user_id)
        if profile is None:
            raise NotFoundError(f"User {user_id} not found")
        changes = data.model_dump(exclude_unset=True)
        if not changes:
            raise BadRequestError("Nothing to update")
        for field, value in changes.items():
            setattr(profile, field, value)
        await self.session.commit()
        logger.info("profile updated", extra=log_extra(user_id=user_id, fields=sorted(changes)))
        return profile

    async def list_all(self, limit: int, offset: int) -> tuple[list[Profile], int]:
        total = await self.session.scalar(select(func.count()).select_from(Profile))
        rows = await self.session.scalars(select(Profile).order_by(Profile.id).limit(limit).offset(offset))
        return list(rows), int(total or 0)
