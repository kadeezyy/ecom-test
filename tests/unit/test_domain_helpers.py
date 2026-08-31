"""Деньги, идентификаторы, курсор витрины."""

from __future__ import annotations

import pytest

from shop.core.ids import delivery_request_id, new_order_id
from shop.domain.money import is_supported_currency, to_major, to_minor
from shop.services.catalog_service import (
    InvalidCursor,
    _decode_cursor,
    _encode_cursor,
)


def test_деньги_ходят_туда_обратно() -> None:
    assert to_minor(500) == 50_000
    assert to_major(50_000) == 500
    assert to_major(to_minor(3490)) == 3490


def test_дробная_сумма_в_минорных_единицах_запрещена() -> None:
    with pytest.raises(ValueError, match="whole major unit"):
        to_major(50_001)


def test_поддерживается_только_рубль() -> None:
    assert is_supported_currency("RUB")
    assert is_supported_currency("rub")
    assert not is_supported_currency("USD")


def test_request_id_детерминирован_и_не_зависит_от_попытки() -> None:
    """От этого свойства зависит вся защита от двойной выдачи."""
    first = delivery_request_id("ord_abc", "a")
    second = delivery_request_id("ord_abc", "a")

    assert first == second == "req_ord_abc_a"
    assert delivery_request_id("ord_abc", "b") != first


def test_идентификаторы_заказов_уникальны() -> None:
    ids = {new_order_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("ord_") for i in ids)


def test_курсор_витрины_ходит_туда_обратно() -> None:
    cursor = _encode_cursor(129_000, "KEY-CS2-PRIME")
    assert _decode_cursor(cursor) == (129_000, "KEY-CS2-PRIME")


def test_пустой_курсор_означает_первую_страницу() -> None:
    assert _decode_cursor(None) == (None, None)
    assert _decode_cursor("") == (None, None)


@pytest.mark.parametrize("bad", ["!!!!", "YWJj", "MTIz"])
def test_битый_курсор_даёт_понятную_ошибку(bad: str) -> None:
    with pytest.raises(InvalidCursor):
        _decode_cursor(bad)
