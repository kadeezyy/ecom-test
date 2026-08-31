"""Преобразование доменных объектов в ответы API.

Единственное место, где минорные единицы превращаются в мажорные (контракт
ТЗ оперирует целыми рублями).
"""

from __future__ import annotations

from collections.abc import Sequence

from shop.api.schemas import (
    CatalogItemResponse,
    CatalogPageResponse,
    DeliveryAttemptResponse,
    DeliveryRefResponse,
    OrderRefResponse,
    OrderResponse,
    ReconciliationResponse,
)
from shop.domain.enums import OrderStatus
from shop.domain.money import to_major
from shop.models import Delivery, Order
from shop.repositories.reconciliation import DeliveryRef, OrderRef
from shop.services.catalog_service import ShelfPage
from shop.services.reconciliation_service import ReconciliationReport


def order_to_response(
    order: Order, *, code: str | None, attempts: Sequence[Delivery] = ()
) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        sku=order.sku,
        status=OrderStatus(order.status),
        amount=to_major(order.amount_minor),
        currency=order.currency,
        code=code,
        created_at=order.created_at,
        paid_at=order.paid_at,
        delivered_at=order.delivered_at,
        delivery_attempts=[
            DeliveryAttemptResponse(
                supplier=a.supplier,
                request_id=a.request_id,
                status=str(a.status),
                reason=a.reason,
                attempts=a.attempts,
            )
            for a in attempts
        ],
    )


def shelf_to_response(page: ShelfPage) -> CatalogPageResponse:
    return CatalogPageResponse(
        items=[
            CatalogItemResponse(
                sku=i.sku,
                name=i.name,
                type=i.type,
                price=to_major(i.price_minor),
                currency=i.currency,
                available=i.available_count,
            )
            for i in page.items
        ],
        next_cursor=page.next_cursor,
    )


def report_to_response(report: ReconciliationReport) -> ReconciliationResponse:
    return ReconciliationResponse(
        generated_at=report.generated_at,
        grace_seconds=report.grace_seconds,
        healthy=report.healthy,
        paid_not_delivered=[_order_ref(r) for r in report.paid_not_delivered],
        delivered_not_paid=[_order_ref(r) for r in report.delivered_not_paid],
        unresolved_deliveries=[_delivery_ref(r) for r in report.unresolved_deliveries],
        superseded_codes=[_delivery_ref(r) for r in report.superseded_codes],
        pending_orphan_events=report.pending_orphan_events,
        dead_jobs=report.dead_jobs,
        orders_by_status=report.orders_by_status,
        ledger_balanced=report.ledger_balanced,
        ledger_total_balance=report.ledger_total_balance,
        ledger_by_account=report.ledger_by_account,
        ledger_open_liabilities=dict(report.ledger_open_liabilities),
    )


def _order_ref(ref: OrderRef) -> OrderRefResponse:
    return OrderRefResponse(
        order_id=ref.order_id,
        sku=ref.sku,
        status=ref.status,
        amount=to_major(ref.amount_minor),
        currency=ref.currency,
        updated_at=ref.updated_at,
    )


def _delivery_ref(ref: DeliveryRef) -> DeliveryRefResponse:
    return DeliveryRefResponse(
        order_id=ref.order_id,
        supplier=ref.supplier,
        request_id=ref.request_id,
        status=ref.status,
        reason=ref.reason,
        attempts=ref.attempts,
    )
