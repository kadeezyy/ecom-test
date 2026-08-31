"""Машина состояний заказа."""

from __future__ import annotations

import pytest

from shop.core.exceptions import InvalidTransition
from shop.domain.enums import FINAL_STATUSES, RECOVERABLE_STATUSES, OrderStatus
from shop.domain.transitions import (
    ALLOWED_TRANSITIONS,
    can_transition,
    ensure_transition,
    is_final,
)

S = OrderStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.CREATED, S.PAID),
        (S.CREATED, S.PAYMENT_FAILED),
        (S.PAID, S.DELIVERING),
        (S.DELIVERING, S.DELIVERED),
        (S.DELIVERING, S.OUT_OF_STOCK),
        (S.DELIVERING, S.DELIVERY_FAILED),
        # Восстановление из «мягких» неудач — ветки сбоев из ТЗ.
        (S.OUT_OF_STOCK, S.DELIVERING),
        (S.DELIVERY_FAILED, S.DELIVERING),
    ],
)
def test_основной_путь_и_восстановление_разрешены(
    current: OrderStatus, target: OrderStatus
) -> None:
    assert can_transition(current, target)
    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.CREATED, S.DELIVERED),  # выдача без оплаты
        (S.CREATED, S.DELIVERING),
        (S.PAID, S.DELIVERED),  # минуя delivering
        (S.PAID, S.PAYMENT_FAILED),  # оплата не отменяется задним числом
        (S.DELIVERED, S.DELIVERING),  # финальный статус неизменен
        (S.DELIVERED, S.PAYMENT_FAILED),
        (S.PAYMENT_FAILED, S.PAID),
        (S.OUT_OF_STOCK, S.DELIVERED),  # только через delivering
        (S.DELIVERING, S.PAID),
    ],
)
def test_запрещённые_переходы(current: OrderStatus, target: OrderStatus) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransition):
        ensure_transition(current, target)


def test_финальные_статусы_тупиковые() -> None:
    for status in FINAL_STATUSES:
        assert is_final(status)
        assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_восстановимые_статусы_ведут_только_в_delivering() -> None:
    for status in RECOVERABLE_STATUSES:
        assert not is_final(status)
        assert ALLOWED_TRANSITIONS[status] == frozenset({S.DELIVERING})


def test_таблица_переходов_покрывает_все_статусы() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(OrderStatus)
