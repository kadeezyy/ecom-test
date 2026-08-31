"""Клиент поставщика против настоящей заглушки по настоящему HTTP.

Именно здесь проверяется классификация исходов: что считается доказанным
отказом (и разрешает фолбэк), а что — неизвестностью (и запрещает его).
"""

from __future__ import annotations

import pytest

from shop.core.exceptions import SupplierRefused, SupplierUnknown
from shop.domain.enums import SupplierName
from shop.integrations.supplier.client import SupplierClient
from tests.conftest import RunningStub


def _client(url: str, *, max_attempts: int = 1, read_timeout_s: float = 0.3) -> SupplierClient:
    return SupplierClient(
        name=SupplierName.A,
        base_url=url,
        connect_timeout_s=0.3,
        read_timeout_s=read_timeout_s,
        max_attempts=max_attempts,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
    )


async def test_успешная_выдача(stub_a: RunningStub) -> None:
    async with _client(stub_a.url) as client:
        issued = await client.issue(request_id="req_1", sku="KEY-CS2-PRIME", order_id="ord_1")
    assert issued.code
    assert issued.request_id == "req_1"
    assert await stub_a.issued_count() == 1


async def test_тот_же_request_id_возвращает_тот_же_код(stub_a: RunningStub) -> None:
    """Контрактная гарантия, на которой держится безопасность повторов."""
    async with _client(stub_a.url) as client:
        first = await client.issue(request_id="req_1", sku="KEY-GTA5", order_id="ord_1")
        second = await client.issue(request_id="req_1", sku="KEY-GTA5", order_id="ord_1")

    assert first.code == second.code
    assert await stub_a.issued_count() == 1, "второй запрос не должен тратить ключ"


async def test_контрактная_ошибка_это_доказанный_отказ(stub_a: RunningStub) -> None:
    await stub_a.configure(mode="error")
    async with _client(stub_a.url) as client:
        with pytest.raises(SupplierRefused) as exc:
            await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")

    assert exc.value.reason == "internal_error"
    assert await stub_a.issued_count() == 0


async def test_отсутствие_остатка_это_доказанный_отказ(stub_a: RunningStub) -> None:
    await stub_a.drain()
    async with _client(stub_a.url) as client:
        with pytest.raises(SupplierRefused) as exc:
            await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")

    assert exc.value.reason == "out_of_stock"


async def test_недоступный_поставщик_это_доказанный_отказ() -> None:
    """Соединение не установлено -> запрос не был отправлен -> кода точно нет.

    Поэтому фолбэк на второго поставщика в этом случае безопасен.
    """
    async with _client("http://127.0.0.1:1") as client:
        with pytest.raises(SupplierRefused) as exc:
            await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")

    assert exc.value.reason == "connect_failed"


async def test_таймаут_это_неизвестность_а_не_отказ(stub_a: RunningStub) -> None:
    """Ключевая проверка задания.

    Заглушка выдаёт код и зависает: клиент получает таймаут, но код у
    поставщика уже закреплён. Классифицировать это как отказ нельзя.
    """
    await stub_a.configure(mode="timeout", hang_seconds=2.0)

    async with _client(stub_a.url, read_timeout_s=0.2) as client:
        with pytest.raises(SupplierUnknown) as exc:
            await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")

    assert exc.value.reason == "read_timeout"
    assert await stub_a.issued_count() == 1, "поставщик УСПЕЛ выдать код"


async def test_повтор_после_таймаута_возвращает_тот_же_код(stub_a: RunningStub) -> None:
    """Повтор с тем же request_id не создаёт вторую выдачу."""
    await stub_a.configure(mode="timeout", hang_seconds=2.0)
    async with _client(stub_a.url, read_timeout_s=0.2) as client:
        with pytest.raises(SupplierUnknown):
            await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")

        # Поставщик «починился» — повторяем ТОТ ЖЕ request_id.
        await stub_a.configure(mode="ok")
        issued = await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")

    state = await stub_a.state()
    assert state["issued_count"] == 1, "ключ израсходован ровно один раз"
    assert issued.code == state["issued"][0]["code"]


async def test_повторы_с_бэкоффом_при_неизвестном_исходе(stub_a: RunningStub) -> None:
    """Клиент сам повторяет неизвестный исход — с тем же request_id."""
    await stub_a.configure(mode="timeout", hang_seconds=2.0)
    async with _client(stub_a.url, max_attempts=3, read_timeout_s=0.1) as client:
        with pytest.raises(SupplierUnknown):
            await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")

    # Три попытки, но ключ израсходован один: request_id не менялся.
    assert await stub_a.issued_count() == 1


async def test_доказанный_отказ_не_повторяется(stub_a: RunningStub) -> None:
    """Повторять отказ бессмысленно — его разруливает фолбэк уровнем выше."""
    await stub_a.configure(mode="error")
    async with _client(stub_a.url, max_attempts=3) as client:
        with pytest.raises(SupplierRefused):
            await client.issue(request_id="req_1", sku="KEY-EFT", order_id="ord_1")


async def test_остаток_поставщика_доступен_для_синхронизации(stub_a: RunningStub) -> None:
    async with _client(stub_a.url) as client:
        stock = await client.fetch_stock()

    assert stock is not None
    available, skus = stock
    assert available == 25
    assert "KEY-CS2-PRIME" in skus
