"""Test fixtures.

Every service runs in-process with its own in-memory SQLite database. Services
talk to each other through ``httpx.ASGITransport`` (real HTTP semantics, no
network) and through a shared ``InMemoryEventBus`` (same topic semantics as
the PostgreSQL event bus). This lets us test the real inter-service flows without Docker.
"""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest

from shopcommon.messaging import InMemoryEventBus
from shopcommon.security import create_access_token

JWT_SECRET = "test-jwt-secret-that-is-long-enough-123"
INTERNAL_TOKEN = "test-internal-token"
SQLITE = "sqlite+aiosqlite:///:memory:"

BASE = dict(jwt_secret=JWT_SECRET, internal_token=INTERNAL_TOKEN, database_url=SQLITE, startup_retries=1)


def transport(app) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app, raise_app_exceptions=False)


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport(app), base_url="http://testserver")


def token(user_id: int = 1, email: str = "alice@example.com", role: str = "customer") -> str:
    return create_access_token(user_id=user_id, email=email, role=role, secret=JWT_SECRET)


def auth(user_id: int = 1, email: str = "alice@example.com", role: str = "customer") -> dict[str, str]:
    return {"Authorization": f"Bearer {token(user_id, email, role)}"}


ADMIN = auth(999, "admin@example.com", "admin")
DELIVERY = {"recipient": "Алиса Иванова", "phone": "+373 69 123 456", "address": "Кишинёв, ул. Пушкина, 10"}
INTERNAL = {"X-Internal-Token": INTERNAL_TOKEN}


@asynccontextmanager
async def running(*apps):
    """Run the lifespan (startup/shutdown) of the given apps."""
    async with AsyncExitStack() as stack:
        for app in apps:
            await stack.enter_async_context(app.router.lifespan_context(app))
        yield


@dataclass
class System:
    bus: InMemoryEventBus
    apps: dict = field(default_factory=dict)
    clients: dict = field(default_factory=dict)

    def __getitem__(self, name: str) -> httpx.AsyncClient:
        return self.clients[name]


@pytest.fixture
async def system():
    """The whole platform (6 services) wired together in-process."""
    from auth_service.config import Settings as AuthSettings
    from auth_service.main import create_app as auth_app
    from notification_service.config import Settings as NotificationSettings
    from notification_service.main import create_app as notification_app
    from order_service.config import Settings as OrderSettings
    from order_service.main import create_app as order_app
    from payment_service.config import Settings as PaymentSettings
    from payment_service.main import create_app as payment_app
    from product_service.config import Settings as ProductSettings
    from product_service.main import create_app as product_app
    from user_service.config import Settings as UserSettings
    from user_service.main import create_app as user_app

    bus = InMemoryEventBus()
    apps = {}
    apps["users"] = user_app(UserSettings(**BASE))
    apps["products"] = product_app(ProductSettings(**BASE, seed_demo_data=False), bus=bus)
    apps["payments"] = payment_app(PaymentSettings(**BASE, max_payment_amount=1000), bus=bus)
    apps["notifications"] = notification_app(NotificationSettings(**BASE), bus=bus)
    apps["auth"] = auth_app(
        AuthSettings(**BASE, password_hash_iterations=1000),
        bus=bus,
        user_transport=transport(apps["users"]),
        bootstrap_admin=False,
    )
    apps["orders"] = order_app(OrderSettings(**BASE), bus=bus, product_transport=transport(apps["products"]))
    async with running(*apps.values()):
        clients = {name: client(app) for name, app in apps.items()}
        try:
            yield System(bus=bus, apps=apps, clients=clients)
        finally:
            await bus.join()
            for c in clients.values():
                await c.aclose()
