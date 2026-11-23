from conftest import ADMIN, BASE, auth, client, running
from payment_service.config import Settings
from payment_service.main import create_app
from shopcommon.messaging import InMemoryEventBus


def _app(bus):
    return create_app(Settings(**BASE, max_payment_amount=100), bus=bus)


async def test_order_created_is_charged_once():
    bus = InMemoryEventBus()
    app = _app(bus)
    async with running(app), client(app) as c:
        await bus.publish("order.created", {"order_id": 1, "user_id": 7, "total_amount": "50.00"})
        await bus.publish("order.created", {"order_id": 1, "user_id": 7, "total_amount": "50.00"})  # redelivery
        await bus.join()

        completed = bus.events_of("payment.completed")
        assert len(completed) == 1
        assert completed[0]["data"]["transaction_id"].startswith("txn_")

        r = await c.get("/payments/order/1", headers=auth(7))
        assert r.status_code == 200 and r.json()["status"] == "completed" and r.json()["amount"] == 50.0
        assert (await c.get("/payments/order/1", headers=auth(8))).status_code == 404
        assert (await c.get("/payments", headers=ADMIN)).json()["total"] == 1


async def test_amount_over_limit_is_declined():
    bus = InMemoryEventBus()
    app = _app(bus)
    async with running(app), client(app) as c:
        await bus.publish("order.created", {"order_id": 2, "user_id": 7, "total_amount": "500.00"})
        await bus.join()
        failed = bus.events_of("payment.failed")
        assert len(failed) == 1 and "лимит" in failed[0]["data"]["reason"]
        assert (await c.get("/payments/order/2", headers=auth(7))).json()["status"] == "failed"


async def test_cancel_refunds_completed_payment_and_blocks_late_charge():
    bus = InMemoryEventBus()
    app = _app(bus)
    async with running(app), client(app) as c:
        await bus.publish("order.created", {"order_id": 3, "user_id": 7, "total_amount": "10.00"})
        await bus.join()
        await bus.publish("order.cancelled", {"order_id": 3, "user_id": 7, "total_amount": "10.00"})
        await bus.join()
        assert len(bus.events_of("payment.refunded")) == 1
        assert (await c.get("/payments/order/3", headers=auth(7))).json()["status"] == "refunded"

        # cancellation arrives before the order.created event -> never charged
        await bus.publish("order.cancelled", {"order_id": 4, "user_id": 7, "total_amount": "10.00"})
        await bus.join()
        await bus.publish("order.created", {"order_id": 4, "user_id": 7, "total_amount": "10.00"})
        await bus.join()
        assert (await c.get("/payments/order/4", headers=auth(7))).json()["status"] == "cancelled"
        assert len(bus.events_of("payment.completed")) == 1


async def test_ready_reports_broker_and_database():
    bus = InMemoryEventBus()
    app = _app(bus)
    async with running(app), client(app) as c:
        assert (await c.get("/ready")).json()["checks"] == {"database": True, "event_bus": True}
