"""Service application factory: wires logging, middleware, errors, health checks, DB and bus."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Iterable, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import MetaData

from .config import BaseServiceSettings
from .db import Database
from .errors import register_exception_handlers
from .logging import log_extra, setup_logging
from .messaging import EventBus, InMemoryEventBus, PostgresEventBus
from .middleware import RequestContextMiddleware

logger = logging.getLogger("shopcommon.app")

Hook = Callable[[FastAPI], Awaitable[None]]
ReadyCheck = Callable[[], Awaitable[bool]]


def _health_router(service_name: str, version: str) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health", summary="Liveness probe")
    async def health() -> dict:
        return {"status": "ok", "service": service_name, "version": version}

    @router.get("/ready", summary="Readiness probe (dependencies)")
    async def ready(request: Request) -> JSONResponse:
        checks: dict[str, bool] = {}
        for name, check in request.app.state.ready_checks.items():
            try:
                checks[name] = bool(await check())
            except Exception:  # noqa: BLE001
                checks[name] = False
        ok = all(checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ready" if ok else "not_ready", "service": service_name, "checks": checks},
        )

    return router


def create_service_app(
    *,
    settings: BaseServiceSettings,
    title: str,
    description: str,
    version: str = "1.0.0",
    metadata: Optional[MetaData] = None,
    bus: Optional[EventBus] = None,
    use_messaging: bool = True,
    routers: Iterable[APIRouter] = (),
    on_startup: Optional[Hook] = None,
    on_shutdown: Optional[Hook] = None,
    ready_checks: Optional[dict[str, ReadyCheck]] = None,
    openapi_tags: Optional[list[dict]] = None,
) -> FastAPI:
    setup_logging(settings.service_name, settings.log_level)

    db = Database(settings.database_url, metadata) if metadata is not None else None
    owns_bus = bus is None
    if use_messaging and bus is None:
        if settings.database_url.startswith("postgresql"):
            bus = PostgresEventBus(
                settings.database_url,
                service_name=settings.service_name,
                poll_interval=settings.event_poll_interval_seconds,
            )
        else:
            bus = InMemoryEventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if db is not None:
            await db.connect(settings.startup_retries, settings.startup_retry_delay_seconds)
        if bus is not None and owns_bus:
            await bus.connect(settings.startup_retries, settings.startup_retry_delay_seconds)
        if on_startup is not None:
            await on_startup(app)
        logger.info("service started", extra=log_extra(version=version))
        try:
            yield
        finally:
            if on_shutdown is not None:
                await on_shutdown(app)
            if bus is not None and owns_bus:
                await bus.close()
            if db is not None:
                await db.dispose()
            logger.info("service stopped")

    app = FastAPI(
        title=title,
        description=description,
        version=version,
        lifespan=lifespan,
        openapi_tags=openapi_tags,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.bus = bus

    checks: dict[str, ReadyCheck] = {}
    if db is not None:
        checks["database"] = db.ping
    if bus is not None:
        checks["event_bus"] = bus.is_healthy
    checks.update(ready_checks or {})
    app.state.ready_checks = checks

    app.add_middleware(RequestContextMiddleware, service_name=settings.service_name)
    # The web UI (frontend/index.html) talks to every service directly from the browser.
    # Tokens travel in the Authorization header (no cookies), so a wildcard origin is safe here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    register_exception_handlers(app)
    app.include_router(_health_router(settings.service_name, version))
    for router in routers:
        app.include_router(router)
    return app
