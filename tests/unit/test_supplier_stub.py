"""Инварианты самой заглушки — иначе тесты выше ничего не доказывают."""

from __future__ import annotations

import httpx

from tests.conftest import RunningStub


async def test_один_ключ_не_уходит_в_два_заказа(stub_a: RunningStub) -> None:
    codes = set()
    for i in range(10):
        response = await stub_a.client.post(
            "/issue",
            json={"request_id": f"req_{i}", "sku": "KEY-GTA5", "order_id": f"ord_{i}"},
        )
        response.raise_for_status()
        codes.add(response.json()["code"])

    assert len(codes) == 10
    assert await stub_a.available() == 15


async def test_повтор_request_id_идемпотентен(stub_a: RunningStub) -> None:
    payload = {"request_id": "req_x", "sku": "KEY-GTA5", "order_id": "ord_x"}
    first = (await stub_a.client.post("/issue", json=payload)).json()
    second = (await stub_a.client.post("/issue", json=payload)).json()

    assert first["code"] == second["code"]
    assert await stub_a.available() == 24


async def test_пустой_пул_даёт_контрактную_ошибку(stub_a: RunningStub) -> None:
    await stub_a.drain()
    response = await stub_a.client.post(
        "/issue", json={"request_id": "req_x", "sku": "KEY-GTA5", "order_id": "ord_x"}
    )

    assert response.status_code == 409
    assert response.json() == {"status": "error", "reason": "out_of_stock"}


async def test_пополнение_остатка_восстанавливает_выдачу(stub_a: RunningStub) -> None:
    await stub_a.drain()
    await stub_a.restock(["NEW1-NEW1-NEW1"])

    response = await stub_a.client.post(
        "/issue", json={"request_id": "req_x", "sku": "KEY-GTA5", "order_id": "ord_x"}
    )
    assert response.json()["code"] == "NEW1-NEW1-NEW1"


async def test_режим_timeout_выдаёт_код_до_зависания(stub_a: RunningStub) -> None:
    """Суть ловушки: код закреплён за request_id ещё до того, как ответ завис."""
    await stub_a.configure(mode="timeout", hang_seconds=2.0)

    try:
        await stub_a.client.post(
            "/issue",
            json={"request_id": "req_x", "sku": "KEY-GTA5", "order_id": "ord_x"},
            timeout=0.2,
        )
    except httpx.ReadTimeout:
        pass
    else:  # pragma: no cover
        raise AssertionError("ожидался таймаут")

    state = await stub_a.state()
    assert state["issued_count"] == 1
    assert state["available"] == 24


async def test_детерминированный_режим_переключается_на_лету(stub_a: RunningStub) -> None:
    await stub_a.configure(mode="error")
    assert (
        await stub_a.client.post("/issue", json={"request_id": "r1", "sku": "S", "order_id": "o1"})
    ).status_code == 503

    await stub_a.configure(mode="ok")
    assert (
        await stub_a.client.post("/issue", json={"request_id": "r2", "sku": "S", "order_id": "o2"})
    ).status_code == 200
