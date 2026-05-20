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
from app.sync.bdns_concesiones import enrich_all as enrich_concesiones_all
from app.sync.bdns_enricher import enrich_existing
from app.sync.bdns_puller import sync_all
from app.sync.borme_ingester import sync_day as borme_sync_day
from app.sync.catalogs import sync_catalogs
from app.sync.empresite_sitemap import sync_empresite_sitemap
from app.sync.eu_puller import sync_all as eu_sync_all
from app.sync.ted_puller import sync_recent as ted_sync_recent
from app.sync.wikidata_puller import sync_wikidata

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


async def run_bdns_concesiones() -> None:
    """Enriquece subvenciones con stats reales de concesiones históricas (importes
    medios y máximos pagados a beneficiarios anteriores). Diario."""
    logger.info("Starting BDNS concesiones enrich")
    with SessionLocal() as session:
        stats = await enrich_concesiones_all(session, only_missing=True, delay_seconds=0.3)
    logger.info("BDNS concesiones enrich done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "bdns_concesiones", "stats": stats})


async def run_catalogs_sync() -> None:
    """Sync BDNS taxonomies (finalidades, beneficiarios, etc.). Monthly."""
    logger.info("Starting BDNS catalogs sync")
    with SessionLocal() as session:
        stats = await sync_catalogs(session)
    logger.info("BDNS catalogs sync done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "catalogs", "stats": stats})


async def run_ted_sync() -> None:
    """Sync diario de licitaciones públicas TED (España, últimos 14 días)."""
    logger.info("Starting TED sync")
    with SessionLocal() as session:
        stats = await ted_sync_recent(session, days=14, max_pages=20)
    logger.info("TED sync done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "ted", "stats": stats})


async def run_eu_sync() -> None:
    """Tarea: descarga hasta 30 páginas × ~60 queries × 50 = teórico 90k topics
    del EU Funding & Tenders Portal. min_useful=60 hace que paremos pronto en
    cada query si ya tiene records nuevos."""
    logger.info("Starting EU Funding & Tenders sync")
    with SessionLocal() as session:
        stats = await eu_sync_all(session, max_pages=30, min_useful=60)
    logger.info("EU sync done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "eu", "stats": stats})


async def run_empresite_sync() -> None:
    """Refresca el índice de empresas españolas desde el sitemap público de Empresite.

    Mensual — el sitemap actualiza una vez al mes con nuevas constituciones y bajas.
    Solo descargamos el sitemap (es decir, los NOMBRES de empresa); no scrapeamos
    páginas individuales. Los nombres se indexan en `empresa` con hoja_rm='EMP:<hash>'.
    """
    logger.info("Starting Empresite sitemap sync")
    with SessionLocal() as session:
        stats = await sync_empresite_sitemap(session)
    logger.info("Empresite sync done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "empresite", "stats": stats})


async def run_wikidata_sync() -> None:
    """Refresca empresas grandes españolas desde Wikidata (semanal).

    Complementa BORME: BORME publica cambios mercantiles, Wikidata mantiene un
    catálogo curado de matrices grandes (BBVA, Santander, Mercadona, Inditex...)
    que llevan años sin cambios en el registro y por tanto no aparecen en BORME.
    """
    logger.info("Starting Wikidata sync")
    with SessionLocal() as session:
        stats = await sync_wikidata(session)
    logger.info("Wikidata sync done: %s", stats)
    logger.info("sync_complete", extra={"sync_name": "wikidata", "stats": stats})


async def run_borme_sync() -> None:
    """Daily BORME ingest at 10:30 Europe/Madrid (BORME publishes ~8-9 AM).

    Sincroniza los últimos 3 días para tolerar que la app esté apagada algún día
    (Railway restart, deploy, etc.). El upsert con `on_conflict_do_nothing` hace
    que reprocesar días ya ingeridos sea idempotente y barato.
    """
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    aggregated_stats: dict[str, int] = {}
    for offset in (2, 1, 0):  # antes-de-ayer, ayer, hoy
        target = today - _td(days=offset)
        logger.info("Starting BORME sync for %s", target)
        with SessionLocal() as session:
            stats = await borme_sync_day(session, target)
        for k, v in stats.items():
            if isinstance(v, (int, float)):
                aggregated_stats[k] = aggregated_stats.get(k, 0) + v
        logger.info("BORME sync for %s done: %s", target, stats)
    logger.info("sync_complete", extra={"sync_name": "borme", "stats": aggregated_stats})


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
    # NOTA: BDNS concesiones enrich DESACTIVADO.
    # El endpoint /api/concesiones/busqueda ignora el parámetro `codigoBDNS` (nuestro
    # identificador único de convocatoria) y solo acepta `numeroConvocatoria` (un ID
    # legacy distinto que no exponemos en /api/convocatorias). Sin mapeo entre ambos,
    # el enricher devolvía las mismas stats globales (24M concesiones de todo BDNS)
    # para cada subvención. Re-activar solo cuando encontremos el mapeo o BDNS exponga
    # el endpoint correcto.
    # scheduler.add_job(
    #     run_bdns_concesiones, CronTrigger(hour=4, minute=15),
    #     id="bdns_concesiones", replace_existing=True,
    # )
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
        run_ted_sync,
        CronTrigger(hour=4, minute=15),
        id="ted_sync",
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
    # Wikidata: semanal — los lunes a las 5:00 (catálogo curado por la comunidad,
    # cambia despacio así que no necesita refresh diario).
    scheduler.add_job(
        run_wikidata_sync,
        CronTrigger(day_of_week="mon", hour=5, minute=0),
        id="wikidata_sync",
        replace_existing=True,
    )
    # Empresite sitemap: mensual el día 5 a las 4:00 — actualiza el índice de empresas
    # españolas (4M+ entradas). Es lo más pesado, lo corremos en horario tranquilo.
    scheduler.add_job(
        run_empresite_sync,
        CronTrigger(day=5, hour=4, minute=0),
        id="empresite_sync",
        replace_existing=True,
    )
    return scheduler
