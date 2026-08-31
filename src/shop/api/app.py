"""Сборка FastAPI-приложения."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shop.api.errors import register_exception_handlers
from shop.api.routers import admin, catalog, health, orders, webhooks
from shop.core.config import get_settings
from shop.core.db import dispose_engine, get_sessionmaker, init_engine
from shop.core.logging import configure_logging, get_logger
from shop.integrations.supplier.registry import SupplierRegistry
from shop.services.delivery_service import DeliveryService

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    init_engine(settings)

    app.state.settings = settings
    app.state.suppliers = SupplierRegistry(settings)
    app.state.delivery_service = DeliveryService(get_sessionmaker(), app.state.suppliers)

    logger.info(
        "api_started",
        supplier_a=settings.supplier_a_url,
        supplier_b=settings.supplier_b_url,
    )
    try:
        yield
    finally:
        await app.state.suppliers.aclose()
        await dispose_engine()
        logger.info("api_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Ядро магазина цифровых товаров",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(orders.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)
    return app


app = create_app()
