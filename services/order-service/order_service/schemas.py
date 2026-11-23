from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shopcommon.schemas import Money
from shopcommon.validation import Address, OptionalComment, PersonName, Phone


class OrderItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)


class DeliveryIn(BaseModel):
    recipient: PersonName
    phone: Phone
    address: Address
    comment: OptionalComment = Field(default=None, max_length=500)


class OrderCreate(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)
    delivery: DeliveryIn

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [{"product_id": 1, "quantity": 2}],
                "delivery": {
                    "recipient": "Алиса Иванова",
                    "phone": "+373 69 123 456",
                    "address": "Кишинёв, ул. Пушкина, 10, кв. 5",
                    "comment": "Позвонить за час",
                },
            }
        }
    )


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    product_name: str
    unit_price: Money
    quantity: int


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: str
    total_amount: Money
    items: list[OrderItemOut]
    recipient: Optional[str] = None
    contact_phone: Optional[str] = None
    delivery_address: Optional[str] = None
    comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class OrderPage(BaseModel):
    items: list[OrderOut]
    total: int
    limit: int
    offset: int
