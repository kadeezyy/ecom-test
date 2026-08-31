"""Деньги.

Внутри системы суммы хранятся **только** в минорных единицах (копейках)
целым числом — никаких float. На границе транспорта используется формат из
ТЗ: целое число мажорных единиц (``"amount": 500`` для товара за 500 ₽).
"""

from __future__ import annotations

from typing import Final

MINOR_UNITS_PER_MAJOR: Final[int] = 100

SUPPORTED_CURRENCIES: Final[frozenset[str]] = frozenset({"RUB"})


def to_minor(major: int) -> int:
    """Мажорные единицы (рубли) -> минорные (копейки)."""
    return major * MINOR_UNITS_PER_MAJOR


def to_major(minor: int) -> int:
    """Минорные единицы -> мажорные.

    Все цены в каталоге целые в рублях, дробных остатков не бывает.
    """
    if minor % MINOR_UNITS_PER_MAJOR != 0:  # pragma: no cover — защита инварианта
        raise ValueError(f"amount {minor} is not a whole major unit")
    return minor // MINOR_UNITS_PER_MAJOR


def is_supported_currency(currency: str) -> bool:
    return currency.upper() in SUPPORTED_CURRENCIES
