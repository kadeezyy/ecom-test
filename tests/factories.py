"""Конструкторы полезных нагрузок для тестов."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


def webhook_payload(order_id: str, **overrides: Any) -> dict[str, Any]:
    """Вебхук по контракту из ТЗ."""
    payload: dict[str, Any] = {
        "event_id": new_event_id(),
        "order_id": order_id,
        "status": "paid",
        "amount": 500,
        "currency": "RUB",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    payload.update(overrides)
    return payload
