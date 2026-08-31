"""Выдача товара и очередь фоновых задач."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shop.domain.enums import DeliveryStatus, JobState
from shop.models.base import Base, TimestampMixin


class Delivery(Base, TimestampMixin):
    """Попытка выдачи у конкретного поставщика.

    На заказ приходится не более одной строки на поставщика, и её
    ``request_id`` детерминирован — поэтому любой повтор (в том числе после
    таймаута) обращается к поставщику с тем же идентификатором и получает
    тот же код.
    """

    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(40), ForeignKey("orders.id"), nullable=False)
    supplier: Mapped[str] = mapped_column(String(8), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[DeliveryStatus] = mapped_column(String(24), nullable=False)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_deliveries_order_id", "order_id"),
        # ГЛАВНЫЙ рубеж exactly-once: у заказа не может быть двух успешных
        # выдач. Даже если вся логика приложения ошибётся, БД не даст.
        Index(
            "uq_deliveries_one_success_per_order",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
        # Один и тот же код не может уйти в два заказа.
        Index(
            "uq_deliveries_code",
            "code",
            unique=True,
            postgresql_where=text("code IS NOT NULL AND status = 'succeeded'"),
        ),
    )


class DeliveryJob(Base, TimestampMixin):
    """Задача фоновой выдачи.

    Очередь живёт в той же БД, что и заказы: постановка задачи и перевод
    заказа в ``paid`` происходят в одной транзакции, поэтому потерять или
    задвоить задачу невозможно. Забор — ``FOR UPDATE SKIP LOCKED``.
    """

    __tablename__ = "delivery_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(40), ForeignKey("orders.id"), nullable=False)
    state: Mapped[JobState] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # 50 параллельных вебхуков -> максимум одна активная задача на заказ.
        Index(
            "uq_delivery_jobs_active_per_order",
            "order_id",
            unique=True,
            postgresql_where=text("state IN ('queued', 'running')"),
        ),
        Index(
            "ix_delivery_jobs_claimable",
            "run_after",
            postgresql_where=text("state = 'queued'"),
        ),
    )
