import json
import logging

import httpx
import pytest

from shopcommon.errors import UnauthorizedError, UpstreamError, UpstreamTimeoutError
from shopcommon.http import ServiceClient
from shopcommon.logging import JsonFormatter, log_extra, request_id_var
from shopcommon.messaging import InMemoryEventBus, topic_matches
from shopcommon.security import create_access_token, decode_access_token


@pytest.mark.parametrize(
    "pattern,key,expected",
    [
        ("order.created", "order.created", True),
        ("order.*", "order.created", True),
        ("order.*", "order.created.v2", False),
        ("order.#", "order.created.v2", True),
        ("#", "anything.at.all", True),
        ("payment.*", "order.created", False),
        ("*.created", "order.created", True),
    ],
)
def test_topic_matching(pattern, key, expected):
    assert topic_matches(pattern, key) is expected


def test_jwt_roundtrip_and_tampering():
    tok = create_access_token(user_id=7, email="a@b.io", role="admin", secret="s1-secret-key-long-enough-for-hs256!!")
    user = decode_access_token(tok, "s1-secret-key-long-enough-for-hs256!!")
    assert (user.id, user.email, user.is_admin) == (7, "a@b.io", True)
    with pytest.raises(UnauthorizedError):
        decode_access_token(tok, "other-secret-key-long-enough-for-hs256")
    with pytest.raises(UnauthorizedError):
        decode_access_token("garbage", "s1")


def test_expired_token_rejected():
    tok = create_access_token(user_id=1, email="a@b.io", role="customer", secret="s-secret-key-long-enough-for-hs256!!!", expires_minutes=-1)
    with pytest.raises(UnauthorizedError) as exc:
        decode_access_token(tok, "s-secret-key-long-enough-for-hs256!!!")
    assert exc.value.code == "token_expired"


def test_json_log_format_contains_required_fields():
    token = request_id_var.set("req-123")
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "Order created", None, None)
        record.extra_fields = log_extra(order_id=5)["extra_fields"]
        line = json.loads(JsonFormatter("order-service").format(record))
    finally:
        request_id_var.reset(token)
    assert line["service"] == "order-service"
    assert line["level"] == "INFO"
    assert line["request_id"] == "req-123"
    assert line["message"] == "Order created"
    assert line["order_id"] == 5
    assert "timestamp" in line


async def test_in_memory_bus_delivers_to_matching_subscribers():
    bus = InMemoryEventBus()
    received = []

    async def handler(event):
        received.append(event["event_type"])

    async def failing(event):
        raise RuntimeError("boom")  # must not break other consumers

    await bus.subscribe("q1", ["order.*"], handler, service_name="test")
    await bus.subscribe("q2", ["order.created"], failing, service_name="test")
    await bus.publish("order.created", {"order_id": 1})
    await bus.publish("payment.completed", {"order_id": 1})
    await bus.join()
    assert received == ["order.created"]


async def test_service_client_maps_timeout_and_connection_errors():
    def timeout_handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused_handler(request):
        raise httpx.ConnectError("refused", request=request)

    slow = ServiceClient("slow-service", "http://slow", transport=httpx.MockTransport(timeout_handler))
    with pytest.raises(UpstreamTimeoutError):
        await slow.request("GET", "/x")

    down = ServiceClient("down-service", "http://down", transport=httpx.MockTransport(refused_handler), connect_retries=1)
    with pytest.raises(UpstreamError) as exc:
        await down.request("GET", "/x")
    assert exc.value.status_code == 502


async def test_service_client_propagates_request_id_and_internal_token():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})

    c = ServiceClient("svc", "http://svc", internal_token="tok", transport=httpx.MockTransport(handler))
    token = request_id_var.set("rid-42")
    try:
        await c.request("GET", "/x")
    finally:
        request_id_var.reset(token)
    assert seen["x-request-id"] == "rid-42"
    assert seen["x-internal-token"] == "tok"


async def test_database_connection_failure_returns_503_not_500():
    from conftest import BASE, auth, client, running
    from user_service.config import Settings
    from user_service.main import create_app

    app = create_app(Settings(**BASE))

    @app.get("/db-down")
    async def db_down():
        raise ConnectionRefusedError("connect call failed ('payment-db', 5432)")

    @app.get("/bug")
    async def bug():
        raise ValueError("programming error")

    async with running(app), client(app) as c:
        r = await c.get("/db-down", headers=auth())
        assert r.status_code == 503 and r.json()["error"]["code"] == "database_error"
        r = await c.get("/bug")
        assert r.status_code == 500 and "programming error" not in r.text
