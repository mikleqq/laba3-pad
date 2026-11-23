"""Business logic of the Product Service (catalog + stock reservation)."""

import logging
from collections import OrderedDict
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shopcommon.errors import BadRequestError, ConflictError, NotFoundError
from shopcommon.logging import log_extra
from shopcommon.messaging import EventBus, safe_publish

from .models import Product
from .schemas import ProductCreate, ProductUpdate, ReservedItem, StockItem

logger = logging.getLogger("product_service")

# (name, category, price, stock, description). Brands are fictional.
DEMO_PRODUCTS = [
    ("Смартфон Nova X12, 128 ГБ", "smartphones", "499.00", 18, "6,4\" AMOLED 120 Гц, камера 50 Мп, 5000 мА·ч"),
    ("Смартфон Nova X12 Pro, 256 ГБ", "smartphones", "749.00", 9, "6,7\" LTPO, тройная камера, зарядка 67 Вт"),
    ("Ноутбук Vertex Air 14", "laptops", "1099.00", 7, "14\" 2.8K, 16 ГБ ОЗУ, SSD 512 ГБ, 1,2 кг"),
    ("Игровой ноутбук Vertex Storm 16", "laptops", "1799.00", 4, "16\" 240 Гц, RTX-класс графика, 32 ГБ ОЗУ"),
    ("Планшет Nova Tab 11", "tablets", "399.00", 12, "11\" 2K, 8 ГБ / 128 ГБ, поддержка стилуса"),
    ("Телевизор Lumio 55\" 4K", "tv", "649.00", 6, "QLED, HDR10+, Smart TV, 120 Гц"),
    ("Монитор Pixelon 27\" 4K", "monitors", "349.00", 10, "IPS, 99% sRGB, USB-C 90 Вт"),
    ("Игровой монитор Pixelon 32\" QHD", "monitors", "429.00", 8, "VA, 165 Гц, 1 мс, изогнутый 1500R"),
    ("Наушники Sonar ANC", "audio", "149.00", 15, "Активное шумоподавление, 30 ч работы"),
    ("Беспроводные наушники Sonar Buds", "audio", "89.00", 25, "TWS, Bluetooth 5.3, защита IPX4"),
    ("Колонка Sonar Boom", "audio", "69.00", 20, "Портативная, 20 Вт, влагозащита IP67"),
    ("Смарт-часы Pulse 3", "wearables", "199.00", 14, "AMOLED, пульс и SpO2, GPS, 10 дней работы"),
    ("Игровая консоль Arcadia One", "gaming", "499.00", 5, "4K 120 FPS, SSD 1 ТБ, геймпад в комплекте"),
    ("Геймпад Arcadia", "gaming", "59.00", 30, "Беспроводной, вибрация, 40 ч работы"),
    ("Механическая клавиатура Keyforge", "accessories", "89.99", 25, "Hot-swap переключатели, RGB-подсветка"),
    ("Беспроводная мышь Glide", "accessories", "29.50", 40, "Эргономичная, 2.4 ГГц + Bluetooth"),
]


def merge_items(items: list[StockItem]) -> "OrderedDict[int, int]":
    merged: "OrderedDict[int, int]" = OrderedDict()
    for item in items:
        merged[item.product_id] = merged.get(item.product_id, 0) + item.quantity
    return merged


class ProductService:
    def __init__(self, session: AsyncSession, bus: EventBus | None = None, low_stock_threshold: int = 3) -> None:
        self.session = session
        self.bus = bus
        self.low_stock_threshold = low_stock_threshold

    # ---------- catalog ----------
    async def list_all(self, *, search: str | None, category: str | None, limit: int, offset: int):
        query = select(Product)
        if search:
            pattern = f"%{search.lower()}%"
            query = query.where(or_(func.lower(Product.name).like(pattern), func.lower(Product.description).like(pattern)))
        if category:
            query = query.where(Product.category == category)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self.session.scalars(query.order_by(Product.id).limit(limit).offset(offset))
        return list(rows), int(total or 0)

    async def get(self, product_id: int) -> Product:
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError(f"Product {product_id} not found")
        return product

    async def create(self, data: ProductCreate) -> Product:
        product = Product(**data.model_dump())
        self.session.add(product)
        await self.session.commit()
        logger.info("product created", extra=log_extra(product_id=product.id))
        return product

    async def update(self, product_id: int, data: ProductUpdate) -> Product:
        product = await self.get(product_id)
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        if not changes:
            raise BadRequestError("Nothing to update")
        for field, value in changes.items():
            setattr(product, field, value)
        await self.session.commit()
        logger.info("product updated", extra=log_extra(product_id=product_id, fields=sorted(changes)))
        return product

    async def delete(self, product_id: int) -> None:
        product = await self.get(product_id)
        await self.session.delete(product)
        await self.session.commit()
        logger.info("product deleted", extra=log_extra(product_id=product_id))

    # ---------- stock (internal API used by order-service) ----------
    async def reserve(self, items: list[StockItem]) -> list[ReservedItem]:
        """All-or-nothing stock reservation inside a single transaction."""
        merged = merge_items(items)
        rows = await self.session.scalars(
            select(Product).where(Product.id.in_(merged.keys())).with_for_update()
        )
        products = {p.id: p for p in rows}
        reserved: list[ReservedItem] = []
        for product_id, quantity in merged.items():
            product = products.get(product_id)
            if product is None:
                await self.session.rollback()
                raise NotFoundError(f"Product {product_id} not found")
            if product.stock < quantity:
                error = ConflictError(
                    f"Insufficient stock for '{product.name}': requested {quantity}, available {product.stock}",
                    code="insufficient_stock",
                )
                await self.session.rollback()  # undo decrements of previous items
                raise error
            product.stock -= quantity
            reserved.append(
                ReservedItem(product_id=product.id, name=product.name, unit_price=Decimal(product.price), quantity=quantity)
            )
        await self.session.commit()
        logger.info("stock reserved", extra=log_extra(items=dict(merged)))
        await self._notify_low_stock([products[pid] for pid in merged])
        return reserved

    async def release(self, items: list[StockItem]) -> None:
        merged = merge_items(items)
        rows = await self.session.scalars(select(Product).where(Product.id.in_(merged.keys())).with_for_update())
        for product in rows:
            product.stock += merged[product.id]
        await self.session.commit()
        logger.info("stock released", extra=log_extra(items=dict(merged)))

    async def _notify_low_stock(self, products: list[Product]) -> None:
        if self.bus is None:
            return
        for product in products:
            if product.stock <= self.low_stock_threshold:
                await safe_publish(
                    self.bus, "product.low_stock", {"product_id": product.id, "name": product.name, "stock": product.stock}
                )

    async def seed(self) -> None:
        """Add the demo catalog items that are not in the database yet (matched by name)."""
        existing = set(await self.session.scalars(select(Product.name)))
        added = 0
        for name, category, price, stock, description in DEMO_PRODUCTS:
            if name in existing:
                continue
            self.session.add(Product(name=name, category=category, price=Decimal(price), stock=stock, description=description))
            added += 1
        if added:
            await self.session.commit()
            logger.info("demo catalog seeded", extra=log_extra(products=added))
