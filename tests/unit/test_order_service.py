import httpx

from conftest import ADMIN, BASE, DELIVERY, auth, client, running, transport
from order_service.config import Settings as OrderSettings
from order_service.main import create_app as order_app
from product_service.config import Settings as ProductSettings
from product_service.main import create_app as product_app
from shopcommon.messaging import InMemoryEventBus


async def _setup():
    bus = InMemoryEventBus()
    products = product_app(ProductSettings(**BASE, seed_demo_data=False), bus=bus)
    orders = order_app(OrderSettings(**BASE), bus=bus, product_transport=transport(products))
    return bus, products, orders


async def test_create_order_reserves_stock_and_publishes_event():
    bus, products, orders = await _setup()
    async with running(products, orders), client(products) as pc, client(orders) as oc:
        p = (await pc.post("/products", json={"name": "Pen", "price": 2.5, "stock": 10}, headers=ADMIN)).json()

        r = await oc.post("/orders", json={"items": [{"product_id": p["id"], "quantity": 4}], "delivery": DELIVERY}, headers=auth(1))
        assert r.status_code == 201, r.text
        order = r.json()
        assert order["status"] == "pending_payment"
        assert order["total_amount"] == 10.0
        assert order["items"][0]["product_name"] == "Pen"
        assert (await pc.get(f"/products/{p['id']}")).json()["stock"] == 6

        event = bus.events_of("order.created")[0]
        assert event["data"]["order_id"] == order["id"] and event["data"]["total_amount"] == "10.00"
        # request id of the HTTP request travels with the event
        assert event["request_id"] == r.headers["x-request-id"]


async def test_order_errors_from_product_service_are_propagated():
    bus, products, orders = await _setup()
    async with running(products, orders), client(products) as pc, client(orders) as oc:
        p = (await pc.post("/products", json={"name": "Pen", "price": 2.5, "stock": 1}, headers=ADMIN)).json()
        r = await oc.post("/orders", json={"items": [{"product_id": p["id"], "quantity": 5}], "delivery": DELIVERY}, headers=auth(1))
        assert r.status_code == 409
        r = await oc.post("/orders", json={"items": [{"product_id": 12345, "quantity": 1}], "delivery": DELIVERY}, headers=auth(1))
        assert r.status_code == 404
        r = await oc.post("/orders", json={"items": [], "delivery": DELIVERY}, headers=auth(1))
        assert r.status_code == 422
        assert (await oc.post("/orders", json={"items": [{"product_id": 1, "quantity": 1}], "delivery": DELIVERY})).status_code == 401
        assert not bus.events_of("order.created")


async def test_product_service_timeout_returns_504():
    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ReadTimeout("timeout", request=request)

    orders = order_app(OrderSettings(**BASE, http_timeout_seconds=0.1), bus=InMemoryEventBus(), product_transport=SlowTransport())
    async with running(orders), client(orders) as oc:
        r = await oc.post("/orders", json={"items": [{"product_id": 1, "quantity": 1}], "delivery": DELIVERY}, headers=auth(1))
        assert r.status_code == 504
        assert r.json()["error"]["code"] == "upstream_timeout"


async def test_payment_events_update_order_status_and_release_stock():
    bus, products, orders = await _setup()
    async with running(products, orders), client(products) as pc, client(orders) as oc:
        p = (await pc.post("/products", json={"name": "Pen", "price": 1, "stock": 10}, headers=ADMIN)).json()
        o1 = (await oc.post("/orders", json={"items": [{"product_id": p["id"], "quantity": 3}], "delivery": DELIVERY}, headers=auth(1))).json()
        o2 = (await oc.post("/orders", json={"items": [{"product_id": p["id"], "quantity": 2}], "delivery": DELIVERY}, headers=auth(1))).json()
        assert (await pc.get(f"/products/{p['id']}")).json()["stock"] == 5

        await bus.publish("payment.completed", {"order_id": o1["id"], "user_id": 1, "amount": "3.00"})
        await bus.publish("payment.failed", {"order_id": o2["id"], "user_id": 1, "amount": "2.00", "reason": "declined"})
        await bus.join()

        assert (await oc.get(f"/orders/{o1['id']}", headers=auth(1))).json()["status"] == "paid"
        assert (await oc.get(f"/orders/{o2['id']}", headers=auth(1))).json()["status"] == "payment_failed"
        assert (await pc.get(f"/products/{p['id']}")).json()["stock"] == 7  # o2 stock returned

        # duplicate delivery is harmless
        await bus.publish("payment.failed", {"order_id": o2["id"], "user_id": 1, "amount": "2.00"})
        await bus.join()
        assert (await pc.get(f"/products/{p['id']}")).json()["stock"] == 7


async def test_ownership_cancel_and_listing():
    bus, products, orders = await _setup()
    async with running(products, orders), client(products) as pc, client(orders) as oc:
        p = (await pc.post("/products", json={"name": "Pen", "price": 1, "stock": 10}, headers=ADMIN)).json()
        o = (await oc.post("/orders", json={"items": [{"product_id": p["id"], "quantity": 4}], "delivery": DELIVERY}, headers=auth(1))).json()

        assert (await oc.get(f"/orders/{o['id']}", headers=auth(2))).status_code == 404  # other user
        assert (await oc.get(f"/orders/{o['id']}", headers=ADMIN)).status_code == 200
        assert (await oc.get("/orders", headers=auth(2))).json()["total"] == 0
        assert (await oc.get("/orders", headers=ADMIN)).json()["total"] == 1

        r = await oc.post(f"/orders/{o['id']}/cancel", headers=auth(1))
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert (await pc.get(f"/products/{p['id']}")).json()["stock"] == 10
        assert bus.events_of("order.cancelled")[0]["data"]["order_id"] == o["id"]
        assert (await oc.post(f"/orders/{o['id']}/cancel", headers=auth(1))).status_code == 409
