"""Выдача товара: фолбэк, повторы и защита от двойной выдачи."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shop.core.exceptions import (
    SupplierRefused,
    SupplierUnknown,
)
from shop.core.ids import delivery_request_id
from shop.core.logging import get_logger
from shop.domain.enums import DeliveryStatus, OrderStatus, SupplierName
from shop.domain.fallback import choose_supplier
from shop.domain.transitions import ensure_transition
from shop.integrations.supplier.registry import SupplierRegistry
from shop.models import Delivery, Order
from shop.repositories.deliveries import DeliveryRepository
from shop.repositories.ledger import AuditRepository, LedgerRepository
from shop.repositories.orders import OrderRepository
from shop.services.ledger_service import LedgerService

logger = get_logger(__name__)

OUT_OF_STOCK_REASON = "out_of_stock"

#: Статусы, из которых выдача имеет смысл.
DELIVERABLE_STATUSES = frozenset(
    {
        OrderStatus.PAID,
        OrderStatus.DELIVERING,
        OrderStatus.OUT_OF_STOCK,
        OrderStatus.DELIVERY_FAILED,
    }
)


class DeliveryOutcome(StrEnum):
    DELIVERED = "delivered"
    ALREADY_DELIVERED = "already_delivered"
    #: Исход неизвестен — повторить тому же поставщику после бэкоффа.
    RETRY_SAME_SUPPLIER = "retry_same_supplier"
    #: Доказанный отказ, в цепочке есть следующий — повторить сразу.
    RETRY_FALLBACK = "retry_fallback"
    #: Все поставщики доказанно отказали из-за отсутствия остатка.
    OUT_OF_STOCK = "out_of_stock"
    #: Все поставщики доказанно отказали по иной причине.
    CHAIN_EXHAUSTED = "chain_exhausted"
    #: Исход неизвестен и попытки job'а исчерпаны — нужен разбор.
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    order_id: str
    supplier: SupplierName | None = None
    code: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _Target:
    supplier: SupplierName
    request_id: str
    sku: str


class DeliveryService:
    """Выполняет одну попытку выдачи по заказу.

    Разбита на три фазы с намеренным разрывом транзакций:

    1. под блокировкой заказа выбирается поставщик и фиксируется намерение;
    2. **без открытой транзакции** делается сетевой вызов — блокировка строки
       не удерживается на время таймаута поставщика;
    3. под блокировкой заказа фиксируется результат.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        suppliers: SupplierRegistry,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._suppliers = suppliers

    async def run(
        self, order_id: str, *, attempt: int = 1, max_attempts: int = 1
    ) -> DeliveryResult:
        prepared = await self._prepare(order_id)
        if isinstance(prepared, DeliveryResult):
            return prepared

        client = self._suppliers.get(prepared.supplier)
        try:
            issued = await client.issue(
                request_id=prepared.request_id,
                sku=prepared.sku,
                order_id=order_id,
            )
        except SupplierRefused as exc:
            return await self._record_refusal(order_id, prepared, exc)
        except SupplierUnknown as exc:
            return await self._record_unknown(
                order_id, prepared, exc, attempt=attempt, max_attempts=max_attempts
            )

        return await self._record_success(order_id, prepared, issued.code)

    # ------------------------------------------------------------------
    # Фаза 1: выбор поставщика под блокировкой заказа
    # ------------------------------------------------------------------

    async def _prepare(self, order_id: str) -> _Target | DeliveryResult:
        async with self._sessionmaker() as session, session.begin():
            orders = OrderRepository(session)
            deliveries = DeliveryRepository(session)

            order = await orders.get_for_update(order_id)
            if order is None:
                logger.warning("delivery_order_missing", order_id=order_id)
                return DeliveryResult(DeliveryOutcome.SKIPPED, order_id, reason="order_missing")

            status = OrderStatus(order.status)
            if status is OrderStatus.DELIVERED:
                return DeliveryResult(DeliveryOutcome.ALREADY_DELIVERED, order_id)
            if status not in DELIVERABLE_STATUSES:
                logger.info("delivery_skipped", order_id=order_id, order_status=str(status))
                return DeliveryResult(DeliveryOutcome.SKIPPED, order_id, reason=f"status_{status}")

            rows = await deliveries.list_for_order(order_id)
            succeeded = next((r for r in rows if r.status == DeliveryStatus.SUCCEEDED), None)
            if succeeded is not None:
                # Код уже получен, а заказ отстал (например, процесс умер
                # между записью выдачи и сменой статуса). Догоняем.
                await self._finalize_delivered(session, order, succeeded)
                return DeliveryResult(
                    DeliveryOutcome.ALREADY_DELIVERED,
                    order_id,
                    supplier=SupplierName(succeeded.supplier),
                    code=succeeded.code,
                )

            statuses = {SupplierName(r.supplier): DeliveryStatus(r.status) for r in rows}
            target = choose_supplier(statuses)
            if target is None:
                return await self._finalize_exhausted(session, order, rows)

            if status is not OrderStatus.DELIVERING:
                ensure_transition(status, OrderStatus.DELIVERING)
                order.status = OrderStatus.DELIVERING

            request_id = delivery_request_id(order_id, str(target))
            await deliveries.ensure_row(order_id=order_id, supplier=target, request_id=request_id)
            return _Target(supplier=target, request_id=request_id, sku=order.sku)

    # ------------------------------------------------------------------
    # Фаза 3: фиксация результата
    # ------------------------------------------------------------------

    async def _record_success(self, order_id: str, target: _Target, code: str) -> DeliveryResult:
        now = datetime.now(UTC)
        try:
            async with self._sessionmaker() as session, session.begin():
                orders = OrderRepository(session)
                deliveries = DeliveryRepository(session)

                order = await orders.get_for_update(order_id)
                if order is None:  # pragma: no cover — заказ не удаляется
                    return DeliveryResult(DeliveryOutcome.SKIPPED, order_id)

                await deliveries.record_outcome(
                    request_id=target.request_id,
                    status=DeliveryStatus.SUCCEEDED,
                    code=code,
                    reason=None,
                    attempted_at=now,
                )
                # Здесь сработает частичный уникальный индекс, если у заказа
                # каким-то образом уже есть успешная выдача.
                await session.flush()

                if OrderStatus(order.status) is not OrderStatus.DELIVERED:
                    previous = OrderStatus(order.status)
                    ensure_transition(previous, OrderStatus.DELIVERED)
                    order.status = OrderStatus.DELIVERED
                    order.delivered_at = now

                    await LedgerService(LedgerRepository(session)).record_delivery(
                        order_id=order.id,
                        amount_minor=order.amount_minor,
                        currency=order.currency,
                    )
                    AuditRepository(session).add(
                        order_id=order.id,
                        event="delivery_completed",
                        from_status=previous,
                        to_status=OrderStatus.DELIVERED,
                        payload={
                            "supplier": str(target.supplier),
                            "request_id": target.request_id,
                        },
                    )
        except IntegrityError as exc:
            return await self._record_superseded(order_id, target, code, exc)

        logger.info(
            "delivery_completed",
            order_id=order_id,
            supplier=str(target.supplier),
            request_id=target.request_id,
        )
        return DeliveryResult(
            DeliveryOutcome.DELIVERED, order_id, supplier=target.supplier, code=code
        )

    async def _record_superseded(
        self, order_id: str, target: _Target, code: str, exc: IntegrityError
    ) -> DeliveryResult:
        """Код получен, но заказ уже был выдан. Аномалия — фиксируем, не теряем."""
        logger.error(
            "delivery_superseded",
            exc_info=exc,
            order_id=order_id,
            supplier=str(target.supplier),
            request_id=target.request_id,
        )
        async with self._sessionmaker() as session, session.begin():
            await DeliveryRepository(session).record_outcome(
                request_id=target.request_id,
                status=DeliveryStatus.SUPERSEDED,
                code=code,
                reason="already_delivered",
                attempted_at=datetime.now(UTC),
            )
            AuditRepository(session).add(
                order_id=order_id,
                event="delivery_superseded",
                payload={"supplier": str(target.supplier), "request_id": target.request_id},
            )
        return DeliveryResult(DeliveryOutcome.ALREADY_DELIVERED, order_id, supplier=target.supplier)

    async def _record_refusal(
        self, order_id: str, target: _Target, exc: SupplierRefused
    ) -> DeliveryResult:
        """Доказанный отказ: кода нет. Разрешено идти к следующему поставщику."""
        logger.info(
            "delivery_attempt_refused",
            order_id=order_id,
            supplier=str(target.supplier),
            request_id=target.request_id,
            reason=exc.reason,
        )
        async with self._sessionmaker() as session, session.begin():
            orders = OrderRepository(session)
            deliveries = DeliveryRepository(session)

            order = await orders.get_for_update(order_id)
            if order is None:  # pragma: no cover
                return DeliveryResult(DeliveryOutcome.SKIPPED, order_id)

            await deliveries.record_outcome(
                request_id=target.request_id,
                status=DeliveryStatus.KNOWN_NEGATIVE,
                code=None,
                reason=exc.reason,
                attempted_at=datetime.now(UTC),
            )
            rows = await deliveries.list_for_order(order_id)
            statuses = {SupplierName(r.supplier): DeliveryStatus(r.status) for r in rows}

            if choose_supplier(statuses) is not None:
                return DeliveryResult(
                    DeliveryOutcome.RETRY_FALLBACK,
                    order_id,
                    supplier=target.supplier,
                    reason=exc.reason,
                )
            return await self._finalize_exhausted(session, order, rows)

    async def _record_unknown(
        self,
        order_id: str,
        target: _Target,
        exc: SupplierUnknown,
        *,
        attempt: int,
        max_attempts: int,
    ) -> DeliveryResult:
        """Исход неизвестен.

        Фолбэк запрещён: поставщик мог выдать код, и обращение ко второму
        создало бы вторую выдачу. Единственный допустимый ход — повтор тому же
        поставщику с тем же ``request_id``.
        """
        exhausted = attempt >= max_attempts
        logger.error(
            "delivery_outcome_unknown",
            exc_info=exc,
            order_id=order_id,
            supplier=str(target.supplier),
            request_id=target.request_id,
            reason=exc.reason,
            attempt=attempt,
            max_attempts=max_attempts,
            fallback_blocked=True,
        )

        async with self._sessionmaker() as session, session.begin():
            orders = OrderRepository(session)
            deliveries = DeliveryRepository(session)

            order = await orders.get_for_update(order_id)
            if order is None:  # pragma: no cover
                return DeliveryResult(DeliveryOutcome.SKIPPED, order_id)

            await deliveries.record_outcome(
                request_id=target.request_id,
                status=DeliveryStatus.UNKNOWN,
                code=None,
                reason=exc.reason,
                attempted_at=datetime.now(UTC),
            )
            AuditRepository(session).add(
                order_id=order_id,
                event="delivery_outcome_unknown",
                payload={
                    "supplier": str(target.supplier),
                    "request_id": target.request_id,
                    "reason": exc.reason,
                    "attempt": attempt,
                },
            )

            if not exhausted:
                # Заказ остаётся в delivering: попытки ещё не исчерпаны.
                return DeliveryResult(
                    DeliveryOutcome.RETRY_SAME_SUPPLIER,
                    order_id,
                    supplier=target.supplier,
                    reason=exc.reason,
                )

            previous = OrderStatus(order.status)
            if previous is OrderStatus.DELIVERING:
                ensure_transition(previous, OrderStatus.DELIVERY_FAILED)
                order.status = OrderStatus.DELIVERY_FAILED

        return DeliveryResult(
            DeliveryOutcome.FAILED,
            order_id,
            supplier=target.supplier,
            reason=exc.reason,
        )

    # ------------------------------------------------------------------
    # Терминальные состояния
    # ------------------------------------------------------------------

    async def _finalize_exhausted(
        self, session: AsyncSession, order: Order, rows: Sequence[Delivery]
    ) -> DeliveryResult:
        """Все поставщики доказанно отказали."""
        reasons = [r.reason for r in rows if r.reason]
        out_of_stock = OUT_OF_STOCK_REASON in reasons
        target_status = OrderStatus.OUT_OF_STOCK if out_of_stock else OrderStatus.DELIVERY_FAILED
        previous = OrderStatus(order.status)

        if previous is not target_status and previous is OrderStatus.DELIVERING:
            ensure_transition(previous, target_status)
            order.status = target_status
            AuditRepository(session).add(
                order_id=order.id,
                event="delivery_chain_exhausted",
                from_status=previous,
                to_status=target_status,
                payload={"reasons": reasons},
            )

        logger.warning(
            "delivery_chain_exhausted",
            order_id=order.id,
            reasons=reasons,
            order_status=str(target_status),
        )
        outcome = DeliveryOutcome.OUT_OF_STOCK if out_of_stock else DeliveryOutcome.CHAIN_EXHAUSTED
        return DeliveryResult(outcome, order.id, reason=",".join(reasons) or None)

    async def _finalize_delivered(
        self, session: AsyncSession, order: Order, delivery: Delivery
    ) -> None:
        """Доводит заказ до ``delivered`` по уже существующей успешной выдаче."""
        previous = OrderStatus(order.status)
        if previous is OrderStatus.DELIVERED:
            return

        now = datetime.now(UTC)
        if previous is not OrderStatus.DELIVERING:
            ensure_transition(previous, OrderStatus.DELIVERING)
            order.status = OrderStatus.DELIVERING
            previous = OrderStatus.DELIVERING

        ensure_transition(previous, OrderStatus.DELIVERED)
        order.status = OrderStatus.DELIVERED
        order.delivered_at = now

        await LedgerService(LedgerRepository(session)).record_delivery(
            order_id=order.id, amount_minor=order.amount_minor, currency=order.currency
        )
        AuditRepository(session).add(
            order_id=order.id,
            event="delivery_healed",
            to_status=OrderStatus.DELIVERED,
            payload={"request_id": delivery.request_id, "supplier": delivery.supplier},
        )
        logger.info("delivery_healed", order_id=order.id, request_id=delivery.request_id)

    # ------------------------------------------------------------------
    # Восстановление
    # ------------------------------------------------------------------

    async def prepare_recovery(self, order_id: str) -> int:
        """Готовит заказ к повторной выдаче.

        Сбрасывает **только** доказанные отказы: после пополнения остатка к
        поставщику A можно обратиться снова. Строки ``unknown`` остаются, иначе
        потерялась бы привязка «повторяем тому же поставщику».
        """
        async with self._sessionmaker() as session, session.begin():
            reset = await DeliveryRepository(session).reset_known_negatives(order_id)
        if reset:
            logger.info("delivery_recovery_prepared", order_id=order_id, reset_rows=reset)
        return reset
