"""Async SQLAlchemy plumbing. Each service passes its *own* metadata/database URL."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import Request
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .logging import log_extra

logger = logging.getLogger("shopcommon.db")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, url: str, metadata: MetaData) -> None:
        self.url = url
        self.metadata = metadata
        kwargs: dict = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
        else:
            kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=10)
        self.engine = create_async_engine(url, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def connect(self, retries: int = 30, delay: float = 2.0) -> None:
        """Wait for the database and create the schema (tables owned by this service only)."""
        for attempt in range(1, retries + 1):
            try:
                async with self.engine.begin() as conn:
                    await conn.run_sync(self.metadata.create_all)
                logger.info("database ready")
                return
            except Exception as exc:  # noqa: BLE001 - driver specific errors
                if attempt == retries:
                    logger.error("database unavailable, giving up", extra=log_extra(error=str(exc)))
                    raise
                logger.warning("database not ready, retrying", extra=log_extra(attempt=attempt, error=str(exc)))
                await asyncio.sleep(delay)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessionmaker() as session:
        yield session
