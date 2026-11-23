from typing import Optional

from fastapi import FastAPI

from shopcommon.app import create_service_app
from shopcommon.messaging import EventBus

from .api import internal_router, router
from .config import Settings
from .models import Base
from .service import ProductService


def create_app(settings: Optional[Settings] = None, *, bus: Optional[EventBus] = None) -> FastAPI:
    settings = settings or Settings()

    async def on_startup(app: FastAPI) -> None:
        if settings.seed_demo_data:
            async with app.state.db.sessionmaker() as session:
                await ProductService(session).seed()

    return create_service_app(
        settings=settings,
        title="ShopHub Product Service",
        description="Product catalog and stock management. Publishes `product.low_stock`.",
        metadata=Base.metadata,
        bus=bus,
        routers=[router, internal_router],
        on_startup=on_startup,
    )


app = create_app()
