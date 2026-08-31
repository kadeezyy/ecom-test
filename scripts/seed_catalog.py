"""Сид каталога из data/catalog.json (12 SKU из задания).

python scripts/seed_catalog.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from shop.core.config import get_settings
from shop.core.db import dispose_engine, get_sessionmaker, init_engine
from shop.core.logging import configure_logging
from shop.domain.money import to_minor
from shop.repositories.products import ProductRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_FILE = REPO_ROOT / "data" / "catalog.json"


def _rows() -> list[dict[str, Any]]:
    payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    return [
        {
            "sku": p["sku"],
            "name": p["name"],
            "type": p["type"],
            "price_minor": to_minor(int(p["price"])),
            "currency": p["currency"],
            "image_url": p.get("image"),
            "is_active": True,
        }
        for p in payload["products"]
    ]


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    init_engine(settings)
    try:
        rows = _rows()
        async with get_sessionmaker()() as session, session.begin():
            await ProductRepository(session).upsert_many(rows)
        print(f"каталог загружен: {len(rows)} SKU")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
