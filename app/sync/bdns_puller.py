"""Cliente HTTP para la BDNS (Base de Datos Nacional de Subvenciones)."""

from __future__ import annotations

from datetime import date
from datetime import date as date_t
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Subvencion

settings = get_settings()

# Mapping from BDNS nivel1 values to normalised ambito strings.
_NIVEL1_TO_AMBITO: dict[str, str] = {
    "ESTATAL": "estatal",
    "AUTONÓMICA": "autonomico",
    "AUTONOMICA": "autonomico",
    "LOCAL": "local",
    "EUROPEA": "ue",
}


async def fetch_page(page: int, since: date, page_size: int | None = None) -> dict[str, Any]:
    """Descarga una página del listado de convocatorias BDNS.

    Args:
        page: número de página (1-indexed, se convierte a 0-indexed internamente).
        since: fecha desde la que filtrar convocatorias modificadas.
        page_size: tamaño de página. Si None, usa el de config.

    Returns:
        La respuesta JSON cruda de la API BDNS (Spring Page). Contiene las claves
        ``content`` (lista de items), ``last`` (bool), ``totalPages``, etc.

    Raises:
        httpx.HTTPStatusError: si el servidor responde con >= 400.
    """
    size = page_size or settings.bdns_page_size
    url = f"{settings.bdns_base_url}/convocatorias/busqueda"
    # BDNS usa paginación 0-indexed; los callers externos pasan 1-indexed.
    params = {
        "page": page - 1,
        "pageSize": size,
        # La API BDNS espera el formato DD/MM/YYYY.
        "fechaDesde": since.strftime("%d/%m/%Y"),
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
    """Mapea un item bruto del endpoint de listado BDNS al formato de Subvencion.

    Campos disponibles en el listado:
        id, mrr, numeroConvocatoria, descripcion, descripcionLeng,
        fechaRecepcion, nivel1, nivel2, nivel3, codigoInvente

    Los campos no presentes en el listado (importes, fechaFin, beneficiarios,
    cnae, finalidad, enlace) se dejan en None / [].  Un paso posterior (Plan 2)
    podrá enriquecerlos desde el endpoint de detalle cuando esté disponible.
    """
    nivel1 = raw.get("nivel1") or ""
    ambito = _NIVEL1_TO_AMBITO.get(nivel1.upper(), "estatal")

    # Organismo: usamos el nivel más específico disponible.
    organismo = raw.get("nivel3") or raw.get("nivel2") or raw.get("nivel1")

    return {
        "source": "bdns",
        # numeroConvocatoria es el identificador público canónico de la convocatoria.
        "external_id": str(raw["numeroConvocatoria"]),
        # descripcion es el campo de texto libre más cercano a un título en el listado.
        "titulo": raw.get("descripcion", ""),
        "organismo": organismo,
        "ambito": ambito,
        # BDNS no devuelve ccaa en el listado; se podría inferir de nivel2 en el futuro.
        "ccaa": None,
        # fechaRecepcion es la fecha de publicación/registro; la más cercana a fechaInicio.
        "fecha_inicio": _parse_date(raw.get("fechaRecepcion")),
        # Fecha de fin no disponible en el listado.
        "fecha_fin": None,
        # Importes no disponibles en el listado.
        "importe_total": None,
        "importe_max_beneficiario": None,
        "porcentaje": None,
        # Beneficiarios, cnae y finalidad no disponibles en el listado.
        "beneficiarios": None,
        "cnae_elegible": [],
        "finalidad": [],
        # descripcion duplicada en el campo propio (puede enriquecerse con detalle).
        "descripcion": raw.get("descripcion"),
        # Enlace oficial no disponible en el listado.
        "enlace_oficial": None,
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
        items = payload.get("content", [])
        if not items:
            break
        for raw in items:
            parsed = parse_item(raw)
            if upsert_subvencion(session, parsed):
                created += 1
            else:
                updated += 1
        session.commit()
        # Spring Page usa `last: true` cuando no hay más páginas.
        if payload.get("last", True):
            break
        page += 1
    return {"created": created, "updated": updated, "total": created + updated}
