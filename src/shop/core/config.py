"""Конфигурация приложения. Единственный источник — переменные окружения."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки API и воркера.

    Секретов в коде нет: значения по умолчанию годятся только для локальной
    разработки, в docker-compose и проде всё приходит из окружения.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "postgresql+asyncpg://shop:shop@localhost:5432/shop"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    log_level: str = "INFO"
    log_json: bool = True

    admin_token: str = "change-me-in-production"

    supplier_a_url: str = "http://localhost:9001"
    supplier_b_url: str = "http://localhost:9002"
    supplier_connect_timeout_s: float = 1.0
    supplier_read_timeout_s: float = 3.0
    supplier_max_attempts: int = Field(default=3, ge=1)
    supplier_backoff_base_s: float = 0.2
    supplier_backoff_max_s: float = 5.0

    worker_poll_interval_s: float = 0.5
    worker_batch_size: int = Field(default=10, ge=1)
    job_max_attempts: int = Field(default=10, ge=1)
    job_backoff_base_s: float = 1.0
    job_backoff_max_s: float = 60.0
    stuck_order_age_s: int = 30
    sweep_interval_s: int = 15
    stock_sync_interval_s: int = 30

    def supplier_url(self, name: str) -> str:
        match name.lower():
            case "a":
                return self.supplier_a_url
            case "b":
                return self.supplier_b_url
            case _:  # pragma: no cover — защищено типом SupplierName
                raise ValueError(f"unknown supplier: {name}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
