from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from shopcommon.schemas import Money


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    user_id: int
    amount: Money
    status: str
    transaction_id: Optional[str]
    failure_reason: Optional[str]
    created_at: datetime
    updated_at: datetime


class PaymentPage(BaseModel):
    items: list[PaymentOut]
    total: int
    limit: int
    offset: int
