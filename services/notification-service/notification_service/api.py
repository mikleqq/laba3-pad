from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.db import get_session
from shopcommon.schemas import error_responses
from shopcommon.security import CurrentUser, get_current_user

from .schemas import NotificationOut, NotificationPage
from .service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_service(session: AsyncSession = Depends(get_session)) -> NotificationService:
    return NotificationService(session)


@router.get("", response_model=NotificationPage, summary="Own notifications", responses=error_responses(401))
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    service: NotificationService = Depends(get_service),
):
    items, total, unread = await service.list_all(user, unread_only, limit)
    return NotificationPage(items=items, total=total, unread=unread)


@router.post(
    "/{notification_id}/read",
    response_model=NotificationOut,
    summary="Mark notification as read",
    responses=error_responses(401, 404),
)
async def mark_read(
    notification_id: int, user: CurrentUser = Depends(get_current_user), service: NotificationService = Depends(get_service)
):
    return await service.mark_read(notification_id, user)
