from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shop.models import Product
from shop.repositories._sql import rows_affected


@dataclass(frozen=True, slots=True)
class ShelfItem:
    sku: str
    name: str
    type: str
    price_minor: int
    currency: str
    available_count: int


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, sku: str) -> Product | None:
        return await self._session.get(Product, sku)

    async def shelf(
        self,
        *,
        type_: str | None = None,
        cursor_price: int | None = None,
        cursor_sku: str | None = None,
        limit: int = 50,
    ) -> list[ShelfItem]:
        """«Горячий» запрос витрины.

        Список колонок совпадает с ``INCLUDE`` частичных индексов, условие —
        с их предикатом, сортировка — с их порядком. Поэтому на тысячах SKU
        план остаётся ``Index Only Scan`` c ограничением сверху, без сортировки
        и без обращения к таблице.

        Пагинация курсорная: ``(price_minor, sku) > (:price, :sku)``. ``OFFSET``
        не используется — он заставляет читать и выбрасывать пропущенные строки.
        """
        stmt = select(
            Product.sku,
            Product.name,
            Product.type,
            Product.price_minor,
            Product.currency,
            Product.available_count,
        ).where(Product.is_active.is_(True), Product.available_count > 0)

        if type_ is not None:
            stmt = stmt.where(Product.type == type_)
        if cursor_price is not None and cursor_sku is not None:
            stmt = stmt.where(tuple_(Product.price_minor, Product.sku) > (cursor_price, cursor_sku))

        stmt = stmt.order_by(Product.price_minor, Product.sku).limit(limit)
        rows = (await self._session.execute(stmt)).all()
        return [
            ShelfItem(
                sku=r[0],
                name=r[1],
                type=r[2],
                price_minor=r[3],
                currency=r[4],
                available_count=r[5],
            )
            for r in rows
        ]

    async def upsert_many(self, items: Sequence[dict[str, Any]]) -> int:
        """Идемпотентный сид каталога."""
        if not items:
            return 0
        stmt = pg_insert(Product).values(list(items))
        stmt = stmt.on_conflict_do_update(
            index_elements=[Product.sku],
            set_={
                "name": stmt.excluded.name,
                "type": stmt.excluded.type,
                "price_minor": stmt.excluded.price_minor,
                "currency": stmt.excluded.currency,
                "image_url": stmt.excluded.image_url,
                "is_active": stmt.excluded.is_active,
            },
        )
        result = await self._session.execute(stmt)
        return rows_affected(result)

    async def set_stock(self, skus: Sequence[str], available: int, updated_at: datetime) -> int:
        """Обновляет кэш остатков только для реально изменившихся SKU."""
        if not skus:
            return 0
        result = await self._session.execute(
            update(Product)
            .where(Product.sku.in_(skus), Product.available_count != available)
            .values(available_count=available, stock_updated_at=updated_at)
        )
        return rows_affected(result)

    async def count(self) -> int:
        return (await self._session.execute(select(func.count()).select_from(Product))).scalar_one()
