"""Начальная схема

Revision ID: 0001
Revises:
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("image_url", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("available_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("stock_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sku", name=op.f("pk_products")),
    )
    # Этап 5: покрывающие частичные индексы витрины.
    op.create_index(
        "ix_products_shelf",
        "products",
        ["price_minor", "sku"],
        unique=False,
        postgresql_include=["name", "type", "currency", "available_count"],
        postgresql_where=sa.text("is_active AND available_count > 0"),
    )
    op.create_index(
        "ix_products_shelf_by_type",
        "products",
        ["type", "price_minor", "sku"],
        unique=False,
        postgresql_include=["name", "currency", "available_count"],
        postgresql_where=sa.text("is_active AND available_count > 0"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("last_payment_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["sku"], ["products.sku"], name=op.f("fk_orders_sku")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_orders_idempotency_key")),
    )
    op.create_index("ix_orders_status_updated_at", "orders", ["status", "updated_at"], unique=False)

    op.create_table(
        "payment_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("orphan", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rejected_reason", sa.String(length=64), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # Внешнего ключа на orders нет намеренно: вебхук может прийти раньше заказа.
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_payment_events")),
    )
    op.create_index("ix_payment_events_order_id", "payment_events", ["order_id"], unique=False)
    op.create_index(
        "ix_payment_events_pending_orphans",
        "payment_events",
        ["order_id"],
        unique=False,
        postgresql_where=sa.text("orphan AND NOT applied"),
    )

    op.create_table(
        "deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("supplier", sa.String(length=8), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_deliveries_order_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deliveries")),
        sa.UniqueConstraint("request_id", name=op.f("uq_deliveries_request_id")),
    )
    op.create_index("ix_deliveries_order_id", "deliveries", ["order_id"], unique=False)
    # Главный рубеж exactly-once: не более одной успешной выдачи на заказ.
    op.create_index(
        "uq_deliveries_one_success_per_order",
        "deliveries",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )
    # Один код не может уйти в два заказа.
    op.create_index(
        "uq_deliveries_code",
        "deliveries",
        ["code"],
        unique=True,
        postgresql_where=sa.text("code IS NOT NULL AND status = 'succeeded'"),
    )

    op.create_table(
        "delivery_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_delivery_jobs_order_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_jobs")),
    )
    # Не более одной активной задачи на заказ: 50 вебхуков -> одна выдача.
    op.create_index(
        "uq_delivery_jobs_active_per_order",
        "delivery_jobs",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_delivery_jobs_claimable",
        "delivery_jobs",
        ["run_after"],
        unique=False,
        postgresql_where=sa.text("state = 'queued'"),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("txn_id", sa.String(length=40), nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("account", sa.String(length=32), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        # Идемпотентность проводок.
        sa.UniqueConstraint("order_id", "event_type", "account", name="uq_ledger_entries_posting"),
    )
    op.create_index("ix_ledger_entries_order_id", "ledger_entries", ["order_id"], unique=False)
    op.create_index("ix_ledger_entries_account", "ledger_entries", ["account"], unique=False)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        "ix_audit_log_order_id_created_at", "audit_log", ["order_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("ledger_entries")
    op.drop_table("delivery_jobs")
    op.drop_table("deliveries")
    op.drop_table("payment_events")
    op.drop_table("orders")
    op.drop_table("products")
