"""FastAPI entrypoint con scheduler in-process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.sync.runner import build_scheduler
from app.web.routes_search import router as search_router

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


app = FastAPI(title="Buscador de subvenciones", lifespan=lifespan)
app.include_router(search_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
