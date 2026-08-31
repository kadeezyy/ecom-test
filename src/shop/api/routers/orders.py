"""Эндпоинты заказов."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, status

from shop.api.deps import SessionDep
from shop.api.presenters import order_to_response
from shop.api.schemas import CreateOrderRequest, OrderResponse
from shop.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrderResponse)
async def create_order(
    body: CreateOrderRequest,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OrderResponse:
    """Создаёт заказ по SKU."""
    service = OrderService(session)
    order = await service.create_order(sku=body.sku, idempotency_key=idempotency_key)
    code = await service.get_delivered_code(order.id)
    attempts = await service.list_deliveries(order.id)
    return order_to_response(order, code=code, attempts=attempts)


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(order_id: str, session: SessionDep) -> OrderResponse:
    """Возвращает заказ, его статус и выданный код."""
    service = OrderService(session)
    order = await service.get_order(order_id)
    code = await service.get_delivered_code(order_id)
    attempts = await service.list_deliveries(order_id)
    return order_to_response(order, code=code, attempts=attempts)
