"""Перечисления предметной области."""

from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    """Статусы заказа из ТЗ."""

    CREATED = "created"
    PAID = "paid"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    PAYMENT_FAILED = "payment_failed"
    OUT_OF_STOCK = "out_of_stock"
    DELIVERY_FAILED = "delivery_failed"


#: Финальные статусы: повторные события их не меняют.
FINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.DELIVERED, OrderStatus.PAYMENT_FAILED}
)

#: Восстановимые статусы: заказ оплачен, но не выдан — можно безопасно добить.
RECOVERABLE_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.OUT_OF_STOCK, OrderStatus.DELIVERY_FAILED}
)

#: Статусы, в которых деньги уже получены, а товар ещё не отдан.
UNSETTLED_STATUSES: frozenset[OrderStatus] = (
    frozenset({OrderStatus.PAID, OrderStatus.DELIVERING}) | RECOVERABLE_STATUSES
)


class PaymentEventStatus(StrEnum):
    PAID = "paid"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    """Состояние попытки выдачи у конкретного поставщика."""

    PENDING = "pending"
    #: Код получен.
    SUCCEEDED = "succeeded"
    #: Доказано, что кода нет: контрактная ошибка или несостоявшееся соединение.
    KNOWN_NEGATIVE = "known_negative"
    #: Исход неизвестен: таймаут/обрыв. Фолбэк отсюда запрещён.
    UNKNOWN = "unknown"
    #: Код получен, но заказ уже был выдан ранее. Аномалия для сверки.
    SUPERSEDED = "superseded"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    DEAD = "dead"


class SupplierName(StrEnum):
    A = "a"
    B = "b"


#: Порядок фолбэка: сначала A, при доказанном отказе — B.
SUPPLIER_CHAIN: tuple[SupplierName, ...] = (SupplierName.A, SupplierName.B)


class LedgerAccount(StrEnum):
    """Счета журнала денежных движений.

    Проводки всегда парные и в сумме дают ноль:

    * оплата:  ``gateway`` +A, ``order_liability`` −A
    * выдача:  ``order_liability`` +A, ``revenue`` −A

    Отсюда: ненулевой ``order_liability`` по заказу ≡ «оплачен, но не выдан».
    """

    #: Деньги, подтверждённые платёжной системой.
    GATEWAY = "gateway"
    #: Обязательство перед покупателем выдать товар.
    ORDER_LIABILITY = "order_liability"
    #: Признанная выручка (товар отдан).
    REVENUE = "revenue"


class LedgerEventType(StrEnum):
    PAYMENT_CAPTURED = "payment_captured"
    DELIVERY_COMPLETED = "delivery_completed"
