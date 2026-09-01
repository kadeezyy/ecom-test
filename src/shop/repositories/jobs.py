from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shop.domain.enums import JobState
from shop.models import DeliveryJob
from shop.repositories._sql import rows_affected


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: int
    order_id: str
    attempts: int


_ACTIVE_STATES_SQL = "state IN ('queued', 'running')"


class DeliveryJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, order_id: str, *, run_after: datetime | None = None) -> bool:
        """Ставит задачу, если активной ещё нет. ``False`` — уже была.

        Опирается на частичный уникальный индекс: 50 параллельных вебхуков
        по одному заказу создадут ровно одну задачу.
        """
        values: dict[str, object] = {
            "order_id": order_id,
            "state": str(JobState.QUEUED),
            "attempts": 0,
        }
        if run_after is not None:
            values["run_after"] = run_after

        stmt = (
            pg_insert(DeliveryJob)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[DeliveryJob.order_id],
                index_where=text(_ACTIVE_STATES_SQL),
            )
            .returning(DeliveryJob.id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def claim_batch(self, *, worker_id: str, limit: int) -> Sequence[ClaimedJob]:
        """Забирает пачку готовых задач.

        ``FOR UPDATE SKIP LOCKED`` даёт конкурентным воркерам непересекающиеся
        наборы задач без внешнего брокера и без опроса-гонки.
        """
        candidates = (
            select(DeliveryJob.id)
            .where(
                DeliveryJob.state == str(JobState.QUEUED),
                DeliveryJob.run_after <= func.now(),
            )
            .order_by(DeliveryJob.run_after)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        stmt = (
            update(DeliveryJob)
            .where(DeliveryJob.id.in_(candidates))
            .values(
                state=str(JobState.RUNNING),
                locked_at=func.now(),
                locked_by=worker_id,
                attempts=DeliveryJob.attempts + 1,
            )
            .returning(DeliveryJob.id, DeliveryJob.order_id, DeliveryJob.attempts)
        )
        rows = (await self._session.execute(stmt)).all()
        return [ClaimedJob(id=r[0], order_id=r[1], attempts=r[2]) for r in rows]

    async def finish(self, job_id: int, *, state: JobState, error: str | None = None) -> None:
        await self._session.execute(
            update(DeliveryJob)
            .where(DeliveryJob.id == job_id)
            .values(state=str(state), locked_at=None, locked_by=None, last_error=error)
        )

    async def reschedule(self, job_id: int, *, delay: timedelta, error: str | None) -> None:
        await self._session.execute(
            update(DeliveryJob)
            .where(DeliveryJob.id == job_id)
            .values(
                state=str(JobState.QUEUED),
                run_after=func.now() + delay,
                locked_at=None,
                locked_by=None,
                last_error=error,
            )
        )

    async def requeue_stale_running(self, older_than: datetime) -> int:
        """Возвращает в очередь задачи умершего воркера."""
        result = await self._session.execute(
            update(DeliveryJob)
            .where(
                DeliveryJob.state == str(JobState.RUNNING),
                DeliveryJob.locked_at < older_than,
            )
            .values(state=str(JobState.QUEUED), locked_at=None, locked_by=None)
        )
        return rows_affected(result)

    async def count_by_state(self, state: JobState) -> int:
        stmt = select(func.count()).select_from(DeliveryJob).where(DeliveryJob.state == str(state))
        return (await self._session.execute(stmt)).scalar_one()

    async def list_dead(self, limit: int = 100) -> Sequence[DeliveryJob]:
        stmt = (
            select(DeliveryJob)
            .where(DeliveryJob.state == str(JobState.DEAD))
            .order_by(DeliveryJob.updated_at.desc())
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()
