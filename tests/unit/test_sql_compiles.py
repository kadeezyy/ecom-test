"""Компиляция всего SQL репозиториев под диалект PostgreSQL.

Тест не ходит в БД: он подставляет сессию-перехватчик и проверяет, что
каждый запрос собирается и что критичные конструкции (``FOR UPDATE``,
``SKIP LOCKED``, ``ON CONFLICT`` с частичным индексом, сравнение кортежей)
действительно попадают в SQL, а не теряются по дороге.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.ext.asyncio import AsyncSession

from shop.domain.enums import (
    DeliveryStatus,
    JobState,
    LedgerAccount,
    LedgerEventType,
    OrderStatus,
    SupplierName,
)
from shop.models import Delivery
from shop.repositories.deliveries import DeliveryRepository
from shop.repositories.jobs import DeliveryJobRepository
from shop.repositories.ledger import LedgerRepository, Posting
from shop.repositories.orders import OrderRepository
from shop.repositories.payment_events import PaymentEventRepository
from shop.repositories.products import ProductRepository
from shop.repositories.reconciliation import ReconciliationRepository

NOW = datetime.now(UTC)
# У диалектов SQLAlchemy нет аннотаций __init__ — единственное место с ignore.
PG = PGDialect()  # type: ignore[no-untyped-call]


class _FakeResult:
    def scalar_one_or_none(self) -> Any:
        return Delivery

    def scalar_one(self) -> Any:
        return 0

    def scalars(self) -> Any:
        return self

    def all(self) -> list[Any]:
        return []

    @property
    def rowcount(self) -> int:
        return 0


class _RecordingSession:
    """Сессия, которая вместо выполнения компилирует запрос."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _FakeResult:
        self.sql.append(str(statement.compile(dialect=PG)).replace("\n", " "))
        return _FakeResult()

    async def get(self, entity: Any, ident: Any) -> Any:
        return None

    def add(self, instance: Any) -> None:
        return None

    async def flush(self) -> None:
        return None


@pytest.fixture
def recorder() -> _RecordingSession:
    return _RecordingSession()


def _session(recorder: _RecordingSession) -> AsyncSession:
    return cast("AsyncSession", recorder)


async def test_запросы_по_заказам_компилируются(recorder: _RecordingSession) -> None:
    repo = OrderRepository(_session(recorder))

    await repo.get("ord_1")
    await repo.get_for_update("ord_1")
    await repo.get_by_idempotency_key("key")
    await repo.list_unsettled([OrderStatus.PAID])
    await repo.list_stuck_without_job([OrderStatus.PAID], NOW - timedelta(seconds=30))

    joined = " | ".join(recorder.sql)
    assert "FOR UPDATE" in joined, "блокировка строки заказа обязана быть в SQL"
    assert "NOT (EXISTS" in joined, "фильтр «без активной задачи»"


async def test_запросы_по_событиям_компилируются(recorder: _RecordingSession) -> None:
    repo = PaymentEventRepository(_session(recorder))

    await repo.insert_if_new(
        event_id="evt_1",
        order_id="ord_1",
        status="paid",
        amount_minor=50_000,
        currency="RUB",
        occurred_at=NOW,
        raw={"a": 1},
    )
    await repo.mark_applied("evt_1", NOW)
    await repo.mark_rejected("evt_1", "stale_event")
    await repo.mark_orphan("evt_1")
    await repo.list_pending_orphans("ord_1")
    await repo.list_pending_orphan_order_ids()
    await repo.count_pending_orphans()
    await repo.list_for_order("ord_1")

    joined = " | ".join(recorder.sql)
    assert "ON CONFLICT (event_id) DO NOTHING" in joined, "идемпотентность приёма"


