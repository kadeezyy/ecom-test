"""HTTP-клиент поставщика выдачи.

Единственная задача модуля — превратить «что произошло в сети» в ответ на
вопрос **«мог ли поставщик уже выдать код?»**. Ответов ровно три:

===========================  ==================================  ===========
Событие                      Класс                               Фолбэк?
===========================  ==================================  ===========
``{"status": "ok", code}``   успех                               —
Контрактная ошибка
``{"status": "error", ...}`` :class:`SupplierRefused`            разрешён
(любой HTTP-код)             (приложение поставщика обработало
                             запрос и кода не выдало)
Ошибка соединения /          :class:`SupplierRefused`            разрешён
connect timeout              (TCP-сессии не было, запрос
                             физически не отправлялся)
Таймаут чтения, обрыв,       :class:`SupplierUnknown`            **запрещён**
неразборный ответ            (запрос дошёл, ответ — нет;
                             код мог быть выдан)
===========================  ==================================  ===========

Отсюда и раздельные ``connect``/``read`` таймауты в конфиге: они разводят
доказуемо безопасный случай и опасный.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

from shop.core.exceptions import SupplierRefused, SupplierUnknown
from shop.core.logging import get_logger
from shop.domain.enums import SupplierName

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IssuedCode:
    request_id: str
    code: str


class SupplierClient:
    """Клиент одной заглушки-поставщика."""

    def __init__(
        self,
        *,
        name: SupplierName,
        base_url: str,
        connect_timeout_s: float,
        read_timeout_s: float,
        max_attempts: int,
        backoff_base_s: float,
        backoff_max_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout_s,
                read=read_timeout_s,
                write=read_timeout_s,
                pool=connect_timeout_s,
            ),
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def issue(self, *, request_id: str, sku: str, order_id: str) -> IssuedCode:
        """Запрашивает код.

        Повторы делаются **только** при неизвестном исходе и **всегда с тем
        же** ``request_id`` — контракт обязывает поставщика вернуть на него
        уже выданный код, а не выдать новый. Доказанный отказ не повторяется:
        его должен разрулить фолбэк уровнем выше.
        """
        last_unknown: SupplierUnknown | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._issue_once(
                    request_id=request_id, sku=sku, order_id=order_id, attempt=attempt
                )
            except SupplierUnknown as exc:
                last_unknown = exc
                logger.warning(
                    "supplier_issue_unknown",
                    supplier=str(self.name),
                    request_id=request_id,
                    order_id=order_id,
                    attempt=attempt,
                    max_attempts=self._max_attempts,
                    reason=exc.reason,
                )
                if attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff_delay(attempt))

        assert last_unknown is not None
        raise last_unknown

    async def _issue_once(
        self, *, request_id: str, sku: str, order_id: str, attempt: int
    ) -> IssuedCode:
        payload = {"request_id": request_id, "sku": sku, "order_id": order_id}
        logger.info(
            "supplier_issue_attempt",
            supplier=str(self.name),
            request_id=request_id,
            order_id=order_id,
            sku=sku,
            attempt=attempt,
        )
        try:
            response = await self._client.post("/issue", json=payload)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            # Соединение не установлено -> запрос не был отправлен -> кода нет.
            raise SupplierRefused(
                "connection to supplier failed",
                supplier=str(self.name),
                request_id=request_id,
                reason="connect_failed",
            ) from exc
        except httpx.TimeoutException as exc:
            # Read/write/pool timeout: запрос ушёл, ответ не вернулся.
            raise SupplierUnknown(
                "supplier response timed out",
                supplier=str(self.name),
                request_id=request_id,
                reason="read_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise SupplierUnknown(
                "supplier transport error",
                supplier=str(self.name),
                request_id=request_id,
                reason="transport_error",
            ) from exc

        return self._parse(response, request_id=request_id, order_id=order_id)

    def _parse(self, response: httpx.Response, *, request_id: str, order_id: str) -> IssuedCode:
        body = self._json_or_none(response)

        if body is None:
            raise SupplierUnknown(
                f"unparseable supplier response, http={response.status_code}",
                supplier=str(self.name),
                request_id=request_id,
                reason="malformed_response",
            )

        status = body.get("status")

        if status == "error":
            reason = str(body.get("reason") or "unspecified")[:64]
            logger.info(
                "supplier_issue_refused",
                supplier=str(self.name),
                request_id=request_id,
                order_id=order_id,
                http_status=response.status_code,
                reason=reason,
            )
            raise SupplierRefused(
                f"supplier refused: {reason}",
                supplier=str(self.name),
                request_id=request_id,
                reason=reason,
            )

        code = body.get("code")
        if status == "ok" and isinstance(code, str) and code:
            logger.info(
                "supplier_issue_ok",
                supplier=str(self.name),
                request_id=request_id,
                order_id=order_id,
            )
            return IssuedCode(request_id=request_id, code=code)

        # Ответ вне контракта: доверять ему нельзя, но и отказом считать
        # нельзя — код мог быть выдан.
        raise SupplierUnknown(
            f"contract violation in supplier response, http={response.status_code}",
            supplier=str(self.name),
            request_id=request_id,
            reason="contract_violation",
        )

    @staticmethod
    def _json_or_none(response: httpx.Response) -> dict[str, Any] | None:
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _backoff_delay(self, attempt: int) -> float:
        """Экспоненциальный бэкофф с полным джиттером."""
        window = min(self._backoff_base_s * (2 ** (attempt - 1)), self._backoff_max_s)
        return random.uniform(0, window)

    async def fetch_stock(self) -> tuple[int, list[str]] | None:
        """Остаток поставщика для синхронизации витрины. ``None`` — недоступен."""
        try:
            response = await self._client.get("/stock")
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("supplier_stock_unavailable", supplier=str(self.name))
            return None
        return int(body["available"]), list(body.get("skus", []))
