from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shop.domain.enums import DeliveryStatus, SupplierName
from shop.models import Delivery
from shop.repositories._sql import rows_affected


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_row(
        self, *, order_id: str, supplier: SupplierName, request_id: str
    ) -> Delivery:
        """Создаёт (или возвращает существующую) строку попытки.

        Строка заводится **до** сетевого вызова: если процесс умрёт сразу
        после запроса к поставщику, факт «мы обращались с этим request_id»
        уже зафиксирован и восстановление пойдёт по тому же пути.
        """
        stmt = (
            pg_insert(Delivery)
            .values(
                order_id=order_id,
                supplier=str(supplier),
                request_id=request_id,
                status=str(DeliveryStatus.PENDING),
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[Delivery.request_id])
        )
        await self._session.execute(stmt)
        row = await self.get_by_request_id(request_id)
        assert row is not None
        return row

    async def get_by_request_id(self, request_id: str) -> Delivery | None:
        stmt = select(Delivery).where(Delivery.request_id == request_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_for_order(self, order_id: str) -> Sequence[Delivery]:
        stmt = select(Delivery).where(Delivery.order_id == order_id).order_by(Delivery.supplier)
        return (await self._session.execute(stmt)).scalars().all()

    async def get_succeeded(self, order_id: str) -> Delivery | None:
        stmt = select(Delivery).where(
            Delivery.order_id == order_id,
            Delivery.status == str(DeliveryStatus.SUCCEEDED),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def record_outcome(
        self,
        *,
        request_id: str,
        status: DeliveryStatus,
        code: str | None,
        reason: str | None,
        attempted_at: datetime,
    ) -> None:
        await self._session.execute(
            update(Delivery)
            .where(Delivery.request_id == request_id)
            .values(
                status=str(status),
                code=code,
                reason=reason,
                attempts=Delivery.attempts + 1,
                last_attempt_at=attempted_at,
            )
        )

    async def reset_known_negatives(self, order_id: str) -> int:
        """Сбрасывает доказанные отказы в ``pending`` перед восстановлением.

        Безопасно: ``known_negative`` означает, что поставщик кода не выдавал,
        значит повторный запрос с тем же ``request_id`` ничего не задвоит.
        Строки со статусом ``unknown`` **не трогаются** — иначе потерялось бы
        требование «повтор только тому же поставщику».
        """
        result = await self._session.execute(
            update(Delivery)
            .where(
                Delivery.order_id == order_id,
                Delivery.status == str(DeliveryStatus.KNOWN_NEGATIVE),
            )
            .values(status=str(DeliveryStatus.PENDING), reason=None)
        )
        return rows_affected(result)

    async def list_orders_with_success(self, order_ids: Sequence[str]) -> set[str]:
        if not order_ids:
            return set()
        stmt = select(Delivery.order_id).where(
            Delivery.order_id.in_(order_ids),
            Delivery.status == str(DeliveryStatus.SUCCEEDED),
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def count_by_status(self, status: DeliveryStatus) -> int:
        stmt = select(func.count()).select_from(Delivery).where(Delivery.status == str(status))
        return (await self._session.execute(stmt)).scalar_one()

    async def list_superseded(self, limit: int = 100) -> Sequence[Delivery]:
        """Коды, полученные «вхолостую», — аномалия для ручного разбора."""
        stmt = (
            select(Delivery).where(Delivery.status == str(DeliveryStatus.SUPERSEDED)).limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()
