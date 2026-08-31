"""Журнал денежных движений и аудит переходов."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shop.models.base import Base


class LedgerEntry(Base):
    """Проводка.

    Проводки пишутся парами с противоположными знаками, поэтому сумма
    ``amount_minor`` по любому ``txn_id`` — и по всей таблице — равна нулю.
    Это и есть «журнал, который всегда сходится».
    """

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    txn_id: Mapped[str] = mapped_column(String(40), nullable=False)
    order_id: Mapped[str] = mapped_column(String(40), nullable=False)
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Знаковая сумма в минорных единицах.
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        # Идемпотентность проводок: повторное применение того же события по
        # тому же заказу физически не может продублировать сумму.
        UniqueConstraint("order_id", "event_type", "account", name="uq_ledger_entries_posting"),
        Index("ix_ledger_entries_order_id", "order_id"),
        Index("ix_ledger_entries_account", "account"),
    )


class AuditLog(Base):
    """Журнал значимых событий заказа (переходы статусов, попытки выдачи)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(40), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    __table_args__ = (Index("ix_audit_log_order_id_created_at", "order_id", "created_at"),)
