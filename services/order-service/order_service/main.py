from typing import Optional

import httpx
from fastapi import FastAPI

from shopcommon.app import create_service_app
from shopcommon.http import ServiceClient
from shopcommon.messaging import Event, EventBus

from .api import router
from .config import Settings
from .models import Base
from .service import OrderService


async def upgrade_schema(db) -> None:
    """Add the delivery columns to an orders table created by an older version (PostgreSQL)."""
    if db.engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text

    async with db.engine.begin() as conn:
        for ddl in (
            "ADD COLUMN IF NOT EXISTS recipient varchar(100)",
            "ADD COLUMN IF NOT EXISTS contact_phone varchar(32)",
            "ADD COLUMN IF NOT EXISTS delivery_address varchar(255)",
            "ADD COLUMN IF NOT EXISTS comment text",
        ):
            await conn.execute(text(f"ALTER TABLE orders {ddl}"))


def create_app(
    settings: Optional[Settings] = None,
    *,
    bus: Optional[EventBus] = None,
    product_transport: Optional[httpx.AsyncBaseTransport] = None,
) -> FastAPI:
    settings = settings or Settings()
    product_client = ServiceClient(
        "product-service",
        settings.product_service_url,
        timeout=settings.http_timeout_seconds,
        internal_token=settings.internal_token,
        transport=product_transport,
    )

    async def on_startup(app: FastAPI) -> None:
        await upgrade_schema(app.state.db)

        async def handle_payment(event: Event) -> None:
            async with app.state.db.sessionmaker() as session:
                await OrderService(session, product_client, app.state.bus).apply_payment_result(event)

        await app.state.bus.subscribe(
            "order-service.payments",
            ["payment.completed", "payment.failed"],
            handle_payment,
            service_name=settings.service_name,
        )

    async def on_shutdown(app: FastAPI) -> None:
        await product_client.close()

    app = create_service_app(
        settings=settings,
        title="ShopHub Order Service",
        description=(
            "Order lifecycle. Calls product-service synchronously (stock reservation), "
            "publishes `order.created` / `order.cancelled`, consumes `payment.*`."
        ),
        metadata=Base.metadata,
        bus=bus,
        routers=[router],
        on_startup=on_startup,
        on_shutdown=on_shutdown,
    )
    app.state.product_client = product_client
    return app


app = create_app()
