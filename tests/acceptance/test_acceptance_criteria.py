"""Шесть критериев приёмки из ТЗ — по одному тесту на пункт.

Плюс отдельная проверка (2 теста в разделе критерия 4) на то, ради чего
задание и написано: неизвестный исход **не** должен приводить к обращению
ко второму поставщику.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shop.domain.enums import DeliveryStatus, OrderStatus
from shop.models import Delivery, DeliveryJob, LedgerEntry, Order, PaymentEvent
from tests.conftest import ADMIN_TOKEN, ApiHarness, RunningStub
from tests.factories import webhook_payload

pytestmark = pytest.mark.db

SKU = "STEAM-TOPUP-500"
AMOUNT_MINOR = 50_000


def _succeeded(order: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in order["delivery_attempts"] if a["status"] == "succeeded"]


async def _count(db: AsyncSession, model: Any, **filters: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for column, value in filters.items():
        stmt = stmt.where(getattr(model, column) == value)
    return int((await db.execute(stmt)).scalar_one())


async def _ledger_balance(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)))
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# Критерий 1: 50 параллельных вебхуков -> ровно одна выдача
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_ac1_пятьдесят_параллельных_вебхуков_дают_одну_выдачу(
    api: ApiHarness, db: AsyncSession, stub_a: RunningStub
) -> None:
    order = await api.create_order(SKU)
    payloads = [webhook_payload(order["id"], amount=order["amount"]) for _ in range(50)]

    responses = await asyncio.gather(
        *(api.client.post("/webhooks/payment", json=p) for p in payloads)
    )

    assert {r.status_code for r in responses} == {200}, "все вебхуки приняты"
    applied = [r for r in responses if r.json()["applied"]]
    assert len(applied) == 1, "заказ оплачен ровно одним событием"

    assert await _count(db, PaymentEvent) == 50, "ни одно событие не потеряно"
    assert await _count(db, DeliveryJob, order_id=order["id"]) == 1, "одна задача выдачи"

    await api.deliver()

    final = await api.get_order(order["id"])
    assert final["status"] == "delivered"
    assert final["code"]
    assert len(_succeeded(final)) == 1, "ровно один факт выдачи"
    assert await stub_a.issued_count() == 1, "у поставщика израсходован один ключ"

    # Деньги сходятся: получено 500 ₽, признано выручкой 500 ₽, долгов нет.
    assert await _ledger_balance(db) == 0
    assert await _count(db, LedgerEntry, order_id=order["id"]) == 4


# ---------------------------------------------------------------------------
# Критерий 2: повтор того же event_id ничего не меняет
# ---------------------------------------------------------------------------


async def test_ac2_повтор_одного_event_id_ничего_не_меняет(
    api: ApiHarness, db: AsyncSession, stub_a: RunningStub
) -> None:
    order = await api.create_order(SKU)
    payload = webhook_payload(order["id"], amount=order["amount"])

    responses = await asyncio.gather(
        *(api.client.post("/webhooks/payment", json=payload) for _ in range(50))
    )

    assert {r.status_code for r in responses} == {200}
    bodies = [r.json() for r in responses]
    assert sum(b["applied"] for b in bodies) == 1
    assert sum(b["duplicate"] for b in bodies) == 49

    assert await _count(db, PaymentEvent) == 1, "повторы не создают новых событий"
    assert await _count(db, DeliveryJob, order_id=order["id"]) == 1

    await api.deliver()

    final = await api.get_order(order["id"])
    assert final["status"] == "delivered"
    assert len(_succeeded(final)) == 1
    assert await stub_a.issued_count() == 1

    # Повтор уже после выдачи тоже ничего не меняет.
    repeat = await api.client.post("/webhooks/payment", json=payload)
    assert repeat.json()["duplicate"] is True
    assert (await api.get_order(order["id"]))["code"] == final["code"]


# ---------------------------------------------------------------------------
# Критерий 3: вебхук вне порядка / раньше заказа
# ---------------------------------------------------------------------------


async def test_ac3_вебхук_пришедший_раньше_заказа_применяется_позже(
    api: ApiHarness,
    db: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Событие сохраняется как «осиротевшее» и доигрывается добивателем."""
    order_id = "ord_early_webhook"

    response = await api.client.post(
        "/webhooks/payment", json=webhook_payload(order_id, amount=500)
    )
    assert response.status_code == 200, "платёжка не должна получать 4xx/5xx"
    assert response.json()["reason"] == "order_not_found_yet"

    event = (await db.execute(select(PaymentEvent))).scalars().one()
    assert event.orphan is True
    assert event.applied is False

    # Заказ появляется позже (в бою — параллельная транзакция создания).
    async with sessions() as session, session.begin():
        session.add(
            Order(
                id=order_id,
                sku=SKU,
                amount_minor=AMOUNT_MINOR,
                currency="RUB",
                status=OrderStatus.CREATED,
            )
        )

    await api.worker.sweep_once()

    assert (await api.get_order(order_id))["status"] == "paid"
    await api.deliver()

    final = await api.get_order(order_id)
    assert final["status"] == "delivered"
    assert len(_succeeded(final)) == 1
    assert await _ledger_balance(db) == 0


