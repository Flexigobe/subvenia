"""Sync BDNS taxonomy catalogs (finalidades, beneficiarios, instrumentos, regiones, actividades).

Endpoints return small JSON datasets (each < 100 items). We snapshot the raw payload
per kind into the `bdns_catalog` table. Updated infrequently — monthly cron is plenty.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import BdnsCatalog

logger = logging.getLogger(__name__)
_settings = get_settings()
_HEADERS = {"Accept": "application/json", "User-Agent": "subvenciones-app/0.1"}

# kind → relative path (relative to bdns_base_url which already ends in /api)
CATALOG_ENDPOINTS: dict[str, str] = {
    "finalidades": "/finalidades?vpd=GE",
    "beneficiarios": "/beneficiarios?vpd=GE",
    "instrumentos": "/instrumentos",
    "regiones": "/regiones",
    "actividades": "/actividades",
}


async def sync_catalogs(session: Session) -> dict[str, int]:
    """Descarga cada catálogo y hace upsert por `kind`.

    Returns:
        Dict {kind: item_count or 1}. item_count = len(list) si la respuesta es lista,
        si no devuelve 1 (objeto único o estructura no-lista).
    """
    out: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as client:
        for kind, path in CATALOG_ENDPOINTS.items():
            url = f"{_settings.bdns_base_url}{path}"
            try:
                r = await client.get(url)
                r.raise_for_status()
                data: Any = r.json()
                existing = session.get(BdnsCatalog, kind)
                if existing is None:
                    session.add(BdnsCatalog(kind=kind, payload=data))
                else:
                    existing.payload = data
                out[kind] = len(data) if isinstance(data, list) else 1
                logger.info("Catalog %s updated: %d items", kind, out[kind])
            except Exception as exc:
                logger.warning("Failed to sync catalog %s: %s", kind, exc)
                out[kind] = 0
    session.commit()
    return out


def get_catalog(session: Session, kind: str) -> Any:
    """Devuelve el payload del catálogo, o None si no existe en DB."""
    row = session.get(BdnsCatalog, kind)
    return row.payload if row else None