async def test_запросы_по_выдачам_компилируются(recorder: _RecordingSession) -> None:
    repo = DeliveryRepository(_session(recorder))

    await repo.ensure_row(order_id="ord_1", supplier=SupplierName.A, request_id="req_ord_1_a")
    await repo.get_by_request_id("req_ord_1_a")
    await repo.list_for_order("ord_1")
    await repo.get_succeeded("ord_1")
    await repo.record_outcome(
        request_id="req_ord_1_a",
        status=DeliveryStatus.SUCCEEDED,
        code="AAAA-BBBB-CCCC",
        reason=None,
        attempted_at=NOW,
    )
    await repo.reset_known_negatives("ord_1")
    await repo.list_orders_with_success(["ord_1"])
    await repo.count_by_status(DeliveryStatus.UNKNOWN)
    await repo.list_superseded()

    joined = " | ".join(recorder.sql)
    assert "ON CONFLICT (request_id) DO NOTHING" in joined


async def test_очередь_задач_компилируется(recorder: _RecordingSession) -> None:
    repo = DeliveryJobRepository(_session(recorder))

    await repo.enqueue("ord_1")
    await repo.claim_batch(worker_id="w1", limit=10)
    await repo.finish(1, state=JobState.DONE)
    await repo.reschedule(1, delay=timedelta(seconds=5), error=None)
    await repo.requeue_stale_running(NOW)
    await repo.count_by_state(JobState.DEAD)
    await repo.list_dead()

    joined = " | ".join(recorder.sql)
    assert "FOR UPDATE SKIP LOCKED" in joined, "забор задач без гонки воркеров"
    assert "ON CONFLICT (order_id) WHERE state IN ('queued', 'running')" in joined, (
        "одна активная задача на заказ"
    )


async def test_каталог_компилируется(recorder: _RecordingSession) -> None:
    repo = ProductRepository(_session(recorder))

    await repo.get("SKU-1")
    await repo.shelf()
    await repo.shelf(type_="key", cursor_price=1000, cursor_sku="SKU-1", limit=50)
    await repo.upsert_many(
        [
            {
                "sku": "SKU-1",
                "name": "n",
                "type": "key",
                "price_minor": 100,
                "currency": "RUB",
                "image_url": None,
                "is_active": True,
            }
        ]
    )
    await repo.set_stock(["SKU-1"], 5, NOW)
    await repo.count()

    joined = " | ".join(recorder.sql)
    assert "(products.price_minor, products.sku) > " in joined, "курсорная пагинация"
    assert "ON CONFLICT (sku) DO UPDATE" in joined


async def test_журнал_и_сверка_компилируются(recorder: _RecordingSession) -> None:
    ledger = LedgerRepository(_session(recorder))
    await ledger.post(
        txn_id="txn_1",
        order_id="ord_1",
        event_type=LedgerEventType.PAYMENT_CAPTURED,
        currency="RUB",
        postings=[
            Posting(LedgerAccount.GATEWAY, 50_000),
            Posting(LedgerAccount.ORDER_LIABILITY, -50_000),
        ],
    )
    await ledger.total_balance()
    await ledger.balance_by_account()
    await ledger.open_liabilities()
    await ledger.entries_for_order("ord_1")

    recon = ReconciliationRepository(_session(recorder))
    await recon.paid_not_delivered(older_than=NOW)
    await recon.delivered_not_paid()
    await recon.unresolved_deliveries()
    await recon.superseded_codes()
    await recon.count_orders_by_status()

    joined = " | ".join(recorder.sql)
    assert "ON CONFLICT ON CONSTRAINT uq_ledger_entries_posting DO NOTHING" in joined
    assert "HAVING" in joined, "выборка незакрытых обязательств"


async def test_несбалансированная_проводка_не_записывается(
    recorder: _RecordingSession,
) -> None:
    ledger = LedgerRepository(_session(recorder))

    with pytest.raises(ValueError, match="unbalanced posting"):
        await ledger.post(
            txn_id="txn_1",
            order_id="ord_1",
            event_type=LedgerEventType.PAYMENT_CAPTURED,
            currency="RUB",
            postings=[Posting(LedgerAccount.GATEWAY, 50_000)],
        )

    assert recorder.sql == [], "ничего не ушло в БД"
