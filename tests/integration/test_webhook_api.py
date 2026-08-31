"""Этап 1–2: приём платёжных вебхуков."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shop.models import DeliveryJob, PaymentEvent
from tests.conftest import ApiHarness
from tests.factories import new_event_id, webhook_payload

pytestmark = pytest.mark.db


async def test_оплата_переводит_заказ_в_paid_и_ставит_задачу(
    api: ApiHarness, db: AsyncSession
) -> None:
    order = await api.create_order("STEAM-TOPUP-500")

    response = await api.pay(order["id"], amount=order["amount"])

    assert response.status_code == 200
    assert response.json()["applied"] is True
    assert response.json()["order_status"] == "paid"

    jobs = (
        (await db.execute(select(DeliveryJob).where(DeliveryJob.order_id == order["id"])))
        .scalars()
        .all()
    )
    assert len(jobs) == 1
    assert jobs[0].state == "queued"


async def test_неуспешная_оплата_переводит_в_payment_failed(api: ApiHarness) -> None:
    order = await api.create_order("STEAM-TOPUP-500")

    await api.pay(order["id"], amount=order["amount"], status="failed")

    assert (await api.get_order(order["id"]))["status"] == "payment_failed"


async def test_вебхук_по_несуществующему_заказу_отвечает_200(api: ApiHarness) -> None:
    """404 или 5xx заставили бы платёжку повторять доставку бесконечно."""
    response = await api.client.post(
        "/webhooks/payment", json=webhook_payload("ord_does_not_exist")
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "order_not_found_yet"


async def test_расхождение_суммы_не_переводит_в_paid(api: ApiHarness, db: AsyncSession) -> None:
    order = await api.create_order("KEY-EFT")  # 3490

    response = await api.pay(order["id"], amount=1)

    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert response.json()["reason"] == "amount_mismatch"
    assert (await api.get_order(order["id"]))["status"] == "created"

    event = (await db.execute(select(PaymentEvent))).scalars().one()
    assert event.rejected_reason == "amount_mismatch"


async def test_чужая_валюта_не_переводит_в_paid(api: ApiHarness) -> None:
    order = await api.create_order("KEY-EFT")

    response = await api.pay(order["id"], amount=order["amount"], currency="USD")

    assert response.json()["reason"] == "amount_mismatch"
    assert (await api.get_order(order["id"]))["status"] == "created"


async def test_событие_вне_порядка_не_откатывает_оплату(api: ApiHarness) -> None:
    """Более старый `failed` не должен отменять уже применённый `paid`."""
    order = await api.create_order("STEAM-TOPUP-500")
    now = datetime.now(UTC)

    await api.pay(
        order["id"],
        amount=order["amount"],
        created_at=now.isoformat().replace("+00:00", "Z"),
    )
    late = await api.pay(
        order["id"],
        amount=order["amount"],
        status="failed",
        created_at=(now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    )

    assert late.json()["applied"] is False
    assert late.json()["reason"] == "stale_event"
    assert (await api.get_order(order["id"]))["status"] == "paid"


async def test_повторная_оплата_не_меняет_финальный_заказ(api: ApiHarness) -> None:
    order = await api.create_order("STEAM-TOPUP-500")
    await api.pay(order["id"], amount=order["amount"])
    await api.deliver()
    assert (await api.get_order(order["id"]))["status"] == "delivered"

    late = await api.pay(order["id"], amount=order["amount"])

    assert late.json()["applied"] is False
    assert late.json()["reason"] == "order_final"
    assert (await api.get_order(order["id"]))["status"] == "delivered"


async def test_невалидный_статус_отклоняется_валидацией(api: ApiHarness) -> None:
    order = await api.create_order("STEAM-TOPUP-500")
    response = await api.client.post(
        "/webhooks/payment",
        json=webhook_payload(order["id"], status="refunded"),
    )

    assert response.status_code == 422


async def test_событие_сохраняется_целиком_для_аудита(api: ApiHarness, db: AsyncSession) -> None:
    order = await api.create_order("STEAM-TOPUP-500")
    event_id = new_event_id()

    await api.pay(order["id"], amount=order["amount"], event_id=event_id)

    event = await db.get(PaymentEvent, event_id)
    assert event is not None
    assert event.applied is True
    assert event.raw["order_id"] == order["id"]
