"""BDNS detail-endpoint enricher con rate limiting y backoff."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Subvencion
from app.matching.finalidad_classifier import classify
from app.sync.bdns_mappers import map_detail

logger = logging.getLogger(__name__)
_settings = get_settings()
_HEADERS = {"Accept": "application/json", "User-Agent": "subvenciones-app/0.1"}
_RATE_LIMIT_SLEEP = 0.1  # 10 req/s safe
_MAX_429_RETRIES = 3


async def fetch_detail(num_conv: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Devuelve el JSON detail o None si 204/404.

    Reintento con backoff en 429 (2s × attempt, hasta 3 intentos).
    Levanta httpx.HTTPStatusError en otros errores >= 400.
    """
    url = f"{_settings.bdns_base_url}/convocatorias"
    params = {"numConv": num_conv}
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=30.0, headers=_HEADERS)
    try:
        for attempt in range(_MAX_429_RETRIES):
            r = await client.get(url, params=params)
            if r.status_code == 429:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (204, 404):
                return None
            r.raise_for_status()
            return r.json()
        return None
    finally:
        if owns:
            await client.aclose()


async def enrich_existing(
    session: Session,
    batch_commit: int = 200,
    max_records: int | None = None,
) -> dict[str, int]:
    """Backfill: encuentra Subvenciones BDNS con campos clave vacíos y las enriquece.

    Criterio "vacío": importe_total IS NULL AND fecha_fin IS NULL.
    Hace commit cada batch_commit registros enriquecidos.

    Returns:
        {"enriched": N, "skipped": M (204 del API), "errors": E, "total": N+M+E}
    """
    stmt = select(Subvencion).where(
        Subvencion.source == "bdns",
        Subvencion.importe_total.is_(None),
        Subvencion.fecha_fin.is_(None),
    )
    if max_records:
        stmt = stmt.limit(max_records)
    rows = session.execute(stmt).scalars().all()
    enriched = skipped = errors = 0

    async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as client:
        for sub in rows:
            try:
                detail = await fetch_detail(sub.external_id, client=client)
                if detail is None:
                    skipped += 1
                else:
                    mapped = map_detail(detail)
                    # Plan 3 Task 2: if keyword heuristic produced ['otros'] or empty, try Gemini classifier
                    if not mapped["finalidad"] or mapped["finalidad"] == ["otros"]:
                        long_text = detail.get("descripcionBasesReguladoras") or detail.get("descripcion") or ""
                        if long_text:
                            mapped["finalidad"] = await classify(long_text, fallback=mapped["finalidad"] or ["otros"])
                    for k, v in mapped.items():
                        # raw_payload siempre se sobrescribe; los demás solo si traen valor útil
                        if k == "raw_payload" or (v is not None and v != []):
                            setattr(sub, k, v)
                    enriched += 1
                    if enriched % batch_commit == 0:
                        session.commit()
                        logger.info("Enriched %d/%d", enriched, len(rows))
            except Exception as exc:
                errors += 1
                logger.warning("Error enriching %s: %s", sub.external_id, exc)
            await asyncio.sleep(_RATE_LIMIT_SLEEP)
        session.commit()

    return {
        "enriched": enriched,
        "skipped": skipped,
        "errors": errors,
        "total": len(rows),
    }


async def enrich_one(session: Session, external_id: str) -> bool:
    """Enriquece un único record. Devuelve True si se actualizó."""
    detail = await fetch_detail(external_id)
    if detail is None:
        return False
    sub = session.execute(
        select(Subvencion).where(
            Subvencion.source == "bdns",
            Subvencion.external_id == external_id,
        )
    ).scalar_one_or_none()
    if sub is None:
        return False
    mapped = map_detail(detail)
    # Plan 3 Task 2: if keyword heuristic produced ['otros'] or empty, try Gemini classifier
    if not mapped["finalidad"] or mapped["finalidad"] == ["otros"]:
        long_text = detail.get("descripcionBasesReguladoras") or detail.get("descripcion") or ""
        if long_text:
            mapped["finalidad"] = await classify(long_text, fallback=mapped["finalidad"] or ["otros"])
    for k, v in mapped.items():
        if k == "raw_payload" or (v is not None and v != []):
            setattr(sub, k, v)
    session.commit()
    return True
