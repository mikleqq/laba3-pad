from conftest import BASE, auth, client, running
from notification_service.config import Settings
from notification_service.main import create_app
from shopcommon.messaging import InMemoryEventBus


async def test_events_become_notifications():
    bus = InMemoryEventBus()
    app = create_app(Settings(**BASE), bus=bus)
    async with running(app), client(app) as c:
        await bus.publish("user.registered", {"user_id": 1, "email": "a@b.io", "full_name": "Alice"})
        await bus.publish("order.created", {"order_id": 10, "user_id": 1, "total_amount": "12.50"})
        await bus.publish("payment.failed", {"order_id": 10, "user_id": 1, "amount": "12.50", "reason": "declined"})
        await bus.publish("something.else", {"user_id": 1})  # not subscribed / no template
        await bus.join()

        r = await c.get("/notifications", headers=auth(1))
        body = r.json()
        assert body["total"] == 3 and body["unread"] == 3
        subjects = [n["subject"] for n in body["items"]]
        assert "Добро пожаловать в ShopHub!" in subjects
        assert "Заказ №10 принят" in subjects
        assert any("declined" in n["message"] for n in body["items"])

        nid = body["items"][0]["id"]
        assert (await c.post(f"/notifications/{nid}/read", headers=auth(2))).status_code == 404
        assert (await c.post(f"/notifications/{nid}/read", headers=auth(1))).json()["is_read"] is True
        assert (await c.get("/notifications", params={"unread_only": True}, headers=auth(1))).json()["unread"] == 2
        assert (await c.get("/notifications", headers=auth(2))).json()["total"] == 0


async def test_redelivered_event_is_not_duplicated():
    from notification_service.service import NotificationService

    bus = InMemoryEventBus()
    app = create_app(Settings(**BASE), bus=bus)
    async with running(app):
        event = {"event_id": "abc", "event_type": "order.cancelled", "data": {"order_id": 1, "user_id": 1}}
        async with app.state.db.sessionmaker() as session:
            first = await NotificationService(session).handle_event(event)
            second = await NotificationService(session).handle_event(event)
        assert first.id == second.id
