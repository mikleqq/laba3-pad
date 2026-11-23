"""End-to-end test through the API Gateway: works for Docker Compose and for Kubernetes.

    docker compose up -d --build            ->  pytest -m e2e                       (http://localhost:8080)
    minikube tunnel / port-forward          ->  $env:SHOPHUB_URL="http://..."; pytest -m e2e

Skipped automatically when the gateway is not reachable.
"""

import os
import time
import uuid

import httpx
import pytest

BASE_URL = os.getenv("SHOPHUB_URL", "http://localhost:8080").rstrip("/")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
DELIVERY = {"recipient": "Тест Тестов", "phone": "+373 69 123 456", "address": "Кишинёв, ул. Пушкина, 10"}
SERVICES = ["auth", "user", "product", "order", "payment", "notification"]

pytestmark = pytest.mark.e2e


def _reachable() -> bool:
    try:
        return httpx.get(f"{BASE_URL}/healthz", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module")
def http():
    if not _reachable():
        pytest.skip(f"gateway is not reachable at {BASE_URL}")
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        yield client


def _wait_for(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("condition not met in time")


def _register(http) -> dict[str, str]:
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    r = http.post("/auth/register", json={"email": email, "password": "Secret123", "full_name": "Тест Тестов"})
    assert r.status_code == 201, r.text
    r = http.post("/auth/login", json={"email": email, "password": "Secret123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_every_service_is_healthy_through_the_gateway(http):
    for name in SERVICES:
        assert http.get(f"/status/{name}").status_code == 200, name


def test_internal_endpoints_are_not_exposed(http):
    assert http.post("/internal/stock/reserve", json={}).status_code == 404


def test_request_id_is_returned(http):
    r = http.get("/products?limit=1", headers={"X-Request-ID": "e2e-trace-1"})
    assert r.headers["x-request-id"] == "e2e-trace-1"


def test_purchase_flow(http):
    user = _register(http)
    product = _wait_for(lambda: next((p for p in http.get("/products?limit=100").json()["items"] if p["stock"] > 2), None))

    r = http.post("/orders", json={"items": [{"product_id": product["id"], "quantity": 1}], "delivery": DELIVERY}, headers=user)
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "pending_payment"

    _wait_for(lambda: http.get(f"/orders/{order['id']}", headers=user).json()["status"] == "paid")
    assert http.get(f"/payments/order/{order['id']}", headers=user).json()["status"] == "completed"
    notes = _wait_for(lambda: (n := http.get("/notifications", headers=user).json())["total"] >= 3 and n)
    assert {"user.registered", "order.created", "payment.completed"} <= {i["event_type"] for i in notes["items"]}


def test_validation_errors_come_back_per_field(http):
    r = http.post("/auth/register", json={"email": "bad", "password": "12345678", "full_name": "X1"})
    assert r.status_code == 422
    assert {d["field"] for d in r.json()["error"]["details"]} == {"email", "password", "full_name"}


@pytest.mark.skipif(not ADMIN_PASSWORD, reason="set ADMIN_PASSWORD to test the admin account")
def test_admin_can_manage_catalog(http):
    r = http.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = http.post("/products", json={"name": "E2E товар", "price": 10, "stock": 3, "category": "accessories"}, headers=admin)
    assert r.status_code == 201, r.text
    assert http.delete(f"/products/{r.json()['id']}", headers=admin).status_code == 204
