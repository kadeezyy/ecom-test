"""Машина состояний заказа — единственное место, где описаны переходы."""

from __future__ import annotations

from shop.core.exceptions import InvalidTransition
from shop.domain.enums import FINAL_STATUSES, OrderStatus

#: Разрешённые переходы. Всё, чего здесь нет, — запрещено.
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.PAID, OrderStatus.PAYMENT_FAILED}),
    OrderStatus.PAID: frozenset({OrderStatus.DELIVERING}),
    OrderStatus.DELIVERING: frozenset(
        {
            OrderStatus.DELIVERED,
            OrderStatus.OUT_OF_STOCK,
            OrderStatus.DELIVERY_FAILED,
        }
    ),
    # Восстановление: из обеих «мягких» неудач можно вернуться к выдаче.
    OrderStatus.OUT_OF_STOCK: frozenset({OrderStatus.DELIVERING}),
    OrderStatus.DELIVERY_FAILED: frozenset({OrderStatus.DELIVERING}),
    # Финальные — тупики.
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.PAYMENT_FAILED: frozenset(),
}


def is_final(status: OrderStatus) -> bool:
    """Финальный ли статус (повторные события его не меняют)."""
    return status in FINAL_STATUSES


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    """Разрешён ли переход ``current -> target``."""
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Проверяет переход, иначе бросает :class:`InvalidTransition`."""
    if not can_transition(current, target):
        raise InvalidTransition(
            f"transition {current} -> {target} is not allowed",
            current=str(current),
            target=str(target),
        )
