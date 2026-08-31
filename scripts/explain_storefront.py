"""План выполнения «горячего» запроса витрины (этап 5).

    python scripts/seed_bulk.py --count 50000
    python scripts/explain_storefront.py

Печатает EXPLAIN (ANALYZE, BUFFERS) для запроса витрины — с индексами и,
для сравнения, с принудительно отключённым индексным сканом.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shop.core.config import get_settings
from shop.core.db import dispose_engine, get_sessionmaker, init_engine
from shop.core.logging import configure_logging

QUERY = """
SELECT sku, name, type, price_minor, currency, available_count
FROM products
WHERE is_active AND available_count > 0
ORDER BY price_minor, sku
LIMIT 50
"""

QUERY_BY_TYPE = """
SELECT sku, name, currency, price_minor, available_count
FROM products
WHERE is_active AND available_count > 0 AND type = 'key'
ORDER BY price_minor, sku
LIMIT 50
"""


async def _explain(session: AsyncSession, title: str, sql: str, *, use_indexes: bool) -> None:
    async with session.begin():
        toggle = "on" if use_indexes else "off"
        await session.execute(text(f"SET LOCAL enable_indexscan = {toggle}"))
        await session.execute(text(f"SET LOCAL enable_indexonlyscan = {toggle}"))
        rows = (
            (await session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, TIMING) {sql}")))
            .scalars()
            .all()
        )
    print(f"\n=== {title} ===")
    for row in rows:
        print(row)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    init_engine(settings)
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                count = (await session.execute(text("SELECT count(*) FROM products"))).scalar_one()
                await session.execute(text("ANALYZE products"))
            print(f"SKU в каталоге: {count}")

            await _explain(session, "витрина, с индексами", QUERY, use_indexes=True)
            await _explain(
                session,
                "витрина, индексный скан отключён (для сравнения)",
                QUERY,
                use_indexes=False,
            )
            await _explain(session, "витрина с фильтром по типу", QUERY_BY_TYPE, use_indexes=True)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
