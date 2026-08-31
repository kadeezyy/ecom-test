"""Генерация синтетического каталога для этапа 5.

    python scripts/seed_bulk.py --count 50000

Остатки задаются разными, чтобы частичные индексы витрины работали в
условиях, близких к боевым (часть SKU распродана и из индекса выпадает).
"""

from __future__ import annotations

import argparse
import asyncio
import random

from shop.core.config import get_settings
from shop.core.db import dispose_engine, get_sessionmaker, init_engine
from shop.core.logging import configure_logging
from shop.repositories.products import ProductRepository

TYPES = ("topup", "key", "subscription", "giftcard")
BATCH = 1000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--sold-out-share",
        type=float,
        default=0.3,
        help="доля SKU с нулевым остатком",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    init_engine(settings)
    rng = random.Random(args.seed)

    try:
        inserted = 0
        async with get_sessionmaker()() as session:
            for start in range(0, args.count, BATCH):
                rows = []
                for i in range(start, min(start + BATCH, args.count)):
                    sold_out = rng.random() < args.sold_out_share
                    rows.append(
                        {
                            "sku": f"BULK-{i:07d}",
                            "name": f"Синтетический товар {i}",
                            "type": TYPES[i % len(TYPES)],
                            "price_minor": rng.randrange(9900, 999_900, 100),
                            "currency": "RUB",
                            "image_url": None,
                            "is_active": True,
                            "available_count": 0 if sold_out else rng.randint(1, 500),
                        }
                    )
                async with session.begin():
                    await ProductRepository(session).upsert_many(rows)
                inserted += len(rows)
                print(f"загружено {inserted}/{args.count}", end="\r")

        print(f"\nсинтетический каталог загружен: {inserted} SKU")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
