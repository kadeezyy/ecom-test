from __future__ import annotations

import uuid

ORDER_PREFIX = "ord_"
REQUEST_PREFIX = "req_"
TXN_PREFIX = "txn_"


def new_order_id() -> str:
    return f"{ORDER_PREFIX}{uuid.uuid4().hex[:16]}"


def new_txn_id() -> str:
    return f"{TXN_PREFIX}{uuid.uuid4().hex[:16]}"


def delivery_request_id(order_id: str, supplier: str) -> str:
    """Детерминированный ``request_id`` для выдачи.

    Ключевое свойство: он **не зависит от номера попытки**. Любой повтор к
    тому же поставщику по тому же заказу идёт с тем же ``request_id``, а
    контракт обязывает поставщика вернуть на него тот же самый код. Именно
    это делает безопасным повтор после таймаута.
    """
    return f"{REQUEST_PREFIX}{order_id}_{supplier.lower()}"
