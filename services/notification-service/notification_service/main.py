from typing import Optional

from fastapi import FastAPI

from shopcommon.app import create_service_app
from shopcommon.messaging import Event, EventBus

from .api import router
from .config import Settings
from .models import Base
from .service import EmailSender, NotificationService


def create_app(settings: Optional[Settings] = None, *, bus: Optional[EventBus] = None) -> FastAPI:
    settings = settings or Settings()
    sender = EmailSender(settings.sender_address)

    async def on_startup(app: FastAPI) -> None:
        async def handle(event: Event) -> None:
            async with app.state.db.sessionmaker() as session:
                await NotificationService(session, sender, settings.default_channel).handle_event(event)

        await app.state.bus.subscribe(
            "notification-service.events",
            ["user.registered", "order.*", "payment.*", "product.low_stock"],
            handle,
            service_name=settings.service_name,
        )

    return create_service_app(
        settings=settings,
        title="ShopHub Notification Service",
        description="Consumes domain events from the event bus and notifies users (email channel stub).",
        metadata=Base.metadata,
        bus=bus,
        routers=[router],
        on_startup=on_startup,
    )


app = create_app()
