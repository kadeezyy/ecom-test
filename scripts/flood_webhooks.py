"""Харнесс проверки гонок: шлёт N параллельных вебхуков по одному заказу.

Он же играет роль заглушки платёжной системы — реального эквайринга нет.

    # 50 параллельных вебхуков с РАЗНЫМИ event_id (критерий 1)
    python scripts/flood_webhooks.py --sku STEAM-TOPUP-500 --count 50

    # 50 доставок ОДНОГО события (критерий 2, at-least-once)
    python scripts/flood_webhooks.py --sku STEAM-TOPUP-500 --count 50 --same-event

    # вебхук раньше заказа (критерий 3)
    python scripts/flood_webhooks.py --order-id ord_deadbeef00000000 --count 1
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

DEFAULT_API = "http://localhost:8000"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--sku", help="создать заказ по этому SKU перед обстрелом")
    parser.add_argument("--order-id", help="слать вебхуки по существующему заказу")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument(
        "--same-event",
        action="store_true",
        help="все вебхуки с одним event_id (проверка идемпотентности доставки)",
    )
    parser.add_argument("--amount", type=int, help="сумма; по умолчанию — цена заказа")
    parser.add_argument("--status", default="paid", choices=["paid", "failed"])
    parser.add_argument("--wait-delivered", type=float, default=10.0)
    return parser.parse_args()


async def _create_order(client: httpx.AsyncClient, sku: str) -> dict[str, Any]:
    response = await client.post("/orders", json={"sku": sku})
    response.raise_for_status()
    order: dict[str, Any] = response.json()
    return order


async def _send(
    client: httpx.AsyncClient, payload: dict[str, Any]
) -> tuple[int, dict[str, Any] | str]:
    try:
        response = await client.post("/webhooks/payment", json=payload)
    except httpx.HTTPError as exc:
        return 0, repr(exc)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


async def _wait_delivered(
    client: httpx.AsyncClient, order_id: str, wait_seconds: float
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + wait_seconds
    order: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/orders/{order_id}")
        if response.status_code == 200:
            order = response.json()
            if order["status"] in {"delivered", "payment_failed"}:
                return order
        await asyncio.sleep(0.25)
    return order


async def main() -> None:
    args = _parse_args()
    if not args.sku and not args.order_id:
        raise SystemExit("нужен --sku или --order-id")

    async with httpx.AsyncClient(base_url=args.api_url, timeout=30.0) as client:
        if args.sku:
            order = await _create_order(client, args.sku)
            order_id = order["id"]
            amount = args.amount if args.amount is not None else order["amount"]
            print(f"заказ создан: {order_id}, сумма {amount} {order['currency']}")
        else:
            order_id = args.order_id
            amount = args.amount if args.amount is not None else 500
            print(f"обстрел существующего/будущего заказа: {order_id}")

        shared_event_id = f"evt_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        payloads = [
            {
                "event_id": shared_event_id if args.same_event else f"evt_{uuid.uuid4().hex[:12]}",
                "order_id": order_id,
                "status": args.status,
                "amount": amount,
                "currency": "RUB",
                "created_at": now,
            }
            for _ in range(args.count)
        ]

        # Все запросы стартуют одновременно — это и есть проверка гонок.
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(*(_send(client, p) for p in payloads))
        elapsed = asyncio.get_running_loop().time() - started

        codes: dict[int, int] = {}
        applied = 0
        duplicates = 0
        reasons: dict[str, int] = {}
        for status_code, body in results:
            codes[status_code] = codes.get(status_code, 0) + 1
            if isinstance(body, dict):
                applied += int(bool(body.get("applied")))
                duplicates += int(bool(body.get("duplicate")))
                reason = body.get("reason")
                if reason:
                    reasons[str(reason)] = reasons.get(str(reason), 0) + 1

        print(f"\nотправлено {args.count} вебхуков за {elapsed:.2f} c")
        print(f"HTTP-коды:        {codes}")
        print(f"applied=true:     {applied}   <- должно быть ровно 1 при status=paid")
        print(f"duplicate=true:   {duplicates}")
        print(f"причины отказа:   {reasons}")

        order = await _wait_delivered(client, order_id, args.wait_delivered)
        if order:
            succeeded = [
                a for a in order.get("delivery_attempts", []) if a["status"] == "succeeded"
            ]
            print(f"\nстатус заказа:    {order['status']}")
            print(f"выданный код:     {order.get('code')}")
            print(f"успешных выдач:   {len(succeeded)}   <- должно быть ровно 1")
            print(f"попытки выдачи:   {order.get('delivery_attempts')}")
        else:
            print("\nзаказ не найден (ожидаемо для сценария «вебхук раньше заказа»)")


if __name__ == "__main__":
    asyncio.run(main())
