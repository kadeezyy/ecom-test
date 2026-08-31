"""Приём платёжных вебхуков."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shop.core.exceptions import AmountMismatch
from shop.core.logging import get_logger
from shop.domain.enums import OrderStatus, PaymentEventStatus
from shop.domain.transitions import ensure_transition, is_final
from shop.models import Order, PaymentEvent
from shop.repositories.jobs import DeliveryJobRepository
from shop.repositories.ledger import AuditRepository, LedgerRepository
from shop.repositories.orders import OrderRepository
from shop.repositories.payment_events import PaymentEventRepository
from shop.services.ledger_service import LedgerService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PaymentWebhookCommand:
    """Полезная нагрузка вебхука, очищенная от деталей транспорта."""

    event_id: str
    order_id: str
    status: PaymentEventStatus
    amount_minor: int
    currency: str
    occurred_at: datetime
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WebhookResult:
    """Что вебхук сделал с заказом. Во всех случаях ответ клиенту — 200."""

    duplicate: bool
    applied: bool
    reason: str | None
    order_status: OrderStatus | None


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._orders = OrderRepository(session)
        self._events = PaymentEventRepository(session)
        self._jobs = DeliveryJobRepository(session)
        self._audit = AuditRepository(session)
        self._ledger = LedgerService(LedgerRepository(session))

    async def handle_webhook(self, cmd: PaymentWebhookCommand) -> WebhookResult:
        """Обрабатывает вебхук ровно один раз.

        Три рубежа защиты, в порядке срабатывания:

        1. первичный ключ ``payment_events.event_id`` — повторная доставка
           того же события не создаёт вторую строку и дальше не идёт;
        2. ``SELECT ... FOR UPDATE`` по заказу — параллельные события с
           разными ``event_id`` выстраиваются в очередь, а не гоняются;
        3. проверка перехода статуса — оплатить можно только заказ в
           ``created``, поэтому «побеждает» ровно одно событие.
        """
        is_new = await self._events.insert_if_new(
            event_id=cmd.event_id,
            order_id=cmd.order_id,
            status=str(cmd.status),
            amount_minor=cmd.amount_minor,
            currency=cmd.currency,
            occurred_at=cmd.occurred_at,
            raw=cmd.raw,
        )
        if not is_new:
            logger.info(
                "payment_webhook_duplicate",
                event_id=cmd.event_id,
                order_id=cmd.order_id,
            )
            return WebhookResult(
                duplicate=True, applied=False, reason="duplicate_event", order_status=None
            )

        logger.info(
            "payment_webhook_received",
            event_id=cmd.event_id,
            order_id=cmd.order_id,
            status=str(cmd.status),
        )

        order = await self._orders.get_for_update(cmd.order_id)
        if order is None:
            # Вебхук обогнал создание заказа. Событие сохраняем и применяем
            # позже — при создании заказа или фоновым добивателем.
            await self._events.mark_orphan(cmd.event_id)
            logger.info("payment_webhook_orphaned", event_id=cmd.event_id, order_id=cmd.order_id)
            return WebhookResult(
                duplicate=False,
                applied=False,
                reason="order_not_found_yet",
                order_status=None,
            )

        applied, reason = await self._apply(order, cmd.event_id, cmd)
        return WebhookResult(
            duplicate=False,
            applied=applied,
            reason=reason,
            order_status=OrderStatus(order.status),
        )

    async def apply_pending_orphans(self, order: Order) -> int:
        """Применяет к заказу события, пришедшие раньше него.

        Вызывается сразу после создания заказа и фоновым добивателем — второй
        нужен из-за узкой гонки «заказ создан ровно между проверкой и
        вставкой осиротевшего события».
        """
        events = await self._events.list_pending_orphans(order.id)
        applied_count = 0
        for event in events:
            cmd = self._command_from_event(event)
            applied, _ = await self._apply(order, event.event_id, cmd)
            if applied:
                applied_count += 1
        if events:
            logger.info(
                "payment_orphans_replayed",
                order_id=order.id,
                found=len(events),
                applied=applied_count,
            )
        return applied_count

    # ------------------------------------------------------------------
    # Применение одного события к заблокированному заказу
    # ------------------------------------------------------------------

    async def _apply(
        self, order: Order, event_id: str, cmd: PaymentWebhookCommand
    ) -> tuple[bool, str | None]:
        current = OrderStatus(order.status)

        if is_final(current):
            return await self._reject(event_id, order, "order_final")

        if self._is_stale(order, cmd):
            # Вебхуки приходят не по порядку: более раннее событие не должно
            # откатывать заказ, на который уже подействовало более позднее.
            return await self._reject(event_id, order, "stale_event")

        if cmd.status is PaymentEventStatus.FAILED:
            if current is not OrderStatus.CREATED:
                return await self._reject(event_id, order, "not_applicable")
            return await self._mark_failed(order, event_id, cmd)

        if current is not OrderStatus.CREATED:
            # Заказ уже оплачен другим (параллельным) событием — это
            # нормальный исход гонки, а не ошибка.
            return await self._reject(event_id, order, "already_paid")

        if not self._amount_matches(order, cmd):
            return await self._reject_amount(order, event_id, cmd)

        return await self._mark_paid(order, event_id, cmd)

    async def _mark_paid(
        self, order: Order, event_id: str, cmd: PaymentWebhookCommand
    ) -> tuple[bool, str | None]:
        now = datetime.now(UTC)
        ensure_transition(OrderStatus(order.status), OrderStatus.PAID)

        previous = OrderStatus(order.status)
        order.status = OrderStatus.PAID
        order.paid_at = now
        order.last_payment_event_at = cmd.occurred_at

        await self._ledger.record_payment(
            order_id=order.id, amount_minor=order.amount_minor, currency=order.currency
        )
        enqueued = await self._jobs.enqueue(order.id)
        await self._events.mark_applied(event_id, now)
        self._audit.add(
            order_id=order.id,
            event="payment_captured",
            from_status=previous,
            to_status=OrderStatus.PAID,
            payload={"event_id": event_id, "job_enqueued": enqueued},
        )
        logger.info(
            "payment_captured",
            order_id=order.id,
            event_id=event_id,
            amount_minor=order.amount_minor,
            job_enqueued=enqueued,
        )
        return True, None

    async def _mark_failed(
        self, order: Order, event_id: str, cmd: PaymentWebhookCommand
    ) -> tuple[bool, str | None]:
        now = datetime.now(UTC)
        ensure_transition(OrderStatus(order.status), OrderStatus.PAYMENT_FAILED)

        previous = OrderStatus(order.status)
        order.status = OrderStatus.PAYMENT_FAILED
        order.last_payment_event_at = cmd.occurred_at

        await self._events.mark_applied(event_id, now)
        self._audit.add(
            order_id=order.id,
            event="payment_failed",
            from_status=previous,
            to_status=OrderStatus.PAYMENT_FAILED,
            payload={"event_id": event_id},
        )
        logger.info("payment_failed", order_id=order.id, event_id=event_id)
        return True, None

    async def _reject(self, event_id: str, order: Order, reason: str) -> tuple[bool, str]:
        await self._events.mark_rejected(event_id, reason)
        logger.info(
            "payment_event_ignored",
            order_id=order.id,
            event_id=event_id,
            reason=reason,
            order_status=str(order.status),
        )
        return False, reason

    async def _reject_amount(
        self, order: Order, event_id: str, cmd: PaymentWebhookCommand
    ) -> tuple[bool, str]:
        error = AmountMismatch(
            "webhook amount does not match order",
            order_id=order.id,
            event_id=event_id,
            expected_minor=order.amount_minor,
            actual_minor=cmd.amount_minor,
            expected_currency=order.currency,
            actual_currency=cmd.currency,
        )
        # Заказ не переводим в paid: расхождение сумм — повод для разбора,
        # а не для выдачи товара. Событие попадёт в отчёт сверки.
        logger.error(
            "payment_amount_mismatch",
            exc_info=error,
            order_id=order.id,
            event_id=event_id,
            expected_minor=order.amount_minor,
            actual_minor=cmd.amount_minor,
            expected_currency=order.currency,
            actual_currency=cmd.currency,
        )
        self._audit.add(
            order_id=order.id,
            event="payment_amount_mismatch",
            payload={
                "event_id": event_id,
                "expected_minor": order.amount_minor,
                "actual_minor": cmd.amount_minor,
            },
        )
        return await self._reject(event_id, order, "amount_mismatch")

    # ------------------------------------------------------------------
    # Вспомогательное
    # ------------------------------------------------------------------

    @staticmethod
    def _is_stale(order: Order, cmd: PaymentWebhookCommand) -> bool:
        last = order.last_payment_event_at
        return last is not None and cmd.occurred_at < last

    @staticmethod
    def _amount_matches(order: Order, cmd: PaymentWebhookCommand) -> bool:
        return (
            cmd.amount_minor == order.amount_minor
            and cmd.currency.upper() == order.currency.upper()
        )

    @staticmethod
    def _command_from_event(event: PaymentEvent) -> PaymentWebhookCommand:
        return PaymentWebhookCommand(
            event_id=event.event_id,
            order_id=event.order_id,
            status=PaymentEventStatus(event.status),
            amount_minor=event.amount_minor,
            currency=event.currency,
            occurred_at=event.occurred_at,
            raw=event.raw,
        )
