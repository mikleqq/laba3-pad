from conftest import ADMIN, BASE, INTERNAL, auth, client, running
from user_service.config import Settings
from user_service.main import create_app


async def _create(c, uid=1, email="alice@example.com"):
    return await c.post("/internal/users", json={"id": uid, "email": email, "full_name": "Alice"}, headers=INTERNAL)


async def test_internal_endpoint_requires_token():
    app = create_app(Settings(**BASE))
    async with running(app), client(app) as c:
        r = await c.post("/internal/users", json={"id": 1, "email": "a@b.io", "full_name": "A"})
        assert r.status_code == 403
        r = await c.post(
            "/internal/users", json={"id": 1, "email": "a@b.io", "full_name": "A"}, headers={"X-Internal-Token": "wrong"}
        )
        assert r.status_code == 403


async def test_create_is_idempotent_and_detects_conflicts():
    app = create_app(Settings(**BASE))
    async with running(app), client(app) as c:
        assert (await _create(c)).status_code == 201
        assert (await _create(c)).status_code == 200  # same call again
        assert (await _create(c, uid=2)).status_code == 409  # email already used


async def test_profile_read_update_and_access_rules():
    app = create_app(Settings(**BASE))
    async with running(app), client(app) as c:
        await _create(c, 1, "alice@example.com")
        await _create(c, 2, "bob@example.com")

        r = await c.get("/users/me", headers=auth(1))
        assert r.status_code == 200 and r.json()["full_name"] == "Alice"

        r = await c.patch("/users/me", json={"phone": "+373 69 123456", "address": "Chisinau"}, headers=auth(1))
        assert r.status_code == 200 and r.json()["address"] == "Chisinau"

        assert (await c.patch("/users/me", json={"phone": "abc"}, headers=auth(1))).status_code == 422
        assert (await c.patch("/users/me", json={}, headers=auth(1))).status_code == 400

        assert (await c.get("/users/2", headers=auth(1))).status_code == 403  # someone else's profile
        assert (await c.get("/users/2", headers=ADMIN)).status_code == 200
        assert (await c.get("/users/42", headers=ADMIN)).status_code == 404

        assert (await c.get("/users", headers=auth(1))).status_code == 403
        r = await c.get("/users", headers=ADMIN)
        assert r.json()["total"] == 2


async def test_unauthenticated_and_bad_token():
    app = create_app(Settings(**BASE))
    async with running(app), client(app) as c:
        r = await c.get("/users/me")
        assert r.status_code == 401
        r = await c.get("/users/me", headers={"Authorization": "Bearer invalid"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_token"


async def test_health_and_ready():
    app = create_app(Settings(**BASE))
    async with running(app), client(app) as c:
        assert (await c.get("/health")).json()["status"] == "ok"
        r = await c.get("/ready")
        assert r.status_code == 200
        assert r.json()["checks"] == {"database": True}
        assert "x-request-id" in r.headers
