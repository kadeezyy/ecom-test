"""Реестр клиентов поставщиков.

Здесь только владение соединениями. Логика «к кому идти следующим» —
бизнес-правило и живёт в :mod:`shop.services.delivery_service`.
"""

from __future__ import annotations

from shop.core.config import Settings
from shop.domain.enums import SUPPLIER_CHAIN, SupplierName
from shop.integrations.supplier.client import SupplierClient


class SupplierRegistry:
    def __init__(self, settings: Settings) -> None:
        self._clients: dict[SupplierName, SupplierClient] = {
            name: SupplierClient(
                name=name,
                base_url=settings.supplier_url(str(name)),
                connect_timeout_s=settings.supplier_connect_timeout_s,
                read_timeout_s=settings.supplier_read_timeout_s,
                max_attempts=settings.supplier_max_attempts,
                backoff_base_s=settings.supplier_backoff_base_s,
                backoff_max_s=settings.supplier_backoff_max_s,
            )
            for name in SUPPLIER_CHAIN
        }

    def get(self, name: SupplierName) -> SupplierClient:
        return self._clients[name]

    def replace(self, name: SupplierName, client: SupplierClient) -> None:
        """Подменяет клиента поставщика на лету.

        Нужна тестам сетевых отказов; в бою пригодится для переключения
        эндпоинта без перезапуска процесса.
        """
        self._clients[name] = client

    def all(self) -> list[SupplierClient]:
        return list(self._clients.values())

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
