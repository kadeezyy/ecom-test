"""Журнал денежных движений и аудит."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shop.domain.enums import LedgerAccount, LedgerEventType, OrderStatus
from shop.models import AuditLog, LedgerEntry


@dataclass(frozen=True, slots=True)
class Posting:
    account: LedgerAccount
    amount_minor: int


class LedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def post(
        self,
        *,
        txn_id: str,
        order_id: str,
        event_type: LedgerEventType,
        currency: str,
        postings: Sequence[Posting],
    ) -> bool:
        """Записывает группу проводок. ``False`` — они уже были записаны.

        Сумма проводок обязана быть нулевой, повтор гасится уникальным
        ключом ``(order_id, event_type, account)``.
        """
        total = sum(p.amount_minor for p in postings)
        if total != 0:  # pragma: no cover — защита инварианта
            raise ValueError(f"unbalanced posting for {order_id}: sum={total}")

        stmt = (
            pg_insert(LedgerEntry)
            .values(
                [
                    {
                        "txn_id": txn_id,
                        "order_id": order_id,
                        "account": str(p.account),
                        "amount_minor": p.amount_minor,
                        "currency": currency,
                        "event_type": str(event_type),
                    }
                    for p in postings
                ]
            )
            .on_conflict_do_nothing(constraint="uq_ledger_entries_posting")
            .returning(LedgerEntry.id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return len(rows) > 0

    async def total_balance(self) -> int:
        """Сумма всех проводок. Инвариант: всегда 0."""
        stmt = select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0))
        return (await self._session.execute(stmt)).scalar_one()

    async def balance_by_account(self) -> dict[str, int]:
        stmt = select(LedgerEntry.account, func.sum(LedgerEntry.amount_minor)).group_by(
            LedgerEntry.account
        )
        return {row[0]: row[1] for row in (await self._session.execute(stmt)).all()}

    async def open_liabilities(self, limit: int = 500) -> list[tuple[str, int]]:
        """Заказы с ненулевым обязательством ≡ «деньги взяли, товар не отдали»."""
        stmt = (
            select(LedgerEntry.order_id, func.sum(LedgerEntry.amount_minor))
            .where(LedgerEntry.account == str(LedgerAccount.ORDER_LIABILITY))
            .group_by(LedgerEntry.order_id)
            .having(func.sum(LedgerEntry.amount_minor) != 0)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in (await self._session.execute(stmt)).all()]

    async def entries_for_order(self, order_id: str) -> Sequence[LedgerEntry]:
        stmt = select(LedgerEntry).where(LedgerEntry.order_id == order_id).order_by(LedgerEntry.id)
        return (await self._session.execute(stmt)).scalars().all()


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self,
        *,
        order_id: str,
        event: str,
        from_status: OrderStatus | None = None,
        to_status: OrderStatus | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            AuditLog(
                order_id=order_id,
                event=event,
                from_status=str(from_status) if from_status else None,
                to_status=str(to_status) if to_status else None,
                payload=payload or {},
            )
        )

    async def list_for_order(self, order_id: str) -> Sequence[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.order_id == order_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
        return (await self._session.execute(stmt)).scalars().all()
