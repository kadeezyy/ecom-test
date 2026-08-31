"""Сверка: расхождения между деньгами и выданным товаром."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from shop.core.logging import get_logger
from shop.domain.enums import JobState
from shop.repositories.jobs import DeliveryJobRepository
from shop.repositories.ledger import LedgerRepository
from shop.repositories.payment_events import PaymentEventRepository
from shop.repositories.reconciliation import (
    DeliveryRef,
    OrderRef,
    ReconciliationRepository,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    generated_at: datetime
    grace_seconds: int

    #: «Оплачен, но не выдан».
    paid_not_delivered: list[OrderRef]
    #: «Выдан, но не оплачен».
    delivered_not_paid: list[OrderRef]
    #: Попытки с неизвестным исходом — блокируют фолбэк, требуют внимания.
    unresolved_deliveries: list[DeliveryRef]
    #: Коды, полученные при уже выданном заказе.
    superseded_codes: list[DeliveryRef]

    pending_orphan_events: int
    dead_jobs: int
    orders_by_status: dict[str, int]

    ledger_total_balance: int
    ledger_by_account: dict[str, int]
    ledger_open_liabilities: list[tuple[str, int]] = field(default_factory=list)

    @property
    def ledger_balanced(self) -> bool:
        """Инвариант журнала: сумма всех проводок равна нулю."""
        return self.ledger_total_balance == 0

    @property
    def healthy(self) -> bool:
        return self.ledger_balanced and not self.delivered_not_paid and not self.superseded_codes


class ReconciliationService:
    def __init__(self, session: AsyncSession) -> None:
        self._recon = ReconciliationRepository(session)
        self._ledger = LedgerRepository(session)
        self._events = PaymentEventRepository(session)
        self._jobs = DeliveryJobRepository(session)

    async def build_report(self, *, grace_seconds: int = 60) -> ReconciliationReport:
        """Собирает отчёт.

        ``grace_seconds`` отсекает заказы, которые прямо сейчас нормально
        обрабатываются: без него в отчёт попадал бы каждый только что
        оплаченный заказ.
        """
        now = datetime.now(UTC)
        threshold = now - timedelta(seconds=grace_seconds)

        report = ReconciliationReport(
            generated_at=now,
            grace_seconds=grace_seconds,
            paid_not_delivered=await self._recon.paid_not_delivered(older_than=threshold),
            delivered_not_paid=await self._recon.delivered_not_paid(),
            unresolved_deliveries=await self._recon.unresolved_deliveries(),
            superseded_codes=await self._recon.superseded_codes(),
            pending_orphan_events=await self._events.count_pending_orphans(),
            dead_jobs=await self._jobs.count_by_state(JobState.DEAD),
            orders_by_status=await self._recon.count_orders_by_status(),
            ledger_total_balance=await self._ledger.total_balance(),
            ledger_by_account=await self._ledger.balance_by_account(),
            ledger_open_liabilities=await self._ledger.open_liabilities(),
        )

        logger.info(
            "reconciliation_report_built",
            paid_not_delivered=len(report.paid_not_delivered),
            delivered_not_paid=len(report.delivered_not_paid),
            unresolved_deliveries=len(report.unresolved_deliveries),
            superseded_codes=len(report.superseded_codes),
            pending_orphan_events=report.pending_orphan_events,
            dead_jobs=report.dead_jobs,
            ledger_balanced=report.ledger_balanced,
        )
        return report
