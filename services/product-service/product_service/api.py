from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.db import get_session
from shopcommon.schemas import error_responses
from shopcommon.security import CurrentUser, require_admin, require_internal

from .schemas import ProductCreate, ProductOut, ProductPage, ProductUpdate, ReservationOut, StockRequest
from .service import ProductService

router = APIRouter(prefix="/products", tags=["products"])
internal_router = APIRouter(prefix="/internal/stock", tags=["internal"], dependencies=[Depends(require_internal)])


def get_service(request: Request, session: AsyncSession = Depends(get_session)) -> ProductService:
    return ProductService(session, request.app.state.bus, request.app.state.settings.low_stock_threshold)


@router.get("", response_model=ProductPage, summary="Browse the catalog (public)")
async def list_products(
    search: Optional[str] = Query(None, max_length=100, description="Search in name/description"),
    category: Optional[str] = Query(None, max_length=60),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: ProductService = Depends(get_service),
):
    items, total = await service.list_all(search=search, category=category, limit=limit, offset=offset)
    return ProductPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{product_id}", response_model=ProductOut, summary="Product details (public)", responses=error_responses(404))
async def get_product(product_id: int, service: ProductService = Depends(get_service)):
    return await service.get(product_id)


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create product (admin)",
    responses=error_responses(401, 403, 422),
)
async def create_product(
    data: ProductCreate, _: CurrentUser = Depends(require_admin), service: ProductService = Depends(get_service)
):
    return await service.create(data)


@router.patch(
    "/{product_id}", response_model=ProductOut, summary="Update product (admin)", responses=error_responses(400, 401, 403, 404, 422)
)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    _: CurrentUser = Depends(require_admin),
    service: ProductService = Depends(get_service),
):
    return await service.update(product_id, data)


@router.delete(
    "/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete product (admin)", responses=error_responses(401, 403, 404)
)
async def delete_product(
    product_id: int, _: CurrentUser = Depends(require_admin), service: ProductService = Depends(get_service)
) -> Response:
    await service.delete(product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@internal_router.post("/reserve", response_model=ReservationOut, summary="Reserve stock (order-service)")
async def reserve_stock(data: StockRequest, service: ProductService = Depends(get_service)):
    return ReservationOut(items=await service.reserve(data.items))


@internal_router.post("/release", status_code=status.HTTP_204_NO_CONTENT, summary="Release reserved stock (order-service)")
async def release_stock(data: StockRequest, service: ProductService = Depends(get_service)) -> Response:
    await service.release(data.items)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
