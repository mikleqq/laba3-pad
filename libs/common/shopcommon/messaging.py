"""Asynchronous messaging between services, implemented on top of PostgreSQL.

There is no separate message broker: the single ShopHub database holds an
append-only event table ``bus.events``. A producer INSERTs an event; every
consumer (one per service) reads new events in order and stores how far it got
in ``bus.consumer_offsets``. While a consumer is down, events simply stay in
the table and are processed after it restarts.

Event envelope::

    {"event_id": "...", "event_type": "order.created", "occurred_at": "...",
     "request_id": "...", "data": {...}}

Delivery: at-least-once, in order. A failing handler is retried every
``retry_delay`` seconds; after ``max_attempts`` the event is recorded in
``bus.dead_letters`` and skipped. Consumers are written to be idempotent.

``InMemoryEventBus`` has the same semantics and is used in tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .logging import log_extra, request_id_var, service_var

logger = logging.getLogger("shopcommon.messaging")

Event = dict[str, Any]
Handler = Callable[[Event], Awaitable[None]]


def make_event(event_type: str, data: dict[str, Any]) -> Event:
    return {
        "event_id": uuid.uuid4().hex,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id_var.get(),
        "data": data,
    }


def topic_matches(pattern: str, key: str) -> bool:
    """AMQP topic matching: ``*`` = exactly one word, ``#`` = zero or more words."""
    p, k = pattern.split("."), key.split(".")

    def match(i: int, j: int) -> bool:
        if i == len(p):
            return j == len(k)
        if p[i] == "#":
            return any(match(i + 1, jj) for jj in range(j, len(k) + 1))
        if j == len(k):
            return False
        return p[i] in ("*", k[j]) and match(i + 1, j + 1)

    return match(0, 0)


async def run_handler(handler: Handler, event: Event, service_name: str) -> bool:
    rid = request_id_var.set(event.get("request_id") or uuid.uuid4().hex)
    svc = service_var.set(service_name)
    try:
        logger.info(
            "event received", extra=log_extra(event_type=event.get("event_type"), event_id=event.get("event_id"))
        )
        await handler(event)
        return True
    except Exception:  # noqa: BLE001
        logger.exception(
            "event handler failed", extra=log_extra(event_type=event.get("event_type"), event_id=event.get("event_id"))
        )
        return False
    finally:
        request_id_var.reset(rid)
        service_var.reset(svc)


class EventBus(ABC):
    @abstractmethod
    async def connect(self, retries: int = 30, delay: float = 2.0) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def publish(self, event_type: str, data: dict[str, Any]) -> Event: ...

    @abstractmethod
    async def subscribe(self, queue_name: str, routing_keys: list[str], handler: Handler, *, service_name: str) -> None: ...

    @abstractmethod
    async def is_healthy(self) -> bool: ...


async def safe_publish(bus: EventBus, event_type: str, data: dict[str, Any]) -> Optional[Event]:
    """Publish without failing the caller's already-committed operation."""
    try:
        return await bus.publish(event_type, data)
    except Exception:  # noqa: BLE001
        logger.exception("event publish failed", extra=log_extra(event_type=event_type))
        return None


