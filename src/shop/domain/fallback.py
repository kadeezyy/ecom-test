"""Правило выбора поставщика — ядро защиты от двойной выдачи.

Правило одно и умещается в одну фразу:

    **перейти к следующему поставщику можно только из доказанного отказа.**

Состояние ``unknown`` (таймаут чтения, обрыв) переходить не даёт: код мог
быть уже выдан, и обращение ко второму поставщику создало бы вторую выдачу.
Из ``unknown`` есть только один путь — повтор тому же поставщику с тем же
``request_id``, который контракт обязывает обслужить идемпотентно.
"""

from __future__ import annotations

from collections.abc import Mapping

from shop.domain.enums import SUPPLIER_CHAIN, DeliveryStatus, SupplierName


def choose_supplier(
    statuses: Mapping[SupplierName, DeliveryStatus],
) -> SupplierName | None:
    """Кому адресовать следующую попытку.

    ``None`` означает «идти некуда»: либо уже выдано, либо цепочка исчерпана.
    """
    for name in SUPPLIER_CHAIN:
        status = statuses.get(name)

        if status is None or status in (DeliveryStatus.PENDING, DeliveryStatus.UNKNOWN):
            # Ещё не спрашивали, или спрашивали и не знаем ответа:
            # оба случая ведут к этому же поставщику и тому же request_id.
            return name

        if status is DeliveryStatus.SUCCEEDED:
            return None

        # KNOWN_NEGATIVE / SUPERSEDED — единственные основания идти дальше.

    return None


def is_chain_exhausted(statuses: Mapping[SupplierName, DeliveryStatus]) -> bool:
    """Все поставщики доказанно отказали."""
    return all(
        statuses.get(name) in (DeliveryStatus.KNOWN_NEGATIVE, DeliveryStatus.SUPERSEDED)
        for name in SUPPLIER_CHAIN
    )
