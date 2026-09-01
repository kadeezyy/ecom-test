from __future__ import annotations

from shop.core.ids import new_txn_id
from shop.domain.enums import LedgerAccount, LedgerEventType
from shop.repositories.ledger import LedgerRepository, Posting


class LedgerService:
    """Две операции, обе — сбалансированные пары проводок."""

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    async def record_payment(self, *, order_id: str, amount_minor: int, currency: str) -> bool:
        """Деньги подтверждены: появились средства и обязательство выдать товар."""
        return await self._ledger.post(
            txn_id=new_txn_id(),
            order_id=order_id,
            event_type=LedgerEventType.PAYMENT_CAPTURED,
            currency=currency,
            postings=[
                Posting(LedgerAccount.GATEWAY, amount_minor),
                Posting(LedgerAccount.ORDER_LIABILITY, -amount_minor),
            ],
        )

    async def record_delivery(self, *, order_id: str, amount_minor: int, currency: str) -> bool:
        """Товар отдан: обязательство закрыто, выручка признана."""
        return await self._ledger.post(
            txn_id=new_txn_id(),
            order_id=order_id,
            event_type=LedgerEventType.DELIVERY_COMPLETED,
            currency=currency,
            postings=[
                Posting(LedgerAccount.ORDER_LIABILITY, amount_minor),
                Posting(LedgerAccount.REVENUE, -amount_minor),
            ],
        )
