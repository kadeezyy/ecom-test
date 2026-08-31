"""Настройки заглушки поставщика."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class StubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STUB_", extra="ignore")

    name: str = "a"
    #: random | ok | timeout | error | out_of_stock
    mode: str = "random"
    fail_rate: float = 0.2
    timeout_rate: float = 0.2
    hang_seconds: float = 10.0
    seed: int = 1337

    keys_file: Path = REPO_ROOT / "data" / "keys.json"
    key_offset: int = 0
    key_limit: int = 25

    catalog_file: Path = REPO_ROOT / "data" / "catalog.json"

    def load_keys(self) -> list[str]:
        payload = json.loads(self.keys_file.read_text(encoding="utf-8"))
        keys: list[str] = list(payload["keys"])
        return keys[self.key_offset : self.key_offset + self.key_limit]

    def load_skus(self) -> list[str]:
        payload = json.loads(self.catalog_file.read_text(encoding="utf-8"))
        return [p["sku"] for p in payload["products"]]


@lru_cache(maxsize=1)
def get_stub_settings() -> StubSettings:
    return StubSettings()
