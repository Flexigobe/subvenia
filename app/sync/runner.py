"""Cron in-process con APScheduler."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.alerts.dispatcher import dispatch_alerts, flush_outbox
from app.config import get_settings
from app.db.session import SessionLocal
from app.sync.bdns_enricher import enrich_existing
from app.sync.bdns_puller import sync_all
from app.sync.borme_ingester import sync_day as borme_sync_day
from app.sync.catalogs import sync_catalogs
from app.sync.eu_puller import sync_all as eu_sync_all

logger = logging.getLogger(__name__)


async def run_bdns_sync() -> None:
    """Tarea: descarga últimos 14 días de BDNS y aplica."""
    settings = get_settings()
    since = date.today() - timedelta(days=14)
    logger.info("Starting BDNS sync since %s", since)
    with SessionLocal() as session:
        stats = await sync_all(session, since=since)
    logger.info(
        "BDNS sync done: created=%d updated=%d total=%d",
        stats["created"],
        stats["updated"],
        stats["total"],
    )
    logger.info("sync_complete", extra={"sync_name": "bdns", "stats": stats})


async def run_bdns_enricher() -> None:
    """Backfill incremental: enriquece records BDNS que aún tengan campos vacíos."""
    logger.info("Starting BDNS enrichment pass")
    with SessionLocal() as session:
        stats = await enrich_existing(session, max_records=1000)
    logger.info("BDNS enrichment done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "bdns_enricher", "stats": stats})


async def run_catalogs_sync() -> None:
    """Sync BDNS taxonomies (finalidades, beneficiarios, etc.). Monthly."""
    logger.info("Starting BDNS catalogs sync")
    with SessionLocal() as session:
        stats = await sync_catalogs(session)
    logger.info("BDNS catalogs sync done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "catalogs", "stats": stats})


async def run_eu_sync() -> None:
    """Tarea: descarga hasta 10 páginas (500 topics) del EU Funding & Tenders Portal."""
    logger.info("Starting EU Funding & Tenders sync")
    with SessionLocal() as session:
        stats = await eu_sync_all(session, max_pages=10)
    logger.info("EU sync done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "eu", "stats": stats})


async def run_borme_sync() -> None:
    """Daily BORME ingest at 10:30 Europe/Madrid (BORME publishes ~8-9 AM)."""
    from datetime import date as _date
    target = _date.today()
    logger.info("Starting BORME sync for %s", target)
    with SessionLocal() as session:
        stats = await borme_sync_day(session, target)
    logger.info("sync_complete", extra={"sync_name": "borme", "stats": stats})


async def run_flush_outbox() -> None:
    """Procesa la cola de emails pendientes."""
    with SessionLocal() as session:
        stats = await flush_outbox(session)
    if stats["processed"]:
        logger.info("Outbox flush: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "outbox_flush", "stats": stats})


async def run_dispatch_alerts() -> None:
    """Manda alertas diarias a las suscripciones activas."""
    logger.info("Starting alerts dispatch")
    with SessionLocal() as session:
        stats = await dispatch_alerts(session)
    logger.info("Alerts dispatch done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "alerts_dispatch", "stats": stats})


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="Europe/Madrid")
    scheduler.add_job(
        run_bdns_sync,
        CronTrigger(hour=settings.bdns_sync_hour, minute=settings.bdns_sync_minute),
        id="bdns_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        run_bdns_enricher,
        CronTrigger(hour=3, minute=30),
        id="bdns_enricher",
        replace_existing=True,
    )
    scheduler.add_job(
        run_catalogs_sync,
        CronTrigger(day=1, hour=4, minute=0),
        id="bdns_catalogs",
        replace_existing=True,
    )
    scheduler.add_job(
        run_eu_sync,
        CronTrigger(hour=3, minute=45),
        id="eu_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        run_flush_outbox,
        IntervalTrigger(minutes=5),
        id="flush_outbox",
        replace_existing=True,
    )
    scheduler.add_job(
        run_dispatch_alerts,
        CronTrigger(hour=9, minute=0),
        id="dispatch_alerts",
        replace_existing=True,
    )
    scheduler.add_job(
        run_borme_sync,
        CronTrigger(hour=10, minute=30),
        id="borme_sync",
        replace_existing=True,
    )
    return scheduler
