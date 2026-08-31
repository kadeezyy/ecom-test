"""Этап 1: создание и чтение заказа."""

from __future__ import annotations

import pytest

from tests.conftest import ApiHarness

pytestmark = pytest.mark.db


async def test_создание_заказа_по_sku(api: ApiHarness) -> None:
    response = await api.client.post("/orders", json={"sku": "STEAM-TOPUP-500"})

    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("ord_")
    assert body["sku"] == "STEAM-TOPUP-500"
    assert body["status"] == "created"
    assert body["amount"] == 500
    assert body["currency"] == "RUB"
    assert body["code"] is None
    assert body["paid_at"] is None


async def test_чтение_заказа_по_id(api: ApiHarness) -> None:
    created = await api.create_order("KEY-CS2-PRIME")
    fetched = await api.get_order(created["id"])

    assert fetched["id"] == created["id"]
    assert fetched["amount"] == 1290


async def test_несуществующий_заказ_даёт_404(api: ApiHarness) -> None:
    response = await api.client.get("/orders/ord_missing")

    assert response.status_code == 404
    assert response.json()["code"] == "order_not_found"


async def test_несуществующий_sku_даёт_404(api: ApiHarness) -> None:
    response = await api.client.post("/orders", json={"sku": "НЕТ-ТАКОГО"})

    assert response.status_code == 404
    assert response.json()["code"] == "product_not_found"


async def test_пустой_sku_отклоняется_валидацией(api: ApiHarness) -> None:
    response = await api.client.post("/orders", json={"sku": ""})

    assert response.status_code == 422


async def test_ключ_идемпотентности_не_плодит_заказы(api: ApiHarness) -> None:
    headers = {"Idempotency-Key": "order-key-1"}
    first = await api.client.post("/orders", json={"sku": "KEY-GTA5"}, headers=headers)
    second = await api.client.post("/orders", json={"sku": "KEY-GTA5"}, headers=headers)

    assert first.json()["id"] == second.json()["id"]


async def test_цена_берётся_из_каталога_а_не_из_запроса(api: ApiHarness) -> None:
    """Клиент не может назначить себе цену — она приходит только из каталога."""
    response = await api.client.post("/orders", json={"sku": "KEY-EFT", "amount": 1, "price": 1})

    assert response.json()["amount"] == 3490
