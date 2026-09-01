from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from shop.core.config import Settings, get_settings
from shop.core.db import session_scope
from shop.core.exceptions import Unauthorized
from shop.integrations.supplier.registry import SupplierRegistry
from shop.services.delivery_service import DeliveryService


async def get_session() -> AsyncIterator[AsyncSession]:
    """Транзакция на запрос: commit при успехе, rollback при исключении."""
    async with session_scope() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_supplier_registry(request: Request) -> SupplierRegistry:
    registry: SupplierRegistry = request.app.state.suppliers
    return registry


def get_delivery_service(request: Request) -> DeliveryService:
    service: DeliveryService = request.app.state.delivery_service
    return service


DeliveryServiceDep = Annotated[DeliveryService, Depends(get_delivery_service)]


async def require_admin(
    settings: SettingsDep,
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    if x_admin_token is None or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise Unauthorized("valid X-Admin-Token header is required")


AdminDep = Annotated[None, Depends(require_admin)]
