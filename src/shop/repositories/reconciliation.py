"""Запросы сверки."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shop.domain.enums import DeliveryStatus, OrderStatus
from shop.models import Delivery, Order


@dataclass(frozen=True, slots=True)
class OrderRef:
    order_id: str
    sku: str
    status: str
    amount_minor: int
    currency: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DeliveryRef:
    order_id: str
    supplier: str
    request_id: str
    status: str
    reason: str | None
    attempts: int


class ReconciliationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def paid_not_delivered(self, *, older_than: datetime, limit: int = 200) -> list[OrderRef]:
        """Деньги получены, товар не отдан — главный финансовый риск."""
        stmt = (
            select(Order)
            .where(
                Order.paid_at.is_not(None),
                Order.status != str(OrderStatus.DELIVERED),
                Order.updated_at < older_than,
            )
            .order_by(Order.updated_at)
            .limit(limit)
        )
        return [_to_ref(o) for o in (await self._session.execute(stmt)).scalars().all()]

    async def delivered_not_paid(self, *, limit: int = 200) -> list[OrderRef]:
        """Товар отдан, оплаты нет — прямой убыток.

        Ищется по факту успешной выдачи, а не по статусу заказа: статус мог
        быть выставлен ошибочно, а строка выдачи — первичный факт.
        """
        stmt = (
            select(Order)
            .join(Delivery, Delivery.order_id == Order.id)
            .where(
                Delivery.status == str(DeliveryStatus.SUCCEEDED),
                Order.paid_at.is_(None),
            )
            .order_by(Order.updated_at)
            .limit(limit)
        )
        return [_to_ref(o) for o in (await self._session.execute(stmt)).scalars().all()]

    async def unresolved_deliveries(self, *, limit: int = 200) -> list[DeliveryRef]:
        """Попытки с неизвестным исходом: поставщик мог выдать код.

        Пока такая строка жива, фолбэк по заказу заблокирован — это и есть
        отражение правила «таймаут ≠ отказ» в отчёте.
        """
        stmt = (
            select(Delivery)
            .where(Delivery.status == str(DeliveryStatus.UNKNOWN))
            .order_by(Delivery.updated_at)
            .limit(limit)
        )
        return [_to_delivery_ref(d) for d in (await self._session.execute(stmt)).scalars().all()]

    async def superseded_codes(self, *, limit: int = 200) -> list[DeliveryRef]:
        stmt = (
            select(Delivery).where(Delivery.status == str(DeliveryStatus.SUPERSEDED)).limit(limit)
        )
        return [_to_delivery_ref(d) for d in (await self._session.execute(stmt)).scalars().all()]

    async def count_orders_by_status(self) -> dict[str, int]:
        stmt = select(Order.status, func.count()).group_by(Order.status)
        return {row[0]: row[1] for row in (await self._session.execute(stmt)).all()}


def _to_ref(order: Order) -> OrderRef:
    return OrderRef(
        order_id=order.id,
        sku=order.sku,
        status=str(order.status),
        amount_minor=order.amount_minor,
        currency=order.currency,
        updated_at=order.updated_at,
    )


def _to_delivery_ref(delivery: Delivery) -> DeliveryRef:
    return DeliveryRef(
        order_id=delivery.order_id,
        supplier=delivery.supplier,
        request_id=delivery.request_id,
        status=str(delivery.status),
        reason=delivery.reason,
        attempts=delivery.attempts,
    )


def orders_to_refs(orders: Sequence[Order]) -> list[OrderRef]:
    return [_to_ref(o) for o in orders]
