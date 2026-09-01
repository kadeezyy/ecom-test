from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from shop.api.deps import SessionDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(session: SessionDep) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
