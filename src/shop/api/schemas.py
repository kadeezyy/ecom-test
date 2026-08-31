"""Схемы запросов и ответов. Только форма данных, без бизнес-логики."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from shop.domain.enums import OrderStatus, PaymentEventStatus


class CreateOrderRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=64)


class DeliveryAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier: str
    request_id: str
    status: str
    reason: str | None = None
    attempts: int


class OrderResponse(BaseModel):
    id: str
    sku: str
    status: OrderStatus
    amount: int = Field(description="Сумма в мажорных единицах, как в контракте")
    currency: str
    code: str | None = Field(default=None, description="Выданный код (после delivered)")
    created_at: datetime
    paid_at: datetime | None = None
    delivered_at: datetime | None = None
    delivery_attempts: list[DeliveryAttemptResponse] = Field(default_factory=list)


class PaymentWebhookRequest(BaseModel):
    """Контракт вебхука из ТЗ."""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=128)
    order_id: str = Field(min_length=1, max_length=40)
    status: PaymentEventStatus
    amount: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    created_at: datetime


class PaymentWebhookResponse(BaseModel):
    """Ответ платёжной системе.

    Всегда 200, если событие принято на хранение: повторять доставку смысла
    нет. 5xx возвращается только при сбое, который повтор действительно
    может починить.
    """

    accepted: bool = True
    duplicate: bool = False
    applied: bool = False
    reason: str | None = None
    order_status: OrderStatus | None = None


class CatalogItemResponse(BaseModel):
    sku: str
    name: str
    type: str
    price: int
    currency: str
    available: int


class CatalogPageResponse(BaseModel):
    items: list[CatalogItemResponse]
    next_cursor: str | None = None


class OrderRefResponse(BaseModel):
    order_id: str
    sku: str
    status: str
    amount: int
    currency: str
    updated_at: datetime


class DeliveryRefResponse(BaseModel):
    order_id: str
    supplier: str
    request_id: str
    status: str
    reason: str | None
    attempts: int


class ReconciliationResponse(BaseModel):
    generated_at: datetime
    grace_seconds: int
    healthy: bool

    paid_not_delivered: list[OrderRefResponse]
    delivered_not_paid: list[OrderRefResponse]
    unresolved_deliveries: list[DeliveryRefResponse]
    superseded_codes: list[DeliveryRefResponse]

    pending_orphan_events: int
    dead_jobs: int
    orders_by_status: dict[str, int]

    ledger_balanced: bool
    ledger_total_balance: int
    ledger_by_account: dict[str, int]
    ledger_open_liabilities: dict[str, int]


class RetryDeliveryResponse(BaseModel):
    order_id: str
    enqueued: bool
    reset_attempts: int
    order_status: OrderStatus


class ErrorResponse(BaseModel):
    code: str
    message: str
    context: dict[str, object] = Field(default_factory=dict)
