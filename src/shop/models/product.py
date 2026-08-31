"""Каталог товаров."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from shop.models.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    """Товар витрины.

    ``available_count`` намеренно денормализован: остаток живёт у поставщика,
    а витрине нужен один быстрый запрос без обращений к внешней системе.
    Поле обновляет фоновая задача синхронизации остатков.
    """

    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    image_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    available_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    stock_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Этап 5. Два частичных покрывающих индекса под «горячий» запрос
        # витрины. Партиальность выкидывает из индекса неактивные и
        # распроданные SKU, INCLUDE делает скан index-only (без обращения
        # к куче), а (price_minor, sku) даёт стабильный порядок и курсорную
        # пагинацию без OFFSET.
        Index(
            "ix_products_shelf",
            "price_minor",
            "sku",
            postgresql_include=["name", "type", "currency", "available_count"],
            postgresql_where=text("is_active AND available_count > 0"),
        ),
        Index(
            "ix_products_shelf_by_type",
            "type",
            "price_minor",
            "sku",
            postgresql_include=["name", "currency", "available_count"],
            postgresql_where=text("is_active AND available_count > 0"),
        ),
    )