class InMemoryEventBus(EventBus):
    """Process-local bus with the same semantics (topics, async delivery). Used in tests."""

    def __init__(self) -> None:
        # (queue, routing keys, handler, service, lock). The lock makes delivery to one
        # queue sequential and FIFO, like a single consumer of bus.events.
        self.subscriptions: list[tuple[str, list[str], Handler, str, asyncio.Lock]] = []
        self.published: list[Event] = []
        self._tasks: set[asyncio.Task] = set()

    async def connect(self, retries: int = 30, delay: float = 2.0) -> None:
        return None

    async def close(self) -> None:
        await self.join()

    async def publish(self, event_type: str, data: dict[str, Any]) -> Event:
        # Round-trip through JSON to behave exactly like a real broker.
        event = json.loads(json.dumps(make_event(event_type, data), default=str))
        self.published.append(event)
        logger.info("event published", extra=log_extra(event_type=event_type, event_id=event["event_id"]))
        for _queue, keys, handler, service_name, lock in self.subscriptions:
            if any(topic_matches(k, event_type) for k in keys):
                task = asyncio.create_task(self._deliver(lock, handler, event, service_name))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return event

    async def subscribe(self, queue_name: str, routing_keys: list[str], handler: Handler, *, service_name: str) -> None:
        self.subscriptions.append((queue_name, list(routing_keys), handler, service_name, asyncio.Lock()))

    @staticmethod
    async def _deliver(lock: asyncio.Lock, handler: Handler, event: Event, service_name: str) -> None:
        async with lock:
            await run_handler(handler, event, service_name)

    async def is_healthy(self) -> bool:
        return True

    async def join(self) -> None:
        """Wait until all (transitively triggered) handlers have finished."""
        while True:
            pending = [t for t in self._tasks if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    def events_of(self, event_type: str) -> list[Event]:
        return [e for e in self.published if e["event_type"] == event_type]


class PostgresEventBus(EventBus):
    # Publishers serialize their INSERTs with this advisory lock so that event ids
    # become visible in commit order and a consumer can never skip an event.
    _PUBLISH_LOCK = 72_017

    def __init__(
        self,
        url: str,
        *,
        service_name: str,
        poll_interval: float = 0.5,
        retry_delay: float = 3.0,
        max_attempts: int = 40,
        batch_size: int = 100,
    ) -> None:
        self.url = url
        self.service_name = service_name
        self.poll_interval = poll_interval
        self.retry_delay = retry_delay
        self.max_attempts = max_attempts
        self.batch_size = batch_size
        self._engine = None
        self._tasks: list[asyncio.Task] = []

    async def connect(self, retries: int = 30, delay: float = 2.0) -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        self._engine = create_async_engine(self.url, pool_size=3, max_overflow=2, pool_pre_ping=True)
        for attempt in range(1, retries + 1):
            try:
                async with self._engine.connect() as conn:
                    await conn.execute(text("SELECT 1 FROM bus.events LIMIT 1"))
                logger.info("event bus ready (PostgreSQL bus.events)")
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == retries:
                    logger.error("event bus unavailable, giving up", extra=log_extra(error=str(exc)))
                    raise
                logger.warning("event bus not ready, retrying", extra=log_extra(attempt=attempt, error=str(exc)))
                await asyncio.sleep(delay)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._engine is not None:
            await self._engine.dispose()

    async def publish(self, event_type: str, data: dict[str, Any]) -> Event:
        from sqlalchemy import text

        event = make_event(event_type, data)
        async with self._engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": self._PUBLISH_LOCK})
            await conn.execute(
                text(
                    "INSERT INTO bus.events (event_id, event_type, producer, payload) "
                    "VALUES (:event_id, :event_type, :producer, CAST(:payload AS jsonb))"
                ),
                {
                    "event_id": event["event_id"],
                    "event_type": event_type,
                    "producer": self.service_name,
                    "payload": json.dumps(event, default=str),
                },
            )
        logger.info("event published", extra=log_extra(event_type=event_type, event_id=event["event_id"]))
        return event

    async def subscribe(self, queue_name: str, routing_keys: list[str], handler: Handler, *, service_name: str) -> None:
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO bus.consumer_offsets (consumer) VALUES (:c) ON CONFLICT (consumer) DO NOTHING"),
                {"c": queue_name},
            )
        self._tasks.append(asyncio.create_task(self._consume(queue_name, list(routing_keys), handler, service_name)))
        logger.info("subscribed", extra=log_extra(consumer=queue_name, routing_keys=routing_keys))

    async def _consume(self, consumer: str, keys: list[str], handler: Handler, service_name: str) -> None:
        from sqlalchemy import text

        attempts = 0
        while True:
            try:
                async with self._engine.connect() as conn:
                    offset = (
                        await conn.execute(
                            text("SELECT last_event_id FROM bus.consumer_offsets WHERE consumer = :c"), {"c": consumer}
                        )
                    ).scalar_one()
                    rows = (
                        await conn.execute(
                            text("SELECT id, event_type, payload FROM bus.events WHERE id > :o ORDER BY id LIMIT :n"),
                            {"o": offset, "n": self.batch_size},
                        )
                    ).all()
                if not rows:
                    await asyncio.sleep(self.poll_interval)
                    continue

                for row in rows:
                    if any(topic_matches(k, row.event_type) for k in keys):
                        payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
                        if not await run_handler(handler, payload, service_name):
                            attempts += 1
                            if attempts < self.max_attempts:
                                logger.warning(
                                    "event will be retried",
                                    extra=log_extra(consumer=consumer, event=row.id, attempts=attempts),
                                )
                                await asyncio.sleep(self.retry_delay)
                                break  # re-read from the same offset
                            await self._dead_letter(consumer, row.id, "handler failed")
                    attempts = 0
                    await self._advance(consumer, row.id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - database temporarily unavailable
                logger.warning("event bus unavailable, retrying", extra=log_extra(consumer=consumer, error=str(exc)))
                await asyncio.sleep(self.retry_delay)

    async def _advance(self, consumer: str, event_id: int) -> None:
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            await conn.execute(
                text("UPDATE bus.consumer_offsets SET last_event_id = :id, updated_at = now() WHERE consumer = :c"),
                {"id": event_id, "c": consumer},
            )

    async def _dead_letter(self, consumer: str, event_id: int, error: str) -> None:
        from sqlalchemy import text

        logger.error("event moved to dead letters", extra=log_extra(consumer=consumer, event=event_id))
        async with self._engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO bus.dead_letters (consumer, event_id, error) VALUES (:c, :e, :err)"),
                {"c": consumer, "e": event_id, "err": error},
            )

    async def is_healthy(self) -> bool:
        from sqlalchemy import text

        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False
