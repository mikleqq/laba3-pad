from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shopcommon.schemas import Money


class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="accessories", min_length=2, max_length=60, pattern=r"^[a-z_]+$")
    price: Decimal = Field(gt=0, le=1_000_000, max_digits=12, decimal_places=2)
    stock: int = Field(default=0, ge=0, le=100_000)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "Смартфон Nova X12, 128 ГБ", "category": "smartphones", "price": 499, "stock": 18}
        }
    )


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    category: Optional[str] = Field(default=None, min_length=2, max_length=60, pattern=r"^[a-z_]+$")
    price: Optional[Decimal] = Field(default=None, gt=0, le=1_000_000, max_digits=12, decimal_places=2)
    stock: Optional[int] = Field(default=None, ge=0, le=100_000)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    category: str
    price: Money
    stock: int
    created_at: datetime
    updated_at: datetime


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    limit: int
    offset: int


class StockItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=1000)


class StockRequest(BaseModel):
    items: list[StockItem] = Field(min_length=1, max_length=50)


class ReservedItem(BaseModel):
    product_id: int
    name: str
    unit_price: Money
    quantity: int


class ReservationOut(BaseModel):
    items: list[ReservedItem]
