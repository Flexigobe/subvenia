"""Cliente HTTP para la BDNS (Base de Datos Nacional de Subvenciones)."""

from __future__ import annotations

from datetime import date, date as date_t
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Subvencion

settings = get_settings()


async def fetch_page(page: int, since: date, page_size: int | None = None) -> dict[str, Any]:
    """Descarga una página del listado de convocatorias BDNS.

    Args:
        page: número de página (1-indexed).
        since: fecha desde la que filtrar convocatorias modificadas.
        page_size: tamaño de página. Si None, usa el de config.

    Returns:
        Dict con claves `page`, `totalPages`, `items` (lista de convocatorias en bruto).

    Raises:
        httpx.HTTPStatusError: si el servidor responde con >= 400.
    """
    size = page_size or settings.bdns_page_size
    url = f"{settings.bdns_base_url}/convocatorias/busqueda"
    params = {
        "page": page,
        "pageSize": size,
        "fechaDesde": since.isoformat(),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _parse_date(value: str | None) -> date_t | None:
    if not value:
        return None
    return date_t.fromisoformat(value)


def parse_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Mapea un item bruto de BDNS al formato de nuestro modelo Subvencion."""
    return {
        "source": "bdns",
        "external_id": str(raw["id"]),
        "titulo": raw.get("titulo", ""),
        "organismo": raw.get("organismo"),
        "ambito": raw.get("ambito", "estatal"),
        "ccaa": raw.get("ccaa"),
        "fecha_inicio": _parse_date(raw.get("fechaInicio")),
        "fecha_fin": _parse_date(raw.get("fechaFin")),
        "importe_total": raw.get("importeTotal"),
        "importe_max_beneficiario": raw.get("importeMaxBeneficiario"),
        "porcentaje": raw.get("porcentaje"),
        "beneficiarios": raw.get("beneficiarios"),
        "cnae_elegible": raw.get("cnaeElegible") or [],
        "finalidad": raw.get("finalidad") or [],
        "descripcion": raw.get("descripcion"),
        "enlace_oficial": raw.get("enlaceOficial"),
        "raw_payload": raw,
    }


def upsert_subvencion(session: Session, parsed: dict[str, Any]) -> bool:
    """Inserta o actualiza una subvención por (source, external_id).

    Returns:
        True si se creó nueva, False si se actualizó existente.
    """
    existing = session.execute(
        select(Subvencion).where(
            Subvencion.source == parsed["source"],
            Subvencion.external_id == parsed["external_id"],
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(Subvencion(**parsed))
        return True

    for key, value in parsed.items():
        setattr(existing, key, value)
    return False


async def sync_all(session: Session, since: date) -> dict[str, int]:
    """Descarga todas las páginas BDNS desde `since` y hace upsert.

    Returns:
        {"created": N, "updated": M, "total": N+M}
    """
    created = 0
    updated = 0
    page = 1
    while True:
        payload = await fetch_page(page=page, since=since)
        items = payload.get("items", [])
        if not items:
            break
        for raw in items:
            parsed = parse_item(raw)
            if upsert_subvencion(session, parsed):
                created += 1
            else:
                updated += 1
        session.commit()
        total_pages = payload.get("totalPages", 1)
        if page >= total_pages:
            break
        page += 1
    return {"created": created, "updated": updated, "total": created + updated}
