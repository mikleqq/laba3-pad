import httpx

from auth_service.config import Settings
from auth_service.main import create_app
from auth_service.passwords import hash_password, verify_password
from conftest import BASE, client, running
from shopcommon.messaging import InMemoryEventBus
from shopcommon.security import decode_access_token


def test_password_hashing():
    hashed = hash_password("Secret123!", iterations=1000)
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("Secret123!", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("Secret123!", "not-a-hash")


def _fake_user_service(status: int = 201):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if status >= 500:
            return httpx.Response(status, json={"error": {"code": "internal_error", "message": "boom"}})
        return httpx.Response(status, json={})

    return httpx.MockTransport(handler), calls


def _app(user_transport, bus=None):
    return create_app(
        Settings(**BASE, password_hash_iterations=1000),
        bus=bus or InMemoryEventBus(),
        user_transport=user_transport,
        bootstrap_admin=False,
    )


async def test_register_login_and_me():
    fake, calls = _fake_user_service()
    bus = InMemoryEventBus()
    app = _app(fake, bus)
    async with running(app), client(app) as c:
        r = await c.post("/auth/register", json={"email": "Alice@Example.com", "password": "Secret123!", "full_name": "Alice"})
        assert r.status_code == 201, r.text
        assert r.json()["email"] == "alice@example.com"
        assert r.json()["role"] == "customer"
        # user-service called with the internal token
        assert calls[0].headers["x-internal-token"] == BASE["internal_token"]
        assert bus.events_of("user.registered")[0]["data"]["email"] == "alice@example.com"

        r = await c.post("/auth/login", json={"email": "alice@example.com", "password": "Secret123!"})
        assert r.status_code == 200
        tok = r.json()["access_token"]
        assert decode_access_token(tok, BASE["jwt_secret"]).email == "alice@example.com"

        r = await c.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.json()["email"] == "alice@example.com"


async def test_duplicate_email_conflict():
    fake, _ = _fake_user_service()
    app = _app(fake)
    async with running(app), client(app) as c:
        body = {"email": "bob@example.com", "password": "Secret123!", "full_name": "Bob"}
        assert (await c.post("/auth/register", json=body)).status_code == 201
        r = await c.post("/auth/register", json=body)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "email_taken"


async def test_invalid_input_returns_structured_422():
    fake, _ = _fake_user_service()
    app = _app(fake)
    async with running(app), client(app) as c:
        r = await c.post("/auth/register", json={"email": "not-an-email", "password": "123", "full_name": ""})
        assert r.status_code == 422
        err = r.json()["error"]
        assert err["code"] == "validation_error"
        assert {d["field"] for d in err["details"]} == {"email", "password", "full_name"}
        assert "request_id" in err


async def test_wrong_password_401():
    fake, _ = _fake_user_service()
    app = _app(fake)
    async with running(app), client(app) as c:
        await c.post("/auth/register", json={"email": "c@example.com", "password": "Secret123!", "full_name": "Cecil"})
        r = await c.post("/auth/login", json={"email": "c@example.com", "password": "nope-nope"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_credentials"


async def test_registration_rolled_back_when_user_service_fails():
    fake, _ = _fake_user_service(status=500)
    app = _app(fake)
    async with running(app), client(app) as c:
        body = {"email": "d@example.com", "password": "Secret123!", "full_name": "Dora"}
        r = await c.post("/auth/register", json=body)
        assert r.status_code == 502
        assert "Traceback" not in r.text
        # nothing persisted -> login impossible
        r = await c.post("/auth/login", json={"email": "d@example.com", "password": "Secret123!"})
        assert r.status_code == 401


async def test_registration_when_user_service_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    app = _app(httpx.MockTransport(refuse))
    async with running(app), client(app) as c:
        r = await c.post("/auth/register", json={"email": "e@example.com", "password": "Secret123!", "full_name": "Emil"})
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "upstream_unavailable"


async def test_bootstrap_admin_created():
    fake, _ = _fake_user_service()
    app = create_app(Settings(**BASE, password_hash_iterations=1000), bus=InMemoryEventBus(), user_transport=fake)
    async with running(app), client(app) as c:
        await app.state.admin_task
        r = await c.post("/auth/login", json={"email": "admin@example.com", "password": "Admin12345!"})
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "admin"
