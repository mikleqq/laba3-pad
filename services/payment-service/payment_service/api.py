from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.db import get_session
from shopcommon.schemas import error_responses
from shopcommon.security import CurrentUser, get_current_user

from .schemas import PaymentOut, PaymentPage
from .service import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> PaymentService:
    return PaymentService(session, request.app.state.provider, request.app.state.bus)


@router.get("", response_model=PaymentPage, summary="Own payments (admin: all)", responses=error_responses(401))
async def list_payments(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    service: PaymentService = Depends(get_service),
):
    items, total = await service.list_all(user, limit, offset)
    return PaymentPage(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/order/{order_id}", response_model=PaymentOut, summary="Payment of an order", responses=error_responses(401, 404)
)
async def get_payment_for_order(
    order_id: int, user: CurrentUser = Depends(get_current_user), service: PaymentService = Depends(get_service)
):
    return await service.get_for_order(order_id, user)
