from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from shop.api.deps import AdminDep, DeliveryServiceDep, SessionDep
from shop.api.presenters import report_to_response
from shop.api.schemas import ReconciliationResponse, RetryDeliveryResponse
from shop.core.logging import get_logger
from shop.domain.enums import OrderStatus
from shop.repositories.jobs import DeliveryJobRepository
from shop.services.order_service import OrderService
from shop.services.reconciliation_service import ReconciliationService

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/reconcile", response_model=ReconciliationResponse)
async def reconcile(
    session: SessionDep,
    _: AdminDep,
    grace_seconds: Annotated[int, Query(ge=0, le=86400)] = 60,
) -> ReconciliationResponse:
    """Отчёт сверки: «оплачен, но не выдан» и «выдан, но не оплачен»."""
    report = await ReconciliationService(session).build_report(grace_seconds=grace_seconds)
    return report_to_response(report)


@router.post("/orders/{order_id}/retry-delivery", response_model=RetryDeliveryResponse)
async def retry_delivery(
    order_id: str,
    session: SessionDep,
    deliveries: DeliveryServiceDep,
    _: AdminDep,
) -> RetryDeliveryResponse:
    """Безопасно перезапускает выдачу восстановимого заказа.

    Сбрасывает только доказанные отказы — попытки с неизвестным исходом
    остаются, чтобы повтор ушёл тому же поставщику с тем же ``request_id``.
    """
    order = await OrderService(session).get_order(order_id)
    reset = await deliveries.prepare_recovery(order_id)
    enqueued = await DeliveryJobRepository(session).enqueue(order_id)

    logger.info(
        "delivery_retry_requested",
        order_id=order_id,
        order_status=str(order.status),
        reset_attempts=reset,
        job_enqueued=enqueued,
    )
    return RetryDeliveryResponse(
        order_id=order_id,
        enqueued=enqueued,
        reset_attempts=reset,
        order_status=OrderStatus(order.status),
    )
