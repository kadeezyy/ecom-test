"""Этап 4: сверка и ручное восстановление."""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN_TOKEN, ApiHarness, RunningStub

pytestmark = pytest.mark.db


async def test_сверка_требует_токен(api: ApiHarness) -> None:
    assert (await api.client.get("/admin/reconcile")).status_code == 401
    assert (
        await api.client.get("/admin/reconcile", headers={"X-Admin-Token": "wrong"})
    ).status_code == 401


async def test_чистая_система_сходится(api: ApiHarness) -> None:
    order = await api.create_order("STEAM-TOPUP-500")
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    report = await api.reconcile()

    assert report["healthy"] is True
    assert report["ledger_balanced"] is True
    assert report["ledger_total_balance"] == 0
    assert report["paid_not_delivered"] == []
    assert report["delivered_not_paid"] == []
    assert report["ledger_by_account"]["gateway"] == 50_000
    assert report["ledger_by_account"]["revenue"] == -50_000
    assert report["ledger_by_account"]["order_liability"] == 0


async def test_оплачен_но_не_выдан_попадает_в_отчёт(
    api: ApiHarness, stub_a: RunningStub, stub_b: RunningStub
) -> None:
    await stub_a.drain()
    await stub_b.drain()

    order = await api.create_order("STEAM-TOPUP-500")
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    report = await api.reconcile(grace_seconds=0)

    assert [o["order_id"] for o in report["paid_not_delivered"]] == [order["id"]]
    # Деньги взяли, товар не отдали — обязательство висит в журнале.
    assert report["ledger_open_liabilities"][order["id"]] == -50_000
    assert report["ledger_balanced"] is True, "журнал сходится даже при сбое выдачи"


async def test_неизвестные_исходы_видны_в_отчёте(api: ApiHarness, stub_a: RunningStub) -> None:
    await stub_a.configure(mode="timeout", hang_seconds=1.0)

    order = await api.create_order("STEAM-TOPUP-500")
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    report = await api.reconcile(grace_seconds=0)

    unresolved = report["unresolved_deliveries"]
    assert len(unresolved) == 1
    assert unresolved[0]["order_id"] == order["id"]
    assert unresolved[0]["supplier"] == "a"
    assert unresolved[0]["reason"] == "read_timeout"


async def test_повторная_выдача_требует_токен(api: ApiHarness) -> None:
    order = await api.create_order("STEAM-TOPUP-500")

    response = await api.client.post(f"/admin/orders/{order['id']}/retry-delivery")

    assert response.status_code == 401


async def test_повторная_выдача_несуществующего_заказа(api: ApiHarness) -> None:
    response = await api.client.post(
        "/admin/orders/ord_missing/retry-delivery",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )

    assert response.status_code == 404
