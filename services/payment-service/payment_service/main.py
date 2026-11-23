from decimal import Decimal
from typing import Optional

from fastapi import FastAPI

from shopcommon.app import create_service_app
from shopcommon.messaging import Event, EventBus

from .api import router
from .config import Settings
from .models import Base
from .provider import FakePaymentProvider
from .service import PaymentService


def create_app(settings: Optional[Settings] = None, *, bus: Optional[EventBus] = None) -> FastAPI:
    settings = settings or Settings()
    provider = FakePaymentProvider(Decimal(str(settings.max_payment_amount)))

    async def on_startup(app: FastAPI) -> None:
        async def handle(event: Event) -> None:
            async with app.state.db.sessionmaker() as session:
                service = PaymentService(session, provider, app.state.bus)
                if event["event_type"] == "order.created":
                    await service.handle_order_created(event)
                elif event["event_type"] == "order.cancelled":
                    await service.handle_order_cancelled(event)

        await app.state.bus.subscribe(
            "payment-service.orders",
            ["order.created", "order.cancelled"],
            handle,
            service_name=settings.service_name,
        )

    app = create_service_app(
        settings=settings,
        title="ShopHub Payment Service",
        description="Consumes `order.created`/`order.cancelled`, charges/refunds, publishes `payment.*`.",
        metadata=Base.metadata,
        bus=bus,
        routers=[router],
        on_startup=on_startup,
    )
    app.state.provider = provider
    return app


app = create_app()
