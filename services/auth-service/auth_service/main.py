import asyncio
from typing import Optional

import httpx
from fastapi import FastAPI

from shopcommon.app import create_service_app
from shopcommon.http import ServiceClient
from shopcommon.messaging import EventBus

from .api import router
from .config import Settings
from .models import Base
from .service import ensure_admin


def create_app(
    settings: Optional[Settings] = None,
    *,
    bus: Optional[EventBus] = None,
    user_transport: Optional[httpx.AsyncBaseTransport] = None,
    bootstrap_admin: bool = True,
) -> FastAPI:
    settings = settings or Settings()
    user_client = ServiceClient(
        "user-service",
        settings.user_service_url,
        timeout=settings.http_timeout_seconds,
        internal_token=settings.internal_token,
        transport=user_transport,
    )

    async def on_startup(app: FastAPI) -> None:
        if bootstrap_admin:
            app.state.admin_task = asyncio.create_task(ensure_admin(app))

    async def on_shutdown(app: FastAPI) -> None:
        task = getattr(app.state, "admin_task", None)
        if task is not None and not task.done():
            task.cancel()
        await user_client.close()

    app = create_service_app(
        settings=settings,
        title="ShopHub Auth Service",
        description="Registration, login and JWT issuing. Publishes `user.registered`.",
        metadata=Base.metadata,
        bus=bus,
        routers=[router],
        on_startup=on_startup,
        on_shutdown=on_shutdown,
    )
    app.state.user_client = user_client
    return app


app = create_app()
