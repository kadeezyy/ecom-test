"""Эндпоинт платёжных вебхуков."""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter

from shop.api.deps import SessionDep
from shop.api.schemas import PaymentWebhookRequest, PaymentWebhookResponse
from shop.domain.money import to_minor
from shop.services.payment_service import PaymentService, PaymentWebhookCommand

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/payment", response_model=PaymentWebhookResponse)
async def payment_webhook(
    body: PaymentWebhookRequest, session: SessionDep
) -> PaymentWebhookResponse:
    """Принимает вебхук платёжной системы.

    Ответ 200 означает «событие принято», а не «заказ изменён»: дубликат,
    расхождение суммы и событие вне порядка — тоже 200, повторять их
    бессмысленно. 5xx вернётся только при сбое БД, когда повтор поможет.
    """
    occurred_at = body.created_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)

    command = PaymentWebhookCommand(
        event_id=body.event_id,
        order_id=body.order_id,
        status=body.status,
        amount_minor=to_minor(body.amount),
        currency=body.currency.upper(),
        occurred_at=occurred_at,
        raw=body.model_dump(mode="json"),
    )
    result = await PaymentService(session).handle_webhook(command)
    return PaymentWebhookResponse(
        accepted=True,
        duplicate=result.duplicate,
        applied=result.applied,
        reason=result.reason,
        order_status=result.order_status,
    )
