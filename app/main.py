"""FastAPI entrypoint con scheduler in-process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.sync.runner import build_scheduler
from app.web.routes_search import router as search_router
from app.web.routes_browse import router as browse_router  # noqa: E402
from app.web.routes_news import router as news_router  # noqa: E402
from app.web.routes_enrich import router as enrich_router  # noqa: E402
from app.web.routes_alerts import router as alerts_router  # noqa: E402

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
app.include_router(browse_router)
app.include_router(news_router)
app.include_router(enrich_router)
app.include_router(alerts_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
