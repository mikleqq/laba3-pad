"""Business logic of the Order Service.

Order saga::

    POST /orders ──REST──> product-service /internal/stock/reserve
         │ commit order (pending_payment)
         └─ publish order.created ──> payment-service
                                       └─ payment.completed / payment.failed ──> order-service
                                             paid            | payment_failed + stock released
"""

import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.errors import ConflictError, ForbiddenError, NotFoundError
from shopcommon.http import ServiceClient, raise_for_upstream
from shopcommon.logging import log_extra
from shopcommon.messaging import Event, EventBus, safe_publish
from shopcommon.security import CurrentUser

from .models import Order, OrderItem, OrderStatus
from .schemas import OrderCreate

logger = logging.getLogger("order_service")


def _merge(items) -> dict[int, int]:
    merged: dict[int, int] = {}
    for item in items:
        merged[item.product_id] = merged.get(item.product_id, 0) + item.quantity
    return merged


class OrderService:
    def __init__(self, session: AsyncSession, product_client: ServiceClient, bus: EventBus) -> None:
        self.session = session
        self.product_client = product_client
        self.bus = bus

    async def create(self, user: CurrentUser, data: OrderCreate) -> Order:
        merged = _merge(data.items)
        stock_items = [{"product_id": pid, "quantity": qty} for pid, qty in merged.items()]

        # 1. Synchronous step: reserve stock (fails fast with 404/409 if impossible).
        response = await self.product_client.request("POST", "/internal/stock/reserve", json={"items": stock_items})
        raise_for_upstream(response, "product-service")
        reserved = response.json()["items"]

        # 2. Persist the order.
        delivery = data.delivery
        order = Order(
            user_id=user.id,
            status=OrderStatus.PENDING_PAYMENT,
            total_amount=Decimal("0"),
            recipient=delivery.recipient,
            contact_phone=delivery.phone,
            delivery_address=delivery.address,
            comment=delivery.comment,
        )
        total = Decimal("0")
        for item in reserved:
            price = Decimal(str(item["unit_price"]))
            total += price * item["quantity"]
            order.items.append(
                OrderItem(
                    product_id=item["product_id"],
                    product_name=item["name"],
                    unit_price=price,
                    quantity=item["quantity"],
                )
            )
        order.total_amount = total.quantize(Decimal("0.01"))
        self.session.add(order)
        try:
            await self.session.commit()
        except Exception:
            # Compensation: give the reserved stock back.
            await self.session.rollback()
            await self._release_stock(stock_items)
            raise

        logger.info("order created", extra=log_extra(order_id=order.id, user_id=user.id, total=str(order.total_amount)))

        # 3. Asynchronous step: payment is processed by payment-service.
        await safe_publish(
            self.bus,
            "order.created",
            {
                "order_id": order.id,
                "user_id": user.id,
                "total_amount": str(order.total_amount),
                "items": stock_items,
            },
        )
        return order

    async def get(self, order_id: int, requester: CurrentUser) -> Order:
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError(f"Order {order_id} not found")
        if order.user_id != requester.id and not requester.is_admin:
            # Do not reveal existence of other users' orders.
            raise NotFoundError(f"Order {order_id} not found")
        return order

    async def list_all(self, requester: CurrentUser, status: Optional[str], limit: int, offset: int):
        query = select(Order)
        if not requester.is_admin:
            query = query.where(Order.user_id == requester.id)
        if status:
            query = query.where(Order.status == status)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self.session.scalars(query.order_by(Order.id.desc()).limit(limit).offset(offset))
        return list(rows), int(total or 0)

    async def cancel(self, order_id: int, requester: CurrentUser) -> Order:
        order = await self.get(order_id, requester)
        if order.status == OrderStatus.CANCELLED:
            raise ConflictError("Order is already cancelled", code="already_cancelled")
        if order.user_id != requester.id and not requester.is_admin:
            raise ForbiddenError()
        previous = order.status
        order.status = OrderStatus.CANCELLED
        await self.session.commit()
        if previous != OrderStatus.PAYMENT_FAILED:  # stock was already returned on payment failure
            await self._release_stock(self._stock_items(order))
        logger.info("order cancelled", extra=log_extra(order_id=order.id, previous_status=previous))
        await safe_publish(
            self.bus,
            "order.cancelled",
            {
                "order_id": order.id,
                "user_id": order.user_id,
                "total_amount": str(order.total_amount),
                "previous_status": previous,
            },
        )
        return order

    # ---------- event handlers ----------
    async def apply_payment_result(self, event: Event) -> None:
        data = event["data"]
        order = await self.session.get(Order, int(data["order_id"]))
        if order is None:
            logger.warning("payment for unknown order", extra=log_extra(order_id=data.get("order_id")))
            return
        if order.status != OrderStatus.PENDING_PAYMENT:
            # Idempotency / race with cancellation: nothing to do.
            logger.info("payment result ignored", extra=log_extra(order_id=order.id, status=order.status))
            return
        if event["event_type"] == "payment.completed":
            order.status = OrderStatus.PAID
            await self.session.commit()
            logger.info("order paid", extra=log_extra(order_id=order.id))
        elif event["event_type"] == "payment.failed":
            order.status = OrderStatus.PAYMENT_FAILED
            await self.session.commit()
            await self._release_stock(self._stock_items(order))
            logger.info("order payment failed", extra=log_extra(order_id=order.id, reason=data.get("reason")))

    # ---------- helpers ----------
    @staticmethod
    def _stock_items(order: Order) -> list[dict]:
        return [{"product_id": i.product_id, "quantity": i.quantity} for i in order.items]

    async def _release_stock(self, stock_items: list[dict]) -> None:
        try:
            response = await self.product_client.request("POST", "/internal/stock/release", json={"items": stock_items})
            raise_for_upstream(response, "product-service")
        except Exception:  # noqa: BLE001 - compensation is best effort, but must be visible
            logger.exception("stock release failed", extra=log_extra(items=stock_items))
