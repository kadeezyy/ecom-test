"""Этап 5: витрина."""

from __future__ import annotations

import pytest

from tests.conftest import ApiHarness

pytestmark = pytest.mark.db


async def test_витрина_отдаёт_каталог(api: ApiHarness) -> None:
    response = await api.client.get("/catalog")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 12
    assert {i["sku"] for i in items} >= {"STEAM-TOPUP-500", "KEY-CS2-PRIME"}
    assert items[0]["price"] <= items[-1]["price"], "сортировка по цене"


async def test_фильтр_по_типу(api: ApiHarness) -> None:
    response = await api.client.get("/catalog", params={"type": "giftcard"})

    items = response.json()["items"]
    assert len(items) == 3
    assert {i["type"] for i in items} == {"giftcard"}


async def test_курсорная_пагинация_обходит_каталог_без_повторов(
    api: ApiHarness,
) -> None:
    seen: list[str] = []
    cursor: str | None = None

    for _ in range(10):
        params = {"limit": 5} | ({"cursor": cursor} if cursor else {})
        page = (await api.client.get("/catalog", params=params)).json()
        seen.extend(i["sku"] for i in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 12
    assert len(set(seen)) == 12, "страницы не должны пересекаться"


async def test_битый_курсор_даёт_400(api: ApiHarness) -> None:
    response = await api.client.get("/catalog", params={"cursor": "!!!"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_cursor"


async def test_распроданные_sku_не_попадают_на_витрину(api: ApiHarness) -> None:
    from sqlalchemy import text

    from shop.core.db import get_sessionmaker

    async with get_sessionmaker()() as session, session.begin():
        await session.execute(text("UPDATE products SET available_count = 0 WHERE type = 'key'"))

    items = (await api.client.get("/catalog")).json()["items"]

    assert len(items) == 9
    assert "key" not in {i["type"] for i in items}