async def test_ac3_события_вне_порядка_не_ломают_заказ(api: ApiHarness) -> None:
    """`failed` со старой меткой времени не отменяет уже применённый `paid`."""
    from datetime import UTC, datetime, timedelta

    order = await api.create_order(SKU)
    now = datetime.now(UTC)

    await api.pay(
        order["id"],
        amount=order["amount"],
        created_at=now.isoformat().replace("+00:00", "Z"),
    )
    stale = await api.pay(
        order["id"],
        amount=order["amount"],
        status="failed",
        created_at=(now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    )

    assert stale.json()["reason"] == "stale_event"
    await api.deliver()
    assert (await api.get_order(order["id"]))["status"] == "delivered"


# ---------------------------------------------------------------------------
# Критерий 4: таймаут поставщика, который на самом деле выдал код
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_ac4_таймаут_не_приводит_к_фолбэку_на_второго_поставщика(
    api: ApiHarness, db: AsyncSession, stub_a: RunningStub, stub_b: RunningStub
) -> None:
    """Главная ловушка задания.

    A выдаёт код и зависает. Обращаться к B нельзя — код уже мог уйти
    покупателю. Единственный допустимый ход — повтор к A с тем же request_id.
    """
    await stub_a.configure(mode="timeout", hang_seconds=1.5)

    order = await api.create_order(SKU)
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    assert await stub_a.issued_count() == 1, "A успел закрепить код за request_id"
    assert await stub_b.issued_count() == 0, "ФОЛБЭК ЗАПРЕЩЁН при неизвестном исходе"

    state = await api.get_order(order["id"])
    assert state["status"] != "delivered", "нельзя объявлять выдачу без ответа"
    assert state["code"] is None

    attempts = {a["supplier"]: a for a in state["delivery_attempts"]}
    assert attempts["a"]["status"] == DeliveryStatus.UNKNOWN
    assert "b" not in attempts, "к поставщику B даже не обращались"

    # Заказ не потерян: он в восстановимом состоянии и виден в сверке.
    report = await api.reconcile(grace_seconds=0)
    assert len(report["unresolved_deliveries"]) == 1
    assert report["ledger_balanced"] is True


@pytest.mark.slow
async def test_ac4_повтор_после_таймаута_не_создаёт_вторую_выдачу(
    api: ApiHarness, db: AsyncSession, stub_a: RunningStub, stub_b: RunningStub
) -> None:
    await stub_a.configure(mode="timeout", hang_seconds=1.5)

    order = await api.create_order(SKU)
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    issued_code = (await stub_a.state())["issued"][0]["code"]

    # Поставщик ожил. Повтор идёт к нему же и с тем же request_id.
    await stub_a.configure(mode="ok")
    retry = await api.client.post(
        f"/admin/orders/{order['id']}/retry-delivery",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert retry.status_code == 200
    await api.deliver()

    final = await api.get_order(order["id"])
    assert final["status"] == "delivered"
    assert final["code"] == issued_code, "выдан тот самый код, а не новый"

    assert await stub_a.issued_count() == 1, "ключ израсходован ровно один раз"
    assert await stub_b.issued_count() == 0
    assert await _count(db, Delivery, order_id=order["id"]) == 1
    assert len(_succeeded(final)) == 1
    assert await _ledger_balance(db) == 0


# ---------------------------------------------------------------------------
# Критерий 5: поставщик A недоступен -> фолбэк на B
# ---------------------------------------------------------------------------


async def test_ac5_доказанный_отказ_a_даёт_фолбэк_на_b(
    api: ApiHarness, db: AsyncSession, stub_a: RunningStub, stub_b: RunningStub
) -> None:
    await stub_a.configure(mode="error")

    order = await api.create_order(SKU)
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    final = await api.get_order(order["id"])
    assert final["status"] == "delivered"
    assert final["code"]

    assert await stub_a.issued_count() == 0, "A ничего не выдавал"
    assert await stub_b.issued_count() == 1, "выдал B, ровно один раз"
    assert len(_succeeded(final)) == 1

    attempts = {a["supplier"]: a for a in final["delivery_attempts"]}
    assert attempts["a"]["status"] == DeliveryStatus.KNOWN_NEGATIVE
    assert attempts["b"]["status"] == DeliveryStatus.SUCCEEDED
    assert await _ledger_balance(db) == 0


async def test_ac5_недоступный_a_тоже_даёт_фолбэк(
    api: ApiHarness, stub_a: RunningStub, stub_b: RunningStub
) -> None:
    """Соединение не установилось — доказано, что запрос не дошёл."""
    from shop.core.config import get_settings
    from shop.domain.enums import SupplierName
    from shop.integrations.supplier.client import SupplierClient

    assert get_settings().supplier_b_url == stub_b.url

    # Подменяем клиента A на «мёртвый» адрес: соединение не установится.
    dead = SupplierClient(
        name=SupplierName.A,
        base_url="http://127.0.0.1:1",
        connect_timeout_s=0.2,
        read_timeout_s=0.2,
        max_attempts=1,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
    )
    api.worker.suppliers.replace(SupplierName.A, dead)

    order = await api.create_order(SKU)
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    final = await api.get_order(order["id"])
    assert final["status"] == "delivered"
    assert await stub_b.issued_count() == 1
    assert len(_succeeded(final)) == 1


# ---------------------------------------------------------------------------
# Критерий 6: пустой остаток — восстановимое состояние, без падения
# ---------------------------------------------------------------------------


async def test_ac6_пустой_остаток_даёт_восстановимое_состояние(
    api: ApiHarness, db: AsyncSession, stub_a: RunningStub, stub_b: RunningStub
) -> None:
    await stub_a.drain()
    await stub_b.drain()

    order = await api.create_order(SKU)
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()

    state = await api.get_order(order["id"])
    assert state["status"] == OrderStatus.OUT_OF_STOCK, "не падение, а состояние"
    assert state["code"] is None
    attempts = {a["supplier"]: a for a in state["delivery_attempts"]}
    assert attempts["a"]["reason"] == "out_of_stock"
    assert attempts["b"]["reason"] == "out_of_stock"

    # Деньги при этом никуда не делись и журнал сходится.
    assert await _ledger_balance(db) == 0
    report = await api.reconcile(grace_seconds=0)
    assert report["ledger_open_liabilities"][order["id"]] == -AMOUNT_MINOR

    # Остаток пополнили — заказ безопасно доводится до конца.
    await stub_a.restock(["RSTK-RSTK-RSTK"])
    await api.client.post(
        f"/admin/orders/{order['id']}/retry-delivery",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    await api.deliver()

    final = await api.get_order(order["id"])
    assert final["status"] == "delivered"
    assert final["code"] == "RSTK-RSTK-RSTK"
    assert len(_succeeded(final)) == 1
    assert await _ledger_balance(db) == 0
    assert (await api.reconcile(grace_seconds=0))["healthy"] is True


async def test_ac6_добиватель_сам_поднимает_зависший_заказ(
    api: ApiHarness, stub_a: RunningStub, stub_b: RunningStub
) -> None:
    """Восстановление не требует ручного вмешательства."""
    await stub_a.drain()
    await stub_b.drain()

    order = await api.create_order(SKU)
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()
    assert (await api.get_order(order["id"]))["status"] == OrderStatus.OUT_OF_STOCK

    await stub_a.restock(["SWEP-SWEP-SWEP"])
    # Порог «зависшего» заказа обнуляем, чтобы не ждать в тесте.
    api.worker.settings.stuck_order_age_s = 0
    await api.worker.sweep_once()
    await api.deliver()

    final = await api.get_order(order["id"])
    assert final["status"] == "delivered"
    assert final["code"] == "SWEP-SWEP-SWEP"
    assert len(_succeeded(final)) == 1
