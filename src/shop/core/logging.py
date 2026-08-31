"""Структурное логирование.

Правила проекта:

* каждая запись имеет поле ``event`` — стабильный машиночитаемый ключ
  (``payment_webhook_received``, ``delivery_attempt_unknown``, ...);
  текст сообщения ключом поиска не является;
* всё остальное — поля (``order_id``, ``request_id``, ``supplier``, ...);
* ERROR всегда пишется с объектом исключения (``logger.exception`` или
  ``exc_info=True``), чтобы в логе был traceback.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_configured = False


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Настраивает structlog и stdlib logging. Идемпотентна."""
    global _configured

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        # Пишем через stdlib logging, а не напрямую в stdout: так логи
        # uvicorn и SQLAlchemy идут тем же маршрутом и одним потоком.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(name: str) -> Any:
    """Логгер модуля. Возвращаемый тип намеренно ``Any``: structlog
    подменяет bound-класс в ``configure``, точный тип неизвестен статически."""
    if not _configured:  # безопасно при импорте из скриптов и тестов
        configure_logging()
    return structlog.get_logger(name)


def bind_request_context(**kwargs: Any) -> None:
    """Кладёт сквозные поля (order_id, event_id, ...) в contextvars."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
