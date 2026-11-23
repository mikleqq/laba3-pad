from conftest import ADMIN, BASE, INTERNAL, auth, client, running
from product_service.config import Settings
from product_service.main import create_app
from shopcommon.messaging import InMemoryEventBus


def _app(seed=False, bus=None):
    return create_app(Settings(**BASE, seed_demo_data=seed), bus=bus or InMemoryEventBus())


async def test_seeded_catalog_is_public_and_searchable():
    app = _app(seed=True)
    async with running(app), client(app) as c:
        r = await c.get("/products")
        assert r.status_code == 200 and r.json()["total"] == 16
        r = await c.get("/products", params={"search": "клавиатура"})
        assert [p["name"] for p in r.json()["items"]] == ["Механическая клавиатура Keyforge"]
        r = await c.get("/products", params={"category": "tv"})
        assert r.json()["total"] == 1
        assert (await c.get("/products/9999")).status_code == 404


async def test_admin_crud_and_customer_forbidden():
    app = _app()
    async with running(app), client(app) as c:
        body = {"name": "Lamp", "price": 19.99, "stock": 5, "category": "home"}
        assert (await c.post("/products", json=body)).status_code == 401
        assert (await c.post("/products", json=body, headers=auth())).status_code == 403

        r = await c.post("/products", json=body, headers=ADMIN)
        assert r.status_code == 201
        product = r.json()
        assert product["price"] == 19.99

        r = await c.patch(f"/products/{product['id']}", json={"price": 17.5}, headers=ADMIN)
        assert r.json()["price"] == 17.5
        assert (await c.post("/products", json={**body, "price": -1}, headers=ADMIN)).status_code == 422

        assert (await c.delete(f"/products/{product['id']}", headers=ADMIN)).status_code == 204
        assert (await c.get(f"/products/{product['id']}")).status_code == 404


async def test_stock_reservation_is_atomic():
    bus = InMemoryEventBus()
    app = _app(bus=bus)
    async with running(app), client(app) as c:
        a = (await c.post("/products", json={"name": "Item A", "price": 10, "stock": 5}, headers=ADMIN)).json()
        b = (await c.post("/products", json={"name": "Item B", "price": 20, "stock": 1}, headers=ADMIN)).json()

        # B has not enough stock -> nothing reserved at all
        r = await c.post(
            "/internal/stock/reserve",
            json={"items": [{"product_id": a["id"], "quantity": 2}, {"product_id": b["id"], "quantity": 2}]},
            headers=INTERNAL,
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "insufficient_stock"
        assert (await c.get(f"/products/{a['id']}")).json()["stock"] == 5

        # duplicates are merged: 2 + 1 = 3
        r = await c.post(
            "/internal/stock/reserve",
            json={"items": [{"product_id": a["id"], "quantity": 2}, {"product_id": a["id"], "quantity": 1}]},
            headers=INTERNAL,
        )
        assert r.status_code == 200
        assert r.json()["items"] == [{"product_id": a["id"], "name": "Item A", "unit_price": 10.0, "quantity": 3}]
        assert (await c.get(f"/products/{a['id']}")).json()["stock"] == 2
        assert bus.events_of("product.low_stock")[0]["data"]["stock"] == 2

        r = await c.post("/internal/stock/release", json={"items": [{"product_id": a["id"], "quantity": 3}]}, headers=INTERNAL)
        assert r.status_code == 204
        assert (await c.get(f"/products/{a['id']}")).json()["stock"] == 5

        r = await c.post("/internal/stock/reserve", json={"items": [{"product_id": 999, "quantity": 1}]}, headers=INTERNAL)
        assert r.status_code == 404
        r = await c.post("/internal/stock/reserve", json={"items": [{"product_id": a["id"], "quantity": 1}]})
        assert r.status_code == 403
