"""End-to-end business scenarios: the client talks to each service directly (like the web UI)."""

from conftest import ADMIN, DELIVERY


async def _register_and_login(system, email="alice@example.com", password="Secret123!", name="Alice"):
    r = await system["auth"].post("/auth/register", json={"email": email, "password": password, "full_name": name})
    assert r.status_code == 201, r.text
    r = await system["auth"].post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]["id"]


async def _product(system, name, price, stock):
    r = await system["products"].post("/products", json={"name": name, "price": price, "stock": stock}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


async def test_successful_purchase_flow(system):
    headers, user_id = await _register_and_login(system)

    # profile was created synchronously by auth-service -> user-service
    r = await system["users"].get("/users/me", headers=headers)
    assert r.status_code == 200 and r.json()["full_name"] == "Alice"

    product = await _product(system, "Keyboard", 80, 3)

    # REST to product-service (stock), then asynchronous payment through the event bus
    r = await system["orders"].post(
        "/orders", json={"items": [{"product_id": product["id"], "quantity": 2}], "delivery": DELIVERY}, headers=headers
    )
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "pending_payment" and order["total_amount"] == 160.0

    await system.bus.join()  # order.created -> payment -> payment.completed -> order + notifications

    assert (await system["orders"].get(f"/orders/{order['id']}", headers=headers)).json()["status"] == "paid"
    payment = (await system["payments"].get(f"/payments/order/{order['id']}", headers=headers)).json()
    assert payment["status"] == "completed" and payment["amount"] == 160.0
    assert (await system["products"].get(f"/products/{product['id']}")).json()["stock"] == 1

    r = await system["notifications"].get("/notifications", headers=headers)
    types = [n["event_type"] for n in r.json()["items"]]
    assert sorted(types) == ["order.created", "payment.completed", "user.registered"]

    # every event of the purchase carries the request id of the original HTTP call
    order_event = system.bus.events_of("order.created")[0]
    payment_event = system.bus.events_of("payment.completed")[0]
    assert order_event["request_id"] == payment_event["request_id"]


async def test_failed_payment_releases_stock(system):
    headers, _ = await _register_and_login(system, "bob@example.com", name="Bob")
    product = await _product(system, "TV", 900, 5)

    # 2 x 900 = 1800 > payment limit (1000 in the test config)
    r = await system["orders"].post(
        "/orders", json={"items": [{"product_id": product["id"], "quantity": 2}], "delivery": DELIVERY}, headers=headers
    )
    order = r.json()
    await system.bus.join()

    assert (await system["orders"].get(f"/orders/{order['id']}", headers=headers)).json()["status"] == "payment_failed"
    payment = (await system["payments"].get(f"/payments/order/{order['id']}", headers=headers)).json()
    assert payment["status"] == "failed"
    assert (await system["products"].get(f"/products/{product['id']}")).json()["stock"] == 5

    notes = (await system["notifications"].get("/notifications", headers=headers)).json()["items"]
    assert any(n["event_type"] == "payment.failed" for n in notes)


async def test_cancel_paid_order_refunds(system):
    headers, _ = await _register_and_login(system, "carol@example.com", name="Carol")
    product = await _product(system, "Mug", 10, 10)
    order = (
        await system["orders"].post(
            "/orders", json={"items": [{"product_id": product["id"], "quantity": 1}], "delivery": DELIVERY}, headers=headers
        )
    ).json()
    await system.bus.join()

    r = await system["orders"].post(f"/orders/{order['id']}/cancel", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    await system.bus.join()

    payment = (await system["payments"].get(f"/payments/order/{order['id']}", headers=headers)).json()
    assert payment["status"] == "refunded"
    assert (await system["products"].get(f"/products/{product['id']}")).json()["stock"] == 10


async def test_users_cannot_see_each_others_data(system):
    alice, _ = await _register_and_login(system, "alice2@example.com", name="Alice")
    eve, eve_id = await _register_and_login(system, "eve@example.com", name="Eve")
    product = await _product(system, "Pen", 1, 10)
    order = (
        await system["orders"].post(
            "/orders", json={"items": [{"product_id": product["id"], "quantity": 1}], "delivery": DELIVERY}, headers=alice
        )
    ).json()
    await system.bus.join()

    assert (await system["orders"].get(f"/orders/{order['id']}", headers=eve)).status_code == 404
    assert (await system["payments"].get(f"/payments/order/{order['id']}", headers=eve)).status_code == 404
    assert (await system["users"].get("/users", headers=eve)).status_code == 403
    assert (await system["users"].get(f"/users/{eve_id}", headers=eve)).status_code == 200


async def test_cors_allows_the_web_ui(system):
    r = await system["products"].options(
        "/products",
        headers={"Origin": "null", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
