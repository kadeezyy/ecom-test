"""Доступ к заказам."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from shop.domain.enums import JobState, OrderStatus
from shop.models import DeliveryJob, Order


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, order_id: str) -> Order | None:
        return await self._session.get(Order, order_id)

    async def get_for_update(self, order_id: str) -> Order | None:
        """Читает заказ, блокируя строку до конца транзакции.

        Сериализует параллельные вебхуки по одному заказу: конкурирующие
        транзакции выстраиваются в очередь на этой строке, а не гоняются.
        """
        stmt: Select[tuple[Order]] = select(Order).where(Order.id == order_id).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(self, key: str) -> Order | None:
        stmt = select(Order).where(Order.idempotency_key == key)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def add(self, order: Order) -> None:
        self._session.add(order)

    async def list_unsettled(
        self, statuses: Sequence[OrderStatus], limit: int = 500
    ) -> Sequence[Order]:
        """Заказы в незавершённых статусах — для отчёта сверки."""
        stmt = (
            select(Order)
            .where(Order.status.in_([str(s) for s in statuses]))
            .order_by(Order.updated_at)
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def list_stuck_without_job(
        self, statuses: Sequence[OrderStatus], older_than: datetime, limit: int = 100
    ) -> Sequence[Order]:
        """Заказы, которые «зависли»: оплачены, не выданы и без активной задачи.

        Именно их безопасно доводит фоновый добиватель.
        """
        active_job = exists().where(
            and_(
                DeliveryJob.order_id == Order.id,
                DeliveryJob.state.in_([str(JobState.QUEUED), str(JobState.RUNNING)]),
            )
        )
        stmt = (
            select(Order)
            .where(
                Order.status.in_([str(s) for s in statuses]),
                Order.updated_at < older_than,
                ~active_job,
            )
            .order_by(Order.updated_at)
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()
