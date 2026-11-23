"""Server-side validation: every service rejects bad input with field-level Russian messages."""

import pytest

from conftest import ADMIN, BASE, DELIVERY, INTERNAL, auth, client, running, transport
from shopcommon.messaging import InMemoryEventBus
from shopcommon.validation import validate_address, validate_password, validate_person_name, validate_phone


def _fields(response) -> dict[str, str]:
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    return {d["field"]: d["message"] for d in body["details"]}


@pytest.mark.parametrize("value", ["Анна", "Анна-Мария Иванова", "John O'Neil", "  Пётр   Петров  "])
def test_valid_names(value):
    assert validate_person_name(value.strip())


@pytest.mark.parametrize("value", ["A", "Анна1", "<script>", "-Анна", "x" * 101])
def test_invalid_names(value):
    with pytest.raises(ValueError):
        validate_person_name(value)


@pytest.mark.parametrize("value", ["+373 69 123 456", "+7 (999) 123-45-67", "0691234567"])
def test_valid_phones(value):
    assert validate_phone(value) == value


@pytest.mark.parametrize("value", ["12345", "+373 abc 123", "1" * 16, "телефон"])
def test_invalid_phones(value):
    with pytest.raises(ValueError):
        validate_phone(value)


@pytest.mark.parametrize("value,ok", [("Secret123", True), ("Пароль2024", True), ("short1", False),
                                      ("onlyletters", False), ("12345678", False), (" Secret123", False)])
def test_password_rules(value, ok):
    if ok:
        assert validate_password(value) == value
    else:
        with pytest.raises(ValueError):
            validate_password(value)


def test_address_rules():
    assert validate_address("Кишинёв, ул. Пушкина, 10") == "Кишинёв, ул. Пушкина, 10"
    for bad in ["ул.", "12345", "x" * 300]:
        with pytest.raises(ValueError):
            validate_address(bad)


async def test_register_rejects_bad_input_with_russian_messages():
    import httpx

    from auth_service.config import Settings
    from auth_service.main import create_app

    app = create_app(
        Settings(**BASE, password_hash_iterations=1000),
        bus=InMemoryEventBus(),
        user_transport=httpx.MockTransport(lambda r: httpx.Response(201, json={})),
        bootstrap_admin=False,
    )
    async with running(app), client(app) as c:
        fields = _fields(await c.post("/auth/register", json={"email": "not-email", "password": "abcdefgh", "full_name": "1"}))
        assert fields["email"].startswith("Введите корректный email")
        assert "букву и одну цифру" in fields["password"]
        assert "Имя" in fields["full_name"]
        fields = _fields(await c.post("/auth/register", json={}))
        assert set(fields.values()) == {"Обязательное поле"}


async def test_order_requires_valid_delivery_details():
    from order_service.config import Settings as OrderSettings
    from order_service.main import create_app as order_app
    from product_service.config import Settings as ProductSettings
    from product_service.main import create_app as product_app

    bus = InMemoryEventBus()
    products = product_app(ProductSettings(**BASE, seed_demo_data=False), bus=bus)
    orders = order_app(OrderSettings(**BASE), bus=bus, product_transport=transport(products))
    async with running(products, orders), client(products) as pc, client(orders) as oc:
        p = (await pc.post("/products", json={"name": "Телефон", "price": 100, "stock": 5}, headers=ADMIN)).json()
        items = [{"product_id": p["id"], "quantity": 1}]

        fields = _fields(await oc.post("/orders", json={"items": items}, headers=auth()))
        assert fields == {"delivery": "Обязательное поле"}

        bad = {"recipient": "1", "phone": "123", "address": "ул", "comment": "x" * 501}
        fields = _fields(await oc.post("/orders", json={"items": items, "delivery": bad}, headers=auth()))
        assert set(fields) == {"delivery.recipient", "delivery.phone", "delivery.address", "delivery.comment"}

        fields = _fields(await oc.post("/orders", json={"items": [{"product_id": p["id"], "quantity": 0}], "delivery": DELIVERY}, headers=auth()))
        assert fields == {"items.0.quantity": "Значение должно быть больше 0"}

        r = await oc.post("/orders", json={"items": items, "delivery": {**DELIVERY, "comment": "  Позвонить за час  "}}, headers=auth())
        assert r.status_code == 201
        order = r.json()
        assert order["recipient"] == "Алиса Иванова"
        assert order["delivery_address"] == "Кишинёв, ул. Пушкина, 10"
        assert order["comment"] == "Позвонить за час"
        assert not bus.events_of("order.created")[0]["data"].get("phone")  # personal data stays in order-service


async def test_profile_and_product_validation():
    from product_service.config import Settings as ProductSettings
    from product_service.main import create_app as product_app
    from user_service.config import Settings as UserSettings
    from user_service.main import create_app as user_app

    users = user_app(UserSettings(**BASE))
    products = product_app(ProductSettings(**BASE, seed_demo_data=False), bus=InMemoryEventBus())
    async with running(users, products), client(users) as uc, client(products) as pc:
        await uc.post("/internal/users", json={"id": 1, "email": "a@example.com", "full_name": "Анна"}, headers=INTERNAL)
        fields = _fields(await uc.patch("/users/me", json={"phone": "abc", "address": "1", "full_name": "@@"}, headers=auth()))
        assert set(fields) == {"phone", "address", "full_name"}
        r = await uc.patch("/users/me", json={"phone": "+373 69 123 456", "address": "Кишинёв, ул. Пушкина, 10"}, headers=auth())
        assert r.status_code == 200 and r.json()["phone"] == "+373 69 123 456"

        bad = {"name": "X", "price": -5, "stock": -1, "category": "Bad Category!"}
        fields = _fields(await pc.post("/products", json=bad, headers=ADMIN))
        assert set(fields) == {"name", "price", "stock", "category"}
        assert fields["price"] == "Значение должно быть больше 0"
        assert fields["price"] and (await pc.post("/products", json={"name": "Цена", "price": 1.999}, headers=ADMIN)).status_code == 422
