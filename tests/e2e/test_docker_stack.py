"""Smoke test against the real stack: 6 service containers + the PostgreSQL database.

    docker compose up -d --build
    pytest -m e2e

Skipped automatically when the services are not reachable.
"""

import os
import time
import uuid

import httpx
import pytest

HOST = os.getenv("SHOPHUB_HOST", "http://localhost")
PORTS = {"auth": 8001, "users": 8002, "products": 8003, "orders": 8004, "payments": 8005, "notifications": 8006}
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin12345!")

pytestmark = pytest.mark.e2e

DELIVERY = {"recipient": "Тест Тестов", "phone": "+373 69 123 456", "address": "Кишинёв, ул. Пушкина, 10"}


def url(service: str, path: str) -> str:
    return f"{HOST}:{PORTS[service]}{path}"


def _reachable() -> bool:
    try:
        return all(httpx.get(url(s, "/ready"), timeout=2).status_code == 200 for s in PORTS)
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module")
def http():
    if not _reachable():
        pytest.skip("the 6 services are not running")
    with httpx.Client(timeout=10) as c:
        yield c


def _login(http, email, password):
    r = http.post(url("auth", "/auth/login"), json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _wait_for(predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("condition not met in time")


def test_purchase_through_postgres_event_bus(http):
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    r = http.post(url("auth", "/auth/register"), json={"email": email, "password": "Secret123!", "full_name": "Тест Тестов"})
    assert r.status_code == 201, r.text
    user = _login(http, email, "Secret123!")
    admin = _login(http, ADMIN_EMAIL, ADMIN_PASSWORD)

    product = http.post(url("products", "/products"), json={"name": "E2E item", "price": 12.5, "stock": 5}, headers=admin).json()
    order = http.post(
        url("orders", "/orders"), json={"items": [{"product_id": product["id"], "quantity": 2}], "delivery": DELIVERY}, headers=user
    ).json()
    assert order["status"] == "pending_payment"

    _wait_for(lambda: http.get(url("orders", f"/orders/{order['id']}"), headers=user).json()["status"] == "paid")
    payment = http.get(url("payments", f"/payments/order/{order['id']}"), headers=user).json()
    assert payment["status"] == "completed"
    assert http.get(url("products", f"/products/{product['id']}")).json()["stock"] == 3

    notes = _wait_for(
        lambda: (n := http.get(url("notifications", "/notifications"), headers=user).json())["total"] >= 3 and n
    )
    assert {"user.registered", "order.created", "payment.completed"} <= {i["event_type"] for i in notes["items"]}
