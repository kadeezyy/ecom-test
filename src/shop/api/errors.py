from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shop.core.exceptions import DomainError
from shop.core.logging import get_logger

logger = get_logger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        logger.info(
            "request_domain_error",
            error_code=exc.code,
            path=request.url.path,
            **{k: str(v) for k, v in exc.context.items()},
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.message,
                "context": {k: str(v) for k, v in exc.context.items()},
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 500 намеренно: платёжная система должна повторить доставку вебхука,
        # если мы не смогли его сохранить.
        logger.error(
            "request_unhandled_error",
            exc_info=exc,
            path=request.url.path,
            method=request.method,
        )
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "internal error", "context": {}},
        )
