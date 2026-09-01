"""Фоновый воркер выдачи.

Забирает задачи из таблицы-очереди (``FOR UPDATE SKIP LOCKED``), выполняет
попытку выдачи и решает судьбу задачи по её исходу. Дополнительно с заданной
периодичностью гоняет добиватель зависших заказов и синхронизацию остатков.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import signal
import socket
import time
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shop.core.config import Settings, get_settings
from shop.core.db import dispose_engine, get_sessionmaker, init_engine
from shop.core.logging import configure_logging, get_logger
from shop.domain.enums import JobState
from shop.integrations.supplier.registry import SupplierRegistry
from shop.repositories.jobs import ClaimedJob, DeliveryJobRepository
from shop.services.delivery_service import DeliveryOutcome, DeliveryService
from shop.services.stock_sync_service import StockSyncService
from shop.worker.jobs import Sweeper

logger = get_logger(__name__)

#: Исходы, после которых задача считается отработанной.
_TERMINAL_OK = frozenset(
    {
        DeliveryOutcome.DELIVERED,
        DeliveryOutcome.ALREADY_DELIVERED,
        DeliveryOutcome.SKIPPED,
        DeliveryOutcome.OUT_OF_STOCK,
        DeliveryOutcome.CHAIN_EXHAUSTED,
    }
)


class Worker:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessionmaker: async_sessionmaker[AsyncSession] = get_sessionmaker()
        self._suppliers = SupplierRegistry(settings)
        self._deliveries = DeliveryService(self._sessionmaker, self._suppliers)
        self._sweeper = Sweeper(self._sessionmaker, self._deliveries, settings)
        self._stock = StockSyncService(self._sessionmaker, self._suppliers)
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}"
        self._stopping = asyncio.Event()

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def suppliers(self) -> SupplierRegistry:
        return self._suppliers

    def request_stop(self) -> None:
        logger.info("worker_stop_requested", worker_id=self._worker_id)
        self._stopping.set()

    async def run(self) -> None:
        logger.info("worker_started", worker_id=self._worker_id)
        next_sweep = 0.0
        next_stock_sync = 0.0

        try:
            while not self._stopping.is_set():
                now = time.monotonic()

                if now >= next_sweep:
                    await self._safe("sweep", self._sweeper.run_once())
                    next_sweep = now + self._settings.sweep_interval_s
                if now >= next_stock_sync:
                    await self._safe("stock_sync", self._stock.sync())
                    next_stock_sync = now + self._settings.stock_sync_interval_s

                claimed = await self._claim()
                if not claimed:
                    await self._sleep(self._settings.worker_poll_interval_s)
                    continue

                await asyncio.gather(*(self._process(job) for job in claimed))
        finally:
            await self._suppliers.aclose()
            logger.info("worker_stopped", worker_id=self._worker_id)

    async def run_until_idle(
        self, *, max_cycles: int = 200, idle_rounds: int = 3, pause: float = 0.05
    ) -> int:
        """Обрабатывает очередь, пока в ней есть готовые задачи.

        Отдельный вход в тот же конвейер, что и :meth:`run`, но без вечного
        цикла: используется тестами и ручным разбором очереди. Несколько
        «холостых» кругов подряд нужны, потому что задача, отложенная бэкоффом,
        становится доступной чуть позже.
        """
        processed = 0
        idle = 0
        for _ in range(max_cycles):
            claimed = await self._claim()
            if not claimed:
                idle += 1
                if idle >= idle_rounds:
                    return processed
                await asyncio.sleep(pause)
                continue
            idle = 0
            await asyncio.gather(*(self._process(job) for job in claimed))
            processed += len(claimed)
        return processed

    async def sweep_once(self) -> None:
        await self._sweeper.run_once()

    async def aclose(self) -> None:
        await self._suppliers.aclose()

    async def _claim(self) -> list[ClaimedJob]:
        async with self._sessionmaker() as session, session.begin():
            jobs = await DeliveryJobRepository(session).claim_batch(
                worker_id=self._worker_id, limit=self._settings.worker_batch_size
            )
        return list(jobs)

    async def _process(self, job: ClaimedJob) -> None:
        try:
            result = await self._deliveries.run(
                job.order_id,
                attempt=job.attempts,
                max_attempts=self._settings.job_max_attempts,
            )
        except Exception as exc:
            logger.error(
                "delivery_job_crashed",
                exc_info=exc,
                job_id=job.id,
                order_id=job.order_id,
                attempt=job.attempts,
            )
            await self._retry_or_bury(job, error=repr(exc))
            return

        outcome = result.outcome
        if outcome in _TERMINAL_OK:
            await self._finish(job, JobState.DONE, error=result.reason)
        elif outcome is DeliveryOutcome.RETRY_FALLBACK:
            # Доказанный отказ: следующий поставщик доступен, ждать нечего.
            await self._reschedule(job, delay=timedelta(0), error=result.reason)
        elif outcome is DeliveryOutcome.RETRY_SAME_SUPPLIER:
            await self._reschedule(job, delay=self._backoff(job.attempts), error=result.reason)
        else:  # DeliveryOutcome.FAILED — исход неизвестен, попытки исчерпаны
            await self._finish(job, JobState.DEAD, error=result.reason)

        logger.info(
            "delivery_job_finished",
            job_id=job.id,
            order_id=job.order_id,
            attempt=job.attempts,
            outcome=str(outcome),
        )

    async def _retry_or_bury(self, job: ClaimedJob, *, error: str) -> None:
        if job.attempts >= self._settings.job_max_attempts:
            await self._finish(job, JobState.DEAD, error=error)
        else:
            await self._reschedule(job, delay=self._backoff(job.attempts), error=error)

    async def _finish(self, job: ClaimedJob, state: JobState, *, error: str | None) -> None:
        async with self._sessionmaker() as session, session.begin():
            await DeliveryJobRepository(session).finish(job.id, state=state, error=error)

    async def _reschedule(self, job: ClaimedJob, *, delay: timedelta, error: str | None) -> None:
        async with self._sessionmaker() as session, session.begin():
            await DeliveryJobRepository(session).reschedule(job.id, delay=delay, error=error)

    def _backoff(self, attempts: int) -> timedelta:
        window = min(
            self._settings.job_backoff_base_s * (2 ** max(attempts - 1, 0)),
            self._settings.job_backoff_max_s,
        )
        return timedelta(seconds=random.uniform(window / 2, window))

    async def _safe(self, name: str, coro: object) -> None:
        """Периодическая задача не должна ронять цикл воркера."""
        assert asyncio.iscoroutine(coro)
        try:
            await coro
        except Exception as exc:
            logger.error("periodic_task_failed", exc_info=exc, task=name)

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)


async def _amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    init_engine(settings)

    worker = Worker(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.request_stop)

    try:
        await worker.run()
    finally:
        await dispose_engine()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
