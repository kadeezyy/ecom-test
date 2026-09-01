from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shop.domain.enums import OrderStatus
from shop.models.base import Base, TimestampMixin


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), ForeignKey("products.sku"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(String(32), nullable=False)

    #: Ключ идемпотентности создания заказа (заголовок Idempotency-Key).
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    #: Момент последнего *применённого* платёжного события. Защита от
    #: вебхуков, пришедших не по порядку: более старое событие игнорируется.
    last_payment_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Сверка и добиватель ходят по «незавершённым» заказам.
        Index("ix_orders_status_updated_at", "status", "updated_at"),
    )


class PaymentEvent(Base):
    """Вебхук платёжной системы.

    ``event_id`` — первичный ключ: приём события идемпотентен на уровне БД,
    повторная доставка не может создать вторую строку.

    Внешнего ключа на ``orders`` намеренно нет: вебхук может прийти раньше
    заказа, такое событие сохраняется как «осиротевшее» и применяется позже.
    """

    __tablename__ = "payment_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    #: created_at из полезной нагрузки — по нему определяется порядок событий.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Событие пришло раньше заказа и ждёт его появления.
    orphan: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    #: Почему событие не изменило заказ: stale / already_paid / amount_mismatch...
    rejected_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_payment_events_order_id", "order_id"),
        Index(
            "ix_payment_events_pending_orphans",
            "order_id",
            postgresql_where=text("orphan AND NOT applied"),
        ),
    )
