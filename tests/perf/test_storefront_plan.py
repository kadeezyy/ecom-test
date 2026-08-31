"""Этап 5: план выполнения «горячего» запроса витрины.

Тест проверяет не время (оно шумит на CI), а **форму плана**: обращение к
частичному покрывающему индексу вместо чтения всей таблицы с сортировкой.
"""

from __future__ import annotations

import random

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shop.repositories.products import ProductRepository

pytestmark = [pytest.mark.db, pytest.mark.slow]

ROWS = 20_000
BATCH = 2_000

SHELF_QUERY = """
SELECT sku, name, type, price_minor, currency, available_count
FROM products
WHERE is_active AND available_count > 0
ORDER BY price_minor, sku
LIMIT 50
"""


async def _seed(sessions: async_sessionmaker[AsyncSession], count: int) -> None:
    rng = random.Random(42)
    types = ("topup", "key", "subscription", "giftcard")
    for start in range(0, count, BATCH):
        rows = [
            {
                "sku": f"BULK-{i:07d}",
                "name": f"Синтетический товар {i}",
                "type": types[i % len(types)],
                "price_minor": rng.randrange(9_900, 999_900, 100),
                "currency": "RUB",
                "image_url": None,
                "is_active": True,
                # Треть распродана — партиальность индекса должна её отсекать.
                "available_count": 0 if rng.random() < 0.3 else rng.randint(1, 500),
            }
            for i in range(start, min(start + BATCH, count))
        ]
        async with sessions() as session, session.begin():
            await ProductRepository(session).upsert_many(rows)


async def _plan(session: AsyncSession, query: str) -> str:
    rows = (await session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {query}"))).scalars().all()
    return "\n".join(rows)


async def test_витрина_идёт_по_покрывающему_индексу(
    sessions: async_sessionmaker[AsyncSession], db: AsyncSession
) -> None:
    await _seed(sessions, ROWS)
    async with db.begin():
        await db.execute(text("ANALYZE products"))
        plan = await _plan(db, SHELF_QUERY)

    assert "ix_products_shelf" in plan, f"индекс витрины не использован:\n{plan}"
    assert "Seq Scan" not in plan, f"полное сканирование таблицы:\n{plan}"
    # Сортировки быть не должно: порядок даёт сам индекс.
    assert "Sort Method" not in plan, f"лишняя сортировка:\n{plan}"


async def test_фильтр_по_типу_идёт_по_своему_индексу(
    sessions: async_sessionmaker[AsyncSession], db: AsyncSession
) -> None:
    await _seed(sessions, ROWS)
    async with db.begin():
        await db.execute(text("ANALYZE products"))
        plan = await _plan(
            db,
            """
            SELECT sku, name, currency, price_minor, available_count
            FROM products
            WHERE is_active AND available_count > 0 AND type = 'key'
            ORDER BY price_minor, sku
            LIMIT 50
            """,
        )

    assert "ix_products_shelf_by_type" in plan, f"индекс по типу не использован:\n{plan}"
    assert "Seq Scan" not in plan, f"полное сканирование таблицы:\n{plan}"


async def test_курсорная_пагинация_не_деградирует_на_хвосте(
    sessions: async_sessionmaker[AsyncSession], db: AsyncSession
) -> None:
    """OFFSET читал бы и выбрасывал пропущенные строки; курсор — нет."""
    await _seed(sessions, ROWS)
    async with db.begin():
        await db.execute(text("ANALYZE products"))
        plan = await _plan(
            db,
            """
            SELECT sku, name, type, price_minor, currency, available_count
            FROM products
            WHERE is_active AND available_count > 0
              AND (price_minor, sku) > (900000, 'BULK-0000000')
            ORDER BY price_minor, sku
            LIMIT 50
            """,
        )

    assert "ix_products_shelf" in plan, plan
    assert "Seq Scan" not in plan, plan
