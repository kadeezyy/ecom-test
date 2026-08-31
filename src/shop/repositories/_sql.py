"""Мелкие помощники для репозиториев."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.engine import Result


def rows_affected(result: Result[Any]) -> int:
    """Число затронутых строк UPDATE/DELETE.

    ``Session.execute`` типизирован как ``Result``, а ``rowcount`` есть только
    у ``CursorResult`` — приведение локализовано здесь, чтобы не размазывать
    ``cast`` по репозиториям.
    """
    rowcount = cast("CursorResult[Any]", result).rowcount
    return max(rowcount, 0)
