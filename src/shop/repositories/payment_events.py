"""Доступ к платёжным событиям."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shop.models import PaymentEvent


class PaymentEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_new(
        self,
        *,
        event_id: str,
        order_id: str,
        status: str,
        amount_minor: int,
        currency: str,
        occurred_at: datetime,
        raw: dict[str, Any],
    ) -> bool:
        """Вставляет событие. Возвращает ``False``, если оно уже было.

        Идемпотентность приёма вебхука обеспечивается первичным ключом, а не
        предварительным ``SELECT``: между чтением и вставкой у конкурентов
        была бы гонка, а ``ON CONFLICT DO NOTHING`` атомарен.
        """
        stmt = (
            pg_insert(PaymentEvent)
            .values(
                event_id=event_id,
                order_id=order_id,
                status=status,
                amount_minor=amount_minor,
                currency=currency,
                occurred_at=occurred_at,
                raw=raw,
                applied=False,
                orphan=False,
            )
            .on_conflict_do_nothing(index_elements=[PaymentEvent.event_id])
            .returning(PaymentEvent.event_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get(self, event_id: str) -> PaymentEvent | None:
        return await self._session.get(PaymentEvent, event_id)

    async def mark_applied(self, event_id: str, applied_at: datetime) -> None:
        await self._session.execute(
            update(PaymentEvent)
            .where(PaymentEvent.event_id == event_id)
            .values(applied=True, applied_at=applied_at, orphan=False, rejected_reason=None)
        )

    async def mark_rejected(self, event_id: str, reason: str) -> None:
        """Событие принято (200 OK), но заказ не изменило. Причина сохраняется."""
        await self._session.execute(
            update(PaymentEvent)
            .where(PaymentEvent.event_id == event_id)
            .values(applied=False, orphan=False, rejected_reason=reason)
        )

    async def mark_orphan(self, event_id: str) -> None:
        """Событие пришло раньше заказа — ждёт его появления."""
        await self._session.execute(
            update(PaymentEvent)
            .where(PaymentEvent.event_id == event_id)
            .values(orphan=True, applied=False, rejected_reason="order_not_found_yet")
        )

    async def list_pending_orphans(self, order_id: str) -> Sequence[PaymentEvent]:
        """Неприменённые «осиротевшие» события заказа, в порядке возникновения."""
        stmt = (
            select(PaymentEvent)
            .where(
                PaymentEvent.order_id == order_id,
                PaymentEvent.orphan.is_(True),
                PaymentEvent.applied.is_(False),
            )
            .order_by(PaymentEvent.occurred_at, PaymentEvent.received_at)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def list_pending_orphan_order_ids(self, limit: int = 100) -> list[str]:
        """Заказы, по которым лежат неприменённые «осиротевшие» события."""
        stmt = (
            select(PaymentEvent.order_id)
            .where(PaymentEvent.orphan.is_(True), PaymentEvent.applied.is_(False))
            .group_by(PaymentEvent.order_id)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_pending_orphans(self) -> int:
        stmt = select(PaymentEvent).where(
            PaymentEvent.orphan.is_(True), PaymentEvent.applied.is_(False)
        )
        return len((await self._session.execute(stmt)).scalars().all())

    async def list_for_order(self, order_id: str) -> Sequence[PaymentEvent]:
        stmt = (
            select(PaymentEvent)
            .where(PaymentEvent.order_id == order_id)
            .order_by(PaymentEvent.received_at)
        )
        return (await self._session.execute(stmt)).scalars().all()
