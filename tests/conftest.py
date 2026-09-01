"""Общие фикстуры.

Тесты делятся на две группы:

* без маркера — не нужны БД: доменные правила, курсоры, и, что важнее,
  клиент поставщика против **настоящей заглушки по настоящему HTTP**
  (значит, и таймауты настоящие);
* с маркером ``db`` — нужен PostgreSQL в ``TEST_DATABASE_URL``. Без этой
  переменной они пропускаются, а не падают.

Схема в тестах накатывается теми же миграциями Alembic, что и в проде —
второго источника правды о схеме нет.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

TRUNCATED_TABLES = (
    "audit_log",
    "ledger_entries",
    "delivery_jobs",
    "deliveries",
    "payment_events",
    "orders",
    "products",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL не задан — тесты на PostgreSQL пропущены")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@dataclass
class RunningStub:
    """Запущенная заглушка + доступ к её админке."""

    name: str
    url: str
    client: httpx.AsyncClient

    async def configure(self, **kwargs: Any) -> None:
        response = await self.client.post("/admin/config", json=kwargs)
        response.raise_for_status()

    async def state(self) -> dict[str, Any]:
        response = await self.client.get("/admin/state")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def issued_count(self) -> int:
        return int((await self.state())["issued_count"])

    async def available(self) -> int:
        return int((await self.state())["available"])

    async def drain(self) -> None:
        (await self.client.post("/admin/drain")).raise_for_status()

    async def restock(self, keys: list[str]) -> None:
        response = await self.client.post("/admin/restock", json={"keys": keys})
        response.raise_for_status()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@contextlib.asynccontextmanager
async def _serve(app: FastAPI, port: int) -> AsyncIterator[None]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # uvicorn не даёт события готовности — опрос флага здесь единственный вариант.
    while not server.started and not task.done():  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    if task.done():  # pragma: no cover — сервер не поднялся
        task.result()
    try:
        yield
    finally:
        server.should_exit = True
        await task


async def _start_stub(name: str, key_offset: int, mode: str) -> AsyncIterator[RunningStub]:
    from supplier_stub.app import create_app
    from supplier_stub.config import StubSettings

    settings = StubSettings(
        name=name, mode=mode, key_offset=key_offset, key_limit=25, hang_seconds=0.5
    )
    app = create_app(settings)
    port = _free_port()
    async with _serve(app, port):
        url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=url, timeout=5.0) as client:
            yield RunningStub(name=name, url=url, client=client)


@pytest.fixture
async def stub_a() -> AsyncIterator[RunningStub]:
    """Поставщик A. По умолчанию всегда успешен — хаос включают сами тесты."""
    async for stub in _start_stub("a", key_offset=0, mode="ok"):
        yield stub


@pytest.fixture
async def stub_b() -> AsyncIterator[RunningStub]:
    async for stub in _start_stub("b", key_offset=25, mode="ok"):
        yield stub


@pytest.fixture(scope="session")
def migrated_database() -> str:
    """Накатывает миграции на тестовую БД один раз за сессию."""
    assert TEST_DATABASE_URL is not None
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": "src"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_database, poolclass=None)
    async with eng.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TRUNCATED_TABLES)} RESTART IDENTITY CASCADE"))
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def db(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Сессия для прямых проверок состояния в тестах."""
    async with sessions() as session:
        yield session


@pytest.fixture
async def catalog(sessions: async_sessionmaker[AsyncSession]) -> list[dict[str, Any]]:
    """Загружает 12 SKU из задания."""
    from shop.domain.money import to_minor
    from shop.repositories.products import ProductRepository

    payload = json.loads((REPO_ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
    rows = [
        {
            "sku": p["sku"],
            "name": p["name"],
            "type": p["type"],
            "price_minor": to_minor(int(p["price"])),
            "currency": p["currency"],
            "image_url": p.get("image"),
            "is_active": True,
            "available_count": 25,
        }
        for p in payload["products"]
    ]
    async with sessions() as session, session.begin():
        await ProductRepository(session).upsert_many(rows)
    return rows


ADMIN_TOKEN = "test-admin-token"


@dataclass
class ApiHarness:
    """HTTP-клиент API плюс воркер, которым тест сам управляет."""

    client: httpx.AsyncClient
    app: FastAPI
    worker: Any

    async def create_order(self, sku: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.client.post("/orders", json={"sku": sku}, **kwargs)
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def get_order(self, order_id: str) -> dict[str, Any]:
        response = await self.client.get(f"/orders/{order_id}")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    async def pay(self, order_id: str, **overrides: Any) -> httpx.Response:
        from tests.factories import webhook_payload

        return await self.client.post(
            "/webhooks/payment", json=webhook_payload(order_id, **overrides)
        )

    async def deliver(self) -> int:
        """Прогоняет очередь до опустошения (роль фонового воркера)."""
        processed: int = await self.worker.run_until_idle()
        return processed

    async def reconcile(self, **params: Any) -> dict[str, Any]:
        response = await self.client.get(
            "/admin/reconcile",
            params=params,
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body


@pytest.fixture
async def api(
    migrated_database: str,
    engine: AsyncEngine,
    stub_a: RunningStub,
    stub_b: RunningStub,
    catalog: list[dict[str, Any]],
) -> AsyncIterator[ApiHarness]:
    os.environ["DATABASE_URL"] = migrated_database
    os.environ["SUPPLIER_A_URL"] = stub_a.url
    os.environ["SUPPLIER_B_URL"] = stub_b.url
    os.environ["ADMIN_TOKEN"] = ADMIN_TOKEN
    os.environ["SUPPLIER_READ_TIMEOUT_S"] = "0.3"
    os.environ["SUPPLIER_CONNECT_TIMEOUT_S"] = "0.3"
    os.environ["SUPPLIER_MAX_ATTEMPTS"] = "2"
    os.environ["SUPPLIER_BACKOFF_BASE_S"] = "0.01"
    os.environ["JOB_MAX_ATTEMPTS"] = "3"
    os.environ["JOB_BACKOFF_BASE_S"] = "0.01"
    os.environ["JOB_BACKOFF_MAX_S"] = "0.02"
    os.environ["LOG_JSON"] = "true"

    from shop.api.app import create_app
    from shop.core.config import get_settings
    from shop.core.db import dispose_engine
    from shop.worker.main import Worker

    get_settings.cache_clear()
    app = create_app()

    async with app.router.lifespan_context(app):
        worker = Worker(get_settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30.0
        ) as client:
            try:
                yield ApiHarness(client=client, app=app, worker=worker)
            finally:
                await worker.aclose()
    await dispose_engine()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _quiet_logs() -> Iterator[None]:
    from shop.core.logging import configure_logging

    configure_logging("WARNING", json_logs=True)
    yield
