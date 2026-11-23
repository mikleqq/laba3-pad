"""Business logic of the Payment Service."""

import logging
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.errors import NotFoundError
from shopcommon.logging import log_extra
from shopcommon.messaging import Event, EventBus, safe_publish
from shopcommon.security import CurrentUser

from .models import Payment, PaymentStatus
from .provider import FakePaymentProvider

logger = logging.getLogger("payment_service")


class PaymentService:
    def __init__(self, session: AsyncSession, provider: FakePaymentProvider, bus: EventBus) -> None:
        self.session = session
        self.provider = provider
        self.bus = bus

    async def _by_order(self, order_id: int):
        return await self.session.scalar(select(Payment).where(Payment.order_id == order_id))

    # ---------- event handlers ----------
    async def handle_order_created(self, event: Event) -> None:
        data = event["data"]
        order_id, user_id = int(data["order_id"]), int(data["user_id"])
        amount = Decimal(str(data["total_amount"]))

        if await self._by_order(order_id) is not None:
            logger.info("duplicate order.created ignored", extra=log_extra(order_id=order_id))
            return

        result = await self.provider.charge(order_id, amount)
        payment = Payment(
            order_id=order_id,
            user_id=user_id,
            amount=amount,
            status=PaymentStatus.COMPLETED if result.success else PaymentStatus.FAILED,
            transaction_id=result.transaction_id,
            failure_reason=result.reason,
        )
        self.session.add(payment)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            logger.info("concurrent duplicate payment ignored", extra=log_extra(order_id=order_id))
            return

        payload = {
            "payment_id": payment.id,
            "order_id": order_id,
            "user_id": user_id,
            "amount": str(amount),
            "transaction_id": payment.transaction_id,
        }
        if result.success:
            logger.info("payment completed", extra=log_extra(order_id=order_id, amount=str(amount)))
            await safe_publish(self.bus, "payment.completed", payload)
        else:
            logger.warning("payment declined", extra=log_extra(order_id=order_id, reason=result.reason))
            await safe_publish(self.bus, "payment.failed", {**payload, "reason": result.reason})

    async def handle_order_cancelled(self, event: Event) -> None:
        data = event["data"]
        order_id, user_id = int(data["order_id"]), int(data["user_id"])
        payment = await self._by_order(order_id)
        if payment is None:
            # Cancelled before charging: leave a marker so a late order.created is not charged.
            self.session.add(
                Payment(
                    order_id=order_id,
                    user_id=user_id,
                    amount=Decimal(str(data.get("total_amount", "0"))),
                    status=PaymentStatus.CANCELLED,
                    failure_reason="Order cancelled before payment",
                )
            )
            await self.session.commit()
            logger.info("payment cancelled before charge", extra=log_extra(order_id=order_id))
            return
        if payment.status != PaymentStatus.COMPLETED:
            return
        await self.provider.refund(payment.transaction_id or "", payment.amount)
        payment.status = PaymentStatus.REFUNDED
        await self.session.commit()
        logger.info("payment refunded", extra=log_extra(order_id=order_id, amount=str(payment.amount)))
        await safe_publish(
            self.bus,
            "payment.refunded",
            {"payment_id": payment.id, "order_id": order_id, "user_id": user_id, "amount": str(payment.amount)},
        )

    # ---------- queries ----------
    async def get_for_order(self, order_id: int, requester: CurrentUser) -> Payment:
        payment = await self._by_order(order_id)
        if payment is None or (payment.user_id != requester.id and not requester.is_admin):
            raise NotFoundError(f"Payment for order {order_id} not found")
        return payment

    async def list_all(self, requester: CurrentUser, limit: int, offset: int):
        query = select(Payment)
        if not requester.is_admin:
            query = query.where(Payment.user_id == requester.id)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self.session.scalars(query.order_by(Payment.id.desc()).limit(limit).offset(offset))
        return list(rows), int(total or 0)
