"""Business logic of the Notification Service."""

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.errors import NotFoundError
from shopcommon.logging import log_extra
from shopcommon.messaging import Event
from shopcommon.security import CurrentUser

from .models import Notification

logger = logging.getLogger("notification_service")

# event type -> (subject, body). Placeholders are filled from the event payload.
TEMPLATES: dict[str, tuple[str, str]] = {
    "user.registered": ("Добро пожаловать в ShopHub!", "Здравствуйте, {full_name}! Аккаунт {email} создан."),
    "order.created": ("Заказ №{order_id} принят", "Заказ №{order_id} на сумму {total_amount} $ создан и ожидает оплаты."),
    "order.cancelled": ("Заказ №{order_id} отменён", "Заказ №{order_id} отменён."),
    "payment.completed": (
        "Заказ №{order_id} оплачен",
        "Платёж {amount} $ прошёл (транзакция {transaction_id}). Заказ собирается.",
    ),
    "payment.failed": ("Оплата заказа №{order_id} отклонена", "Платёж {amount} $ отклонён: {reason}."),
    "payment.refunded": ("Возврат по заказу №{order_id}", "{amount} $ возвращены на карту."),
}

ADMIN_TEMPLATES: dict[str, tuple[str, str]] = {
    "product.low_stock": ("Мало товара: {name}", "Товар №{product_id} «{name}»: осталось {stock} шт."),
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "?"


class EmailSender:
    """Stub delivery channel: in production this would call SMTP / an email API."""

    def __init__(self, sender: str) -> None:
        self.sender = sender

    async def send(self, user_id: Optional[int], subject: str, body: str) -> None:
        logger.info("email sent", extra=log_extra(sender=self.sender, user_id=user_id, subject=subject))


class NotificationService:
    def __init__(self, session: AsyncSession, sender: Optional[EmailSender] = None, channel: str = "email") -> None:
        self.session = session
        self.sender = sender
        self.channel = channel

    async def handle_event(self, event: Event) -> Optional[Notification]:
        event_type = event["event_type"]
        data = event.get("data", {})
        template = TEMPLATES.get(event_type) or ADMIN_TEMPLATES.get(event_type)
        if template is None:
            logger.info("event without template ignored", extra=log_extra(event_type=event_type))
            return None

        existing = await self.session.scalar(select(Notification).where(Notification.event_id == event["event_id"]))
        if existing is not None:
            logger.info("duplicate event ignored", extra=log_extra(event_id=event["event_id"]))
            return existing

        values = _SafeDict(data)
        subject, body = template[0].format_map(values), template[1].format_map(values)
        user_id = data.get("user_id")  # None -> admin/broadcast notification
        notification = Notification(
            event_id=event["event_id"],
            user_id=int(user_id) if user_id is not None else None,
            event_type=event_type,
            channel=self.channel,
            subject=subject,
            message=body,
            status="pending",
        )
        self.session.add(notification)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return None
        if self.sender is not None:
            try:
                await self.sender.send(notification.user_id, subject, body)
                notification.status = "sent"
            except Exception:  # noqa: BLE001
                logger.exception("notification delivery failed", extra=log_extra(notification_id=notification.id))
                notification.status = "failed"
        await self.session.commit()
        logger.info("notification stored", extra=log_extra(notification_id=notification.id, event_type=event_type))
        return notification

    async def list_all(self, user: CurrentUser, unread_only: bool, limit: int):
        query = select(Notification).where(Notification.user_id == user.id)
        if unread_only:
            query = query.where(Notification.is_read.is_(False))
        rows = await self.session.scalars(query.order_by(Notification.id.desc()).limit(limit))
        total = await self.session.scalar(
            select(func.count()).select_from(Notification).where(Notification.user_id == user.id)
        )
        unread = await self.session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user.id, Notification.is_read.is_(False))
        )
        return list(rows), int(total or 0), int(unread or 0)

    async def mark_read(self, notification_id: int, user: CurrentUser) -> Notification:
        notification = await self.session.get(Notification, notification_id)
        if notification is None or notification.user_id != user.id:
            raise NotFoundError(f"Notification {notification_id} not found")
        notification.is_read = True
        await self.session.commit()
        return notification
