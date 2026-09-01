from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shop.core.logging import get_logger
from shop.integrations.supplier.registry import SupplierRegistry
from shop.repositories.products import ProductRepository

logger = get_logger(__name__)


class StockSyncService:
    """Переносит остаток поставщиков в денормализованное поле каталога.

    Витрина не должна ходить в внешнюю систему на каждый запрос, поэтому
    остаток кэшируется в ``products.available_count``. Обновляются только
    те SKU, которые поставщики реально обслуживают, и только при изменении
    значения — иначе бессмысленные записи в 50k строк каждые 30 секунд.
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        suppliers: SupplierRegistry,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._suppliers = suppliers

    async def sync(self) -> int:
        total_available = 0
        skus: set[str] = set()
        reachable = 0

        for client in self._suppliers.all():
            stock = await client.fetch_stock()
            if stock is None:
                continue
            reachable += 1
            available, supplier_skus = stock
            total_available += available
            skus.update(supplier_skus)

        if reachable == 0:
            logger.warning("stock_sync_no_suppliers_reachable")
            return 0

        async with self._sessionmaker() as session, session.begin():
            updated = await ProductRepository(session).set_stock(
                sorted(skus), total_available, datetime.now(UTC)
            )

        logger.info(
            "stock_synced",
            suppliers_reachable=reachable,
            available=total_available,
            skus=len(skus),
            rows_updated=updated,
        )
        return updated
