"""Cliente para el EU Funding & Tenders Portal — segunda fuente de subvenciones.

API: POST https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA
Respuesta: {"results": [...], "totalResults": N, "pageNumber": M, "pageSize": K}

Cada result tiene:
  - metadata.identifier[0]    — ID del topic (ej. "HORIZON-CL4-2025-01")
  - metadata.title[0]         — título del topic
  - metadata.callTitle[0]     — título de la convocatoria (call)
  - metadata.callIdentifier[0]— identificador de la convocatoria
  - metadata.deadlineDate[0]  — fecha límite ISO 8601 "2026-12-31T00:00:00.000+0000"
  - metadata.startDate[0]     — fecha apertura
  - metadata.status[0]        — "31094501" Forthcoming, "31094502" Open, "31094503" Closed
  - metadata.frameworkProgramme[0] — código del programa marco
  - metadata.typesOfAction    — lista de tipo de acción
  - summary (top-level)       — descripción breve
  - url (top-level)           — URL JSON del topic (no el portal HTML)
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import date as date_t
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Subvencion

logger = logging.getLogger(__name__)

EU_API_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EU_API_KEY = "SEDIA"

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "subvenciones-app/0.1",
}

# status code → estado
_STATUS_MAP: dict[str, str] = {
    "31094501": "proximamente",   # Forthcoming
    "31094502": "abierta",        # Open
    "31094503": "cerrada",        # Closed
}

_FINALIDAD_KEYWORDS: list[tuple[str, str]] = [
    ("digital", "digitalizacion"),
    ("research", "i+d"),
    ("innov", "innovacion"),
    ("employ", "contratacion"),
    ("energy", "eficiencia_energetica"),
    ("renew", "eficiencia_energetica"),
    ("internation", "internacionalizacion"),
    ("export", "internacionalizacion"),
    ("educati", "formacion"),
    ("train", "formacion"),
    ("climate", "medioambiente"),
    ("green", "medioambiente"),
    ("health", "sanidad"),
    ("social", "inclusion_social"),
]


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _infer_finalidad(text: str | None) -> list[str]:
    if not text:
        return []
    norm = _strip_accents(text).lower()
    matched: list[str] = []
    seen: set[str] = set()
    for kw, token in _FINALIDAD_KEYWORDS:
        if kw in norm and token not in seen:
            matched.append(token)
            seen.add(token)
    return matched or ["otros"]


def _parse_date(value: Any) -> date_t | None:
    """Parse ISO 8601 string or Unix ms timestamp to date.

    The EU API returns strings like "2026-12-31T00:00:00.000+0000".
    """
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date()
        # Take first 10 chars: "2026-12-31"
        return date_t.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _first(lst: Any, default: Any = None) -> Any:
    """Return first element of a list, or default if empty/not a list."""
    if isinstance(lst, list):
        return lst[0] if lst else default
    return lst if lst is not None else default


async def fetch_page(page: int = 1, page_size: int = 50) -> dict[str, Any]:
    """Hits the EU search endpoint and returns the raw JSON response.

    POST request with apiKey in query string; empty form body.
    Returns the response dict with keys: results, totalResults, pageNumber, pageSize.
    """
    params = {
        "apiKey": EU_API_KEY,
        "text": "***",
        "pageSize": page_size,
        "pageNumber": page,
        "languages": "es,en",
    }
    async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as client:
        r = await client.post(EU_API_URL, params=params, data={})
        r.raise_for_status()
        return r.json()


def parse_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw EU API result to the Subvencion field dict.

    All metadata values are lists → use _first() to extract the scalar.
    """
    md = raw.get("metadata") or {}

    identifier = _first(md.get("identifier"), "")
    external_id = str(identifier).strip() if identifier else ""

    # Prefer metadata.title; fall back to top-level summary
    title = _first(md.get("title")) or raw.get("summary") or _first(md.get("callTitle")) or ""
    if not isinstance(title, str):
        title = str(title)

    # Call title as organismo (most meaningful human-readable programme name)
    organismo = _first(md.get("callTitle")) or _first(md.get("frameworkProgramme"))

    # deadlineDate is the submission deadline; startDate is when the call opened
    deadline_raw = _first(md.get("deadlineDate"))
    start_raw = _first(md.get("startDate"))

    # Build the human-readable portal URL
    portal_url = (
        f"https://ec.europa.eu/info/funding-tenders/opportunities/portal"
        f"/screen/opportunities/topic-details/{external_id}"
        if external_id else _first(md.get("url")) or raw.get("url")
    )

    # Map status code to estado
    status_code = _first(md.get("status"), "31094503")
    estado = _STATUS_MAP.get(str(status_code), "cerrada")

    # Infer finalidad from title + action type text
    types_of_action = " ".join(md.get("typesOfAction") or [])
    finalidad = _infer_finalidad(f"{title} {types_of_action}")

    return {
        "source": "eu",
        "external_id": external_id,
        "titulo": title,
        "organismo": organismo,
        "ambito": "ue",
        "ccaa": None,
        "fecha_inicio": _parse_date(start_raw),
        "fecha_fin": _parse_date(deadline_raw),
        "importe_total": None,          # EU budgets are complex nested JSON; leave None
        "importe_max_beneficiario": None,
        "porcentaje": None,
        "beneficiarios": None,
        "cnae_elegible": [],
        "finalidad": finalidad,
        "descripcion": raw.get("summary"),
        "enlace_oficial": portal_url,
        "raw_payload": raw,
        "estado": estado,
    }


def _upsert(session: Session, parsed: dict[str, Any]) -> bool:
    """Insert or update a Subvencion by (source='eu', external_id).

    Returns True if created, False if updated.
    """
    existing = session.execute(
        select(Subvencion).where(
            Subvencion.source == "eu",
            Subvencion.external_id == parsed["external_id"],
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(Subvencion(**parsed))
        return True

    for k, v in parsed.items():
        setattr(existing, k, v)
    return False


async def sync_all(
    session: Session,
    max_pages: int | None = None,
    page_size: int = 50,
) -> dict[str, int]:
    """Iterate EU API pages, parse, and upsert into the DB.

    Args:
        session: SQLAlchemy session.
        max_pages: cap on pages fetched (default: no cap — use with care,
                   the index has 600k+ documents across all types).
        page_size: items per page (default 50).

    Returns:
        {"created": N, "updated": M, "total": N+M, "pages": P}
    """
    page = 1
    created = updated = 0

    while True:
        if max_pages is not None and page > max_pages:
            break

        payload = await fetch_page(page=page, page_size=page_size)
        results = payload.get("results") or []
        if not results:
            break

        for raw in results:
            parsed = parse_item(raw)
            if not parsed["external_id"]:
                # Skip items without a usable identifier (FAQ pages, etc.)
                continue
            if _upsert(session, parsed):
                created += 1
            else:
                updated += 1

        session.commit()

        total_results = payload.get("totalResults")
        # Detect last page conservatively: no results, or page * size >= totalResults
        if total_results is not None and page * page_size >= total_results:
            break

        page += 1

    return {
        "created": created,
        "updated": updated,
        "total": created + updated,
        "pages": page - 1,
    }
