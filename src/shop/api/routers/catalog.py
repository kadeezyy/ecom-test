"""Витрина."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from shop.api.deps import SessionDep
from shop.api.presenters import shelf_to_response
from shop.api.schemas import CatalogPageResponse
from shop.services.catalog_service import CatalogService

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("", response_model=CatalogPageResponse)
async def get_catalog(
    session: SessionDep,
    type: Annotated[str | None, Query(max_length=32)] = None,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CatalogPageResponse:
    """Список доступных товаров с курсорной пагинацией."""
    page = await CatalogService(session).shelf(type_=type, cursor=cursor, limit=limit)
    return shelf_to_response(page)
