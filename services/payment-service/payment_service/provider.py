"""Simulated external payment provider (stands in for Stripe/PayPal/...)."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class ChargeResult:
    success: bool
    transaction_id: Optional[str] = None
    reason: Optional[str] = None


class FakePaymentProvider:
    def __init__(self, max_amount: Decimal) -> None:
        self.max_amount = max_amount

    async def charge(self, order_id: int, amount: Decimal) -> ChargeResult:
        if amount <= 0:
            return ChargeResult(False, reason="Некорректная сумма")
        if amount > self.max_amount:
            return ChargeResult(False, reason=f"сумма превышает лимит карты ({self.max_amount:.2f} $)")
        return ChargeResult(True, transaction_id=f"txn_{uuid.uuid4().hex[:20]}")

    async def refund(self, transaction_id: str, amount: Decimal) -> ChargeResult:
        return ChargeResult(True, transaction_id=transaction_id)
