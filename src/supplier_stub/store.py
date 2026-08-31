"""Пул ключей заглушки.

Два инварианта, ради которых всё и написано:

* один ключ не может уйти в два заказа;
* повтор с тем же ``request_id`` возвращает **тот же самый** код.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime


class OutOfKeys(Exception):
    """Пул пуст."""


@dataclass(frozen=True, slots=True)
class Issue:
    request_id: str
    order_id: str
    sku: str
    code: str
    issued_at: datetime


class KeyStore:
    def __init__(self, keys: list[str]) -> None:
        self._initial = list(keys)
        self._free: list[str] = list(keys)
        self._by_request: dict[str, Issue] = {}
        self._lock = asyncio.Lock()

    async def issue(self, *, request_id: str, order_id: str, sku: str) -> Issue:
        """Выдаёт код. Повтор с тем же ``request_id`` возвращает прежний."""
        async with self._lock:
            existing = self._by_request.get(request_id)
            if existing is not None:
                return existing
            if not self._free:
                raise OutOfKeys(request_id)

            issue = Issue(
                request_id=request_id,
                order_id=order_id,
                sku=sku,
                code=self._free.pop(0),
                issued_at=datetime.now(UTC),
            )
            self._by_request[request_id] = issue
            return issue

    async def peek(self, request_id: str) -> Issue | None:
        async with self._lock:
            return self._by_request.get(request_id)

    async def restock(self, keys: list[str]) -> int:
        async with self._lock:
            known = set(self._free) | {i.code for i in self._by_request.values()}
            added = [k for k in keys if k not in known]
            self._free.extend(added)
            return len(added)

    async def reset(self) -> None:
        async with self._lock:
            self._free = list(self._initial)
            self._by_request.clear()

    async def drain(self) -> int:
        """Опустошает пул — для сценария «пустой остаток»."""
        async with self._lock:
            drained = len(self._free)
            self._free.clear()
            return drained

    async def snapshot(self) -> tuple[int, list[Issue]]:
        async with self._lock:
            return len(self._free), list(self._by_request.values())

    @property
    def available(self) -> int:
        return len(self._free)

    @property
    def total(self) -> int:
        """Размер исходного пула."""
        return len(self._initial)
