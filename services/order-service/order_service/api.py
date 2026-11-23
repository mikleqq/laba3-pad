from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.db import get_session
from shopcommon.schemas import error_responses
from shopcommon.security import CurrentUser, get_current_user

from .schemas import OrderCreate, OrderOut, OrderPage
from .service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> OrderService:
    return OrderService(session, request.app.state.product_client, request.app.state.bus)


@router.post(
    "",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Place an order (stock is reserved, payment is processed asynchronously)",
    responses=error_responses(401, 404, 409, 422, 502, 504),
)
async def create_order(
    data: OrderCreate, user: CurrentUser = Depends(get_current_user), service: OrderService = Depends(get_service)
):
    return await service.create(user, data)


@router.get("", response_model=OrderPage, summary="Own orders (admin: all orders)", responses=error_responses(401))
async def list_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    service: OrderService = Depends(get_service),
):
    items, total = await service.list_all(user, status_filter, limit, offset)
    return OrderPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{order_id}", response_model=OrderOut, summary="Order details", responses=error_responses(401, 404))
async def get_order(order_id: int, user: CurrentUser = Depends(get_current_user), service: OrderService = Depends(get_service)):
    return await service.get(order_id, user)


@router.post(
    "/{order_id}/cancel",
    response_model=OrderOut,
    summary="Cancel order (stock released, payment refunded)",
    responses=error_responses(401, 404, 409),
)
async def cancel_order(
    order_id: int, user: CurrentUser = Depends(get_current_user), service: OrderService = Depends(get_service)
):
    return await service.cancel(order_id, user)
