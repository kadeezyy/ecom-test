from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shop.core.config import Settings
from shop.core.logging import get_logger
from shop.domain.enums import UNSETTLED_STATUSES
from shop.repositories.jobs import DeliveryJobRepository
from shop.repositories.orders import OrderRepository
from shop.repositories.payment_events import PaymentEventRepository
from shop.services.delivery_service import DeliveryService
from shop.services.payment_service import PaymentService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SweepResult:
    requeued_stale_jobs: int
    enqueued_stuck_orders: int
    replayed_orphan_events: int


class Sweeper:
    """Добиватель «зависших» заказов.

    Закрывает три дыры, которые не закрывает основной путь:

    * задачи умершего воркера, застрявшие в ``running``;
    * оплаченные, но не выданные заказы без активной задачи (например,
      процесс умер между сменой статуса и постановкой задачи);
    * платёжные события, пришедшие раньше заказа и разминувшиеся с его
      созданием.

    Все операции идемпотентны: повторный проход ничего не задваивает.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        deliveries: DeliveryService,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._deliveries = deliveries
        self._settings = settings

    async def run_once(self) -> SweepResult:
        threshold = datetime.now(UTC) - timedelta(seconds=self._settings.stuck_order_age_s)

        async with self._sessionmaker() as session, session.begin():
            requeued = await DeliveryJobRepository(session).requeue_stale_running(threshold)

        stuck_ids = await self._collect_stuck_orders(threshold)
        enqueued = 0
        for order_id in stuck_ids:
            # Сбрасываем доказанные отказы, чтобы после пополнения остатка
            # цепочка начиналась заново. Строки unknown остаются нетронутыми.
            await self._deliveries.prepare_recovery(order_id)
            async with self._sessionmaker() as session, session.begin():
                if await DeliveryJobRepository(session).enqueue(order_id):
                    enqueued += 1

        replayed = await self._replay_orphans()

        if requeued or enqueued or replayed:
            logger.info(
                "sweep_completed",
                requeued_stale_jobs=requeued,
                enqueued_stuck_orders=enqueued,
                replayed_orphan_events=replayed,
            )
        return SweepResult(requeued, enqueued, replayed)

    async def _collect_stuck_orders(self, threshold: datetime) -> list[str]:
        async with self._sessionmaker() as session:
            orders = await OrderRepository(session).list_stuck_without_job(
                sorted(UNSETTLED_STATUSES), threshold
            )
            return [o.id for o in orders]

    async def _replay_orphans(self) -> int:
        async with self._sessionmaker() as session:
            order_ids = await PaymentEventRepository(session).list_pending_orphan_order_ids()

        replayed = 0
        for order_id in order_ids:
            async with self._sessionmaker() as session, session.begin():
                order = await OrderRepository(session).get_for_update(order_id)
                if order is None:
                    continue
                replayed += await PaymentService(session).apply_pending_orphans(order)
        return replayed
