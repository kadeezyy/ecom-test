"""Заглушка поставщика выдачи.

Реализует контракт из ТЗ (``POST /issue``) и умеет управляемо ломаться:
падать контрактной ошибкой, отвечать «нет остатка» и — главное — **выдавать
код и зависать**, эмулируя ровно ту ситуацию, ради которой всё задание:
поставщик успел выдать код, а ответ не дошёл.

Состояние держится в памяти процесса: заглушка одноразовая и не должна
переживать перезапуск. Админские эндпоинты не защищены намеренно — это
тестовый двойник, а не сервис с данными.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shop.core.logging import configure_logging, get_logger
from supplier_stub.chaos import Behaviour, Chaos
from supplier_stub.config import StubSettings, get_stub_settings
from supplier_stub.store import KeyStore, OutOfKeys

logger = get_logger(__name__)


class IssueRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    sku: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)


class ConfigRequest(BaseModel):
    mode: str | None = None
    fail_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    timeout_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    hang_seconds: float | None = Field(default=None, ge=0.0)
    seed: int | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: StubSettings = app.state.settings
    store: KeyStore = app.state.store
    logger.info(
        "supplier_stub_started",
        supplier=settings.name,
        keys=store.total,
        mode=settings.mode,
    )
    yield


def create_app(settings: StubSettings | None = None) -> FastAPI:
    settings = settings or get_stub_settings()
    configure_logging()

    app = FastAPI(title=f"Заглушка поставщика {settings.name.upper()}", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = KeyStore(settings.load_keys())
    app.state.skus = settings.load_skus()
    app.state.chaos = Chaos(
        mode=settings.mode,
        fail_rate=settings.fail_rate,
        timeout_rate=settings.timeout_rate,
        hang_seconds=settings.hang_seconds,
        seed=settings.seed,
    )

    _register_routes(app)
    return app


def _error(status_code: int, reason: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "reason": reason})


def _register_routes(app: FastAPI) -> None:
    @app.post("/issue")
    async def issue(body: IssueRequest, request: Request) -> Any:
        settings: StubSettings = request.app.state.settings
        store: KeyStore = request.app.state.store
        chaos: Chaos = request.app.state.chaos

        behaviour = chaos.decide()
        existing = await store.peek(body.request_id)

        if behaviour is Behaviour.TIMEOUT:
            # Ловушка задания: код выдаётся ДО зависания. Клиент получит
            # таймаут, но у поставщика код уже закреплён за request_id.
            try:
                issued = existing or await store.issue(
                    request_id=body.request_id, order_id=body.order_id, sku=body.sku
                )
            except OutOfKeys:
                return _error(409, "out_of_stock")

            logger.warning(
                "stub_hang_after_issue",
                supplier=settings.name,
                request_id=body.request_id,
                order_id=body.order_id,
                hang_seconds=chaos.hang_seconds,
            )
            await asyncio.sleep(chaos.hang_seconds)
            return {"status": "ok", "request_id": body.request_id, "code": issued.code}

        if existing is not None:
            # Контрактная гарантия сильнее хаоса: тот же request_id — тот же код.
            logger.info(
                "stub_issue_replay",
                supplier=settings.name,
                request_id=body.request_id,
                order_id=body.order_id,
            )
            return {"status": "ok", "request_id": body.request_id, "code": existing.code}

        if behaviour is Behaviour.ERROR:
            logger.info("stub_error", supplier=settings.name, request_id=body.request_id)
            return _error(503, "internal_error")

        if behaviour is Behaviour.OUT_OF_STOCK:
            return _error(409, "out_of_stock")

        try:
            issued = await store.issue(
                request_id=body.request_id, order_id=body.order_id, sku=body.sku
            )
        except OutOfKeys:
            logger.info("stub_out_of_stock", supplier=settings.name, request_id=body.request_id)
            return _error(409, "out_of_stock")

        logger.info(
            "stub_issued",
            supplier=settings.name,
            request_id=body.request_id,
            order_id=body.order_id,
        )
        return {"status": "ok", "request_id": body.request_id, "code": issued.code}

    @app.get("/stock")
    async def stock(request: Request) -> dict[str, Any]:
        store: KeyStore = request.app.state.store
        return {"available": store.available, "skus": request.app.state.skus}

    @app.get("/admin/state")
    async def state(request: Request) -> dict[str, Any]:
        store: KeyStore = request.app.state.store
        chaos: Chaos = request.app.state.chaos
        available, issues = await store.snapshot()
        return {
            "supplier": request.app.state.settings.name,
            "available": available,
            "issued_count": len(issues),
            "issued": [
                {
                    "request_id": i.request_id,
                    "order_id": i.order_id,
                    "sku": i.sku,
                    "code": i.code,
                }
                for i in issues
            ],
            "chaos": chaos.state(),
        }

    @app.post("/admin/config")
    async def configure(body: ConfigRequest, request: Request) -> dict[str, Any]:
        chaos: Chaos = request.app.state.chaos
        chaos.configure(**body.model_dump(exclude_none=True))
        logger.info("stub_reconfigured", supplier=request.app.state.settings.name, **chaos.state())
        return chaos.state()

    @app.post("/admin/restock")
    async def restock(
        request: Request,
        keys: Annotated[list[str], Body(embed=True)],
    ) -> dict[str, int]:
        store: KeyStore = request.app.state.store
        added = await store.restock(keys)
        logger.info("stub_restocked", supplier=request.app.state.settings.name, added=added)
        return {"added": added, "available": store.available}

    @app.post("/admin/drain")
    async def drain(request: Request) -> dict[str, int]:
        """Опустошает пул — сценарий «пустой остаток»."""
        store: KeyStore = request.app.state.store
        drained = await store.drain()
        return {"drained": drained, "available": store.available}

    @app.post("/admin/reset")
    async def reset(request: Request) -> dict[str, int]:
        store: KeyStore = request.app.state.store
        await store.reset()
        return {"available": store.available}


app = create_app()
