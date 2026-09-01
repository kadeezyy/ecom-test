from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from shop.core.exceptions import DomainError
from shop.repositories.products import ProductRepository, ShelfItem

MAX_PAGE_SIZE = 100


class InvalidCursor(DomainError):
    code = "invalid_cursor"
    http_status = 400


@dataclass(frozen=True, slots=True)
class ShelfPage:
    items: list[ShelfItem]
    next_cursor: str | None


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self._products = ProductRepository(session)

    async def shelf(
        self, *, type_: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> ShelfPage:
        limit = min(max(limit, 1), MAX_PAGE_SIZE)
        cursor_price, cursor_sku = _decode_cursor(cursor)

        items = await self._products.shelf(
            type_=type_,
            cursor_price=cursor_price,
            cursor_sku=cursor_sku,
            limit=limit,
        )
        next_cursor = (
            _encode_cursor(items[-1].price_minor, items[-1].sku) if len(items) == limit else None
        )
        return ShelfPage(items=items, next_cursor=next_cursor)


def _encode_cursor(price_minor: int, sku: str) -> str:
    raw = f"{price_minor}:{sku}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[int | None, str | None]:
    if not cursor:
        return None, None
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded).decode()
        price_str, sku = raw.split(":", 1)
        return int(price_str), sku
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursor("malformed cursor", cursor=cursor) from exc
