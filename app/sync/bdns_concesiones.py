"""Enriquece subvenciones BDNS con estadísticas REALES de concesiones históricas.

BDNS no publica `importe_max_beneficiario` ni `porcentaje` en su API. Pero sí publica
en `/concesiones/busqueda?numConv=X` el importe REAL que han recibido los beneficiarios
en ediciones anteriores de la convocatoria.

A partir de esos datos calculamos:
- num_concesiones: cuántas se han concedido (popularidad)
- importe_medio: promedio de € por beneficiario (esperable)
- importe_max_concedido: máximo histórico (techo real observado)
- importe_min_concedido: mínimo histórico (suelo)
- total_concedido: suma de todos los importes (volumen movido)
- ultima_concesion: fecha de la última concesión

Se guarda en `raw_payload["concesiones_stats"]` para no requerir migración de schema.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Subvencion

logger = logging.getLogger(__name__)

_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
_PAGE_SIZE = 200
_MAX_PAGES = 5  # Tope: 5 páginas × 200 = 1000 concesiones por subvención.
                # Más que suficiente para estadísticas estables. Algunas convocatorias
                # tienen miles de beneficiarios y traerlos todos haría el enrich
                # imprácticamente lento (horas).


async def fetch_concesiones(num_conv: str, client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], int]:
    """Devuelve (muestra hasta 1000 concesiones, total_real_de_beneficiarios).

    Limitamos la DESCARGA a 1000 para velocidad, pero leemos `totalElements` que
    BDNS expone en la respuesta — eso es el número REAL de beneficiarios totales
    aunque solo hayamos descargado una muestra.
    """
    settings = get_settings()
    url = f"{settings.bdns_base_url}/concesiones/busqueda"
    all_items: list[dict[str, Any]] = []
    total_real = 0
    page = 0
    while page < _MAX_PAGES:
        params = {"numConv": num_conv, "page": page, "pageSize": _PAGE_SIZE}
        try:
            r = await client.get(url, params=params, headers=_HEADERS, timeout=30.0)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Concesiones %s page %d failed: %s", num_conv, page, exc)
            break
        items = data.get("content") or []
        all_items.extend(items)
        if page == 0:
            total_real = int(data.get("totalElements") or len(items))
        if data.get("last", True) or not items:
            break
        page += 1
    return all_items, total_real


def _compute_stats(items: list[dict[str, Any]], total_real: int = 0) -> dict[str, Any] | None:
    """Calcula stats a partir de la muestra de concesiones.

    Args:
        items: muestra de concesiones (hasta 1000).
        total_real: número total REAL de beneficiarios (de BDNS totalElements).

    Si total_real > len(items), el total_concedido se extrapola: la media de la
    muestra multiplicada por el número real de beneficiarios.
    """
    if not items:
        return None
    importes = [float(x.get("importe") or 0) for x in items if x.get("importe")]
    if not importes:
        return None
    fechas = [x.get("fechaConcesion") for x in items if x.get("fechaConcesion")]
    ultima = max(fechas) if fechas else None
    media = round(statistics.mean(importes), 2)
    num_real = total_real if total_real > 0 else len(items)
    # Si tenemos muestra parcial, extrapolamos el total
    total_concedido = round(sum(importes), 2)
    if total_real > len(items):
        total_concedido = round(media * total_real, 2)
    return {
        "num_concesiones": num_real,             # número REAL total de beneficiarios
        "muestra": len(items),                    # tamaño de muestra usada para stats
        "num_con_importe": len(importes),
        "importe_medio": media,
        "importe_max_concedido": round(max(importes), 2),
        "importe_min_concedido": round(min(importes), 2),
        "importe_mediana": round(statistics.median(importes), 2),
        "total_concedido": total_concedido,
        "ultima_concesion": ultima,
        "computed_at": datetime.utcnow().isoformat() + "Z",
    }


async def enrich_subvencion(session: Session, sub: Subvencion, client: httpx.AsyncClient) -> bool:
    """Enriquece una subvención con stats de concesiones. Devuelve True si actualizó."""
    cod = (sub.raw_payload or {}).get("codigoBDNS")
    if not cod:
        return False
    items, total_real = await fetch_concesiones(str(cod), client)
    stats = _compute_stats(items, total_real)
    if not stats:
        return False
    rp = dict(sub.raw_payload or {})
    rp["concesiones_stats"] = stats
    sub.raw_payload = rp
    return True


async def _worker(
    session: Session,
    sub: Subvencion,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    stats: dict[str, int],
) -> None:
    async with sem:
        try:
            if await enrich_subvencion(session, sub, client):
                stats["enriched"] += 1
            else:
                stats["skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error enriching %s: %s", sub.external_id, exc)
            stats["errors"] += 1


async def enrich_all(
    session: Session,
    *,
    max_records: int | None = None,
    only_missing: bool = True,
    delay_seconds: float = 0.0,  # legacy param, kept for compat
    concurrency: int = 10,
) -> dict[str, int]:
    """Recorre todas las subvenciones BDNS y enriquece sus stats de concesiones.

    Args:
        max_records: tope opcional para tests/ingest incremental.
        only_missing: si True, solo procesa subvenciones que aún no tienen stats.
        concurrency: cuántas subvenciones procesamos en paralelo. BDNS aguanta bien
                     ~10 conexiones simultáneas.
    """
    stats = {"processed": 0, "enriched": 0, "skipped": 0, "errors": 0}
    stmt = select(Subvencion).where(Subvencion.source == "bdns")
    if max_records:
        stmt = stmt.limit(max_records)
    rows = session.execute(stmt).scalars().all()

    pending: list[Subvencion] = []
    for sub in rows:
        stats["processed"] += 1
        if only_missing and (sub.raw_payload or {}).get("concesiones_stats"):
            stats["skipped"] += 1
            continue
        pending.append(sub)

    if not pending:
        return stats

    sem = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        # Procesa en chunks de 100 con commit periódico para no perder trabajo si crashea
        for chunk_start in range(0, len(pending), 100):
            chunk = pending[chunk_start : chunk_start + 100]
            await asyncio.gather(
                *(_worker(session, sub, client, sem, stats) for sub in chunk),
                return_exceptions=False,
            )
            session.commit()
            logger.info("BDNS concesiones progress: %s", stats)
    return stats
