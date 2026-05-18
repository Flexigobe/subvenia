"""FastAPI entrypoint con scheduler in-process."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.sync.runner import build_scheduler
from app.web.routes_search import router as search_router
from app.web.routes_browse import router as browse_router  # noqa: E402
from app.web.routes_news import router as news_router  # noqa: E402
from app.web.routes_enrich import router as enrich_router  # noqa: E402
from app.web.routes_alerts import router as alerts_router  # noqa: E402
from app.web.routes_empresa import router as empresa_router  # noqa: E402

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Process-level admin override for dev when ADMIN_PASS is empty.
# DO NOT use this in production — set real values via env vars.
_DEV_ADMIN: dict[str, str] = {}

# Holds a reference to the running scheduler so /healthz can inspect it.
_scheduler_ref: dict = {"instance": None}


def _get_admin_credentials() -> tuple[str, str]:
    """Return (user, pass) — env-set values if present, else dev fallback dict."""
    settings = get_settings()
    if settings.admin_pass:
        return settings.admin_user or "admin", settings.admin_pass
    if _DEV_ADMIN:
        return _DEV_ADMIN["user"], _DEV_ADMIN["pass"]
    return "", ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev-only admin fallback: generate random creds if none configured.
    if not settings.admin_pass and not _DEV_ADMIN:
        _DEV_ADMIN["user"] = "admin"
        _DEV_ADMIN["pass"] = secrets.token_urlsafe(16)
        logger.warning(
            "[ADMIN DEV] ADMIN_PASS not set. Generated dev credentials: "
            "user=%s pass=%s  — set ADMIN_USER/ADMIN_PASS in .env to fix.",
            _DEV_ADMIN["user"], _DEV_ADMIN["pass"],
        )

    scheduler = build_scheduler()
    scheduler.start()
    _scheduler_ref["instance"] = scheduler
    logger.info("Scheduler started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        _scheduler_ref["instance"] = None
        logger.info("Scheduler stopped")


app = FastAPI(title="Buscador de subvenciones", lifespan=lifespan)
app.include_router(search_router)
app.include_router(browse_router)
app.include_router(news_router)
app.include_router(enrich_router)
app.include_router(alerts_router)
app.include_router(empresa_router)

from app.web.routes_admin import router as admin_router  # noqa: E402
from app.web.routes_legal import router as legal_router  # noqa: E402

app.include_router(admin_router)
app.include_router(legal_router)

from app.web.rate_limit import RateLimitMiddleware  # noqa: E402

app.add_middleware(RateLimitMiddleware, requests_per_window=settings.rate_limit_per_hour)


@app.get("/healthz")
def healthz() -> dict:
    import time as _time

    from sqlalchemy import text

    checks: dict = {}
    overall = "ok"

    # DB check
    db_status = "ok"
    try:
        from app.db.session import SessionLocal
        t0 = _time.perf_counter()
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        checks["db_latency_ms"] = round((_time.perf_counter() - t0) * 1000, 2)
    except Exception as exc:
        db_status = "error"
        checks["db_error"] = type(exc).__name__
        overall = "degraded"

    # Scheduler check
    scheduler_status = (
        "running"
        if (_scheduler_ref.get("instance") and _scheduler_ref["instance"].running)
        else "stopped"
    )
    if scheduler_status != "running":
        overall = "degraded"

    return {
        "status": overall,
        "db": db_status,
        "scheduler": scheduler_status,
        "checks": checks,
    }
