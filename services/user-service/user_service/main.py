from typing import Optional

from fastapi import FastAPI

from shopcommon.app import create_service_app

from .api import internal_router, router
from .config import Settings
from .models import Base


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    return create_service_app(
        settings=settings,
        title="ShopHub User Service",
        description="Customer profiles (name, phone, address).",
        metadata=Base.metadata,
        use_messaging=False,
        routers=[router, internal_router],
    )


app = create_app()
