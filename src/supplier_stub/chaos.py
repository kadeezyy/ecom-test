"""Управляемый хаос заглушки.

Режим либо жёстко задан (детерминированные тесты), либо разыгрывается
сеяным генератором — тогда сценарий воспроизводится по номеру seed.
"""

from __future__ import annotations

import random
from enum import StrEnum


class Behaviour(StrEnum):
    OK = "ok"
    #: Выдать код и «зависнуть» — ответ до клиента не дойдёт.
    TIMEOUT = "timeout"
    #: Контрактная ошибка: приложение поставщика кода не выдавало.
    ERROR = "error"
    OUT_OF_STOCK = "out_of_stock"


class Mode(StrEnum):
    RANDOM = "random"
    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"
    OUT_OF_STOCK = "out_of_stock"


class Chaos:
    def __init__(
        self,
        *,
        mode: str = "random",
        fail_rate: float = 0.2,
        timeout_rate: float = 0.2,
        hang_seconds: float = 10.0,
        seed: int = 1337,
    ) -> None:
        self.mode = Mode(mode)
        self.fail_rate = fail_rate
        self.timeout_rate = timeout_rate
        self.hang_seconds = hang_seconds
        self._random = random.Random(seed)

    def configure(
        self,
        *,
        mode: str | None = None,
        fail_rate: float | None = None,
        timeout_rate: float | None = None,
        hang_seconds: float | None = None,
        seed: int | None = None,
    ) -> None:
        if mode is not None:
            self.mode = Mode(mode)
        if fail_rate is not None:
            self.fail_rate = fail_rate
        if timeout_rate is not None:
            self.timeout_rate = timeout_rate
        if hang_seconds is not None:
            self.hang_seconds = hang_seconds
        if seed is not None:
            self._random = random.Random(seed)

    def decide(self) -> Behaviour:
        if self.mode is not Mode.RANDOM:
            return Behaviour(self.mode.value)

        roll = self._random.random()
        if roll < self.timeout_rate:
            return Behaviour.TIMEOUT
        if roll < self.timeout_rate + self.fail_rate:
            return Behaviour.ERROR
        return Behaviour.OK

    def state(self) -> dict[str, object]:
        return {
            "mode": str(self.mode),
            "fail_rate": self.fail_rate,
            "timeout_rate": self.timeout_rate,
            "hang_seconds": self.hang_seconds,
        }
