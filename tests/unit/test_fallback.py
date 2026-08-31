"""Правило фолбэка — ядро защиты от двойной выдачи.

Главное утверждение файла: из состояния ``unknown`` переход к следующему
поставщику невозможен ни при каких обстоятельствах.
"""

from __future__ import annotations

import pytest

from shop.domain.enums import DeliveryStatus, SupplierName
from shop.domain.fallback import choose_supplier, is_chain_exhausted

A = SupplierName.A
B = SupplierName.B
D = DeliveryStatus


def test_первая_попытка_идёт_к_поставщику_a() -> None:
    assert choose_supplier({}) is A


def test_доказанный_отказ_a_пускает_к_b() -> None:
    assert choose_supplier({A: D.KNOWN_NEGATIVE}) is B


@pytest.mark.parametrize("status", [D.UNKNOWN, D.PENDING])
def test_неизвестный_исход_у_a_не_пускает_к_b(status: DeliveryStatus) -> None:
    """Ловушка таймаута: поставщик мог выдать код, идти ко второму нельзя."""
    assert choose_supplier({A: status}) is A


def test_неизвестный_исход_у_a_не_пускает_к_b_даже_если_b_свободен() -> None:
    assert choose_supplier({A: D.UNKNOWN, B: D.PENDING}) is A


def test_оба_отказали_идти_некуда() -> None:
    statuses = {A: D.KNOWN_NEGATIVE, B: D.KNOWN_NEGATIVE}
    assert choose_supplier(statuses) is None
    assert is_chain_exhausted(statuses)


def test_успешная_выдача_останавливает_цепочку() -> None:
    assert choose_supplier({A: D.SUCCEEDED}) is None
    assert choose_supplier({A: D.KNOWN_NEGATIVE, B: D.SUCCEEDED}) is None


def test_отказ_a_и_неизвестность_b_возвращают_b() -> None:
    """У B неизвестный исход — повторяем ему же, а не начинаем сначала."""
    assert choose_supplier({A: D.KNOWN_NEGATIVE, B: D.UNKNOWN}) is B


def test_цепочка_не_исчерпана_пока_есть_unknown() -> None:
    assert not is_chain_exhausted({A: D.UNKNOWN, B: D.KNOWN_NEGATIVE})
