"""Sync TED (Tenders Electronic Daily) — licitaciones públicas UE/España.

TED es el servicio oficial de licitaciones de la UE. Cubre contratos de
supplies/services/works del sector público español publicados al Diario Oficial
de la Unión Europea (DOUE). NO son subvenciones — son oportunidades para que
empresas vendan al sector público.

API pública sin autenticación. Doc: https://docs.ted.europa.eu/api/latest/
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import Licitacion

logger = logging.getLogger(__name__)

API_URL = "https://api.ted.europa.eu/v3/notices/search"
_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
_TIMEOUT = 60.0

# NUTS code → ISO province INE mapping (NUTS 3 nivel España)
_NUTS_TO_PROVINCIA: dict[str, str] = {
    "ES111": "15", "ES112": "27", "ES113": "32", "ES114": "36",  # Galicia
    "ES120": "33",  # Asturias
    "ES130": "39",  # Cantabria
    "ES211": "01", "ES212": "20", "ES213": "48",  # País Vasco
    "ES220": "31",  # Navarra
    "ES230": "26",  # La Rioja
    "ES241": "22", "ES242": "44", "ES243": "50",  # Aragón
    "ES300": "28",  # Madrid
    "ES411": "05", "ES412": "09", "ES413": "24", "ES414": "34",
    "ES415": "37", "ES416": "40", "ES417": "42", "ES418": "47", "ES419": "49",  # CyL
    "ES421": "02", "ES422": "13", "ES423": "16", "ES424": "19", "ES425": "45",  # CLM
    "ES431": "06", "ES432": "10",  # Extremadura
    "ES511": "08", "ES512": "17", "ES513": "25", "ES514": "43",  # Catalunya
    "ES521": "03", "ES522": "12", "ES523": "46",  # Comunidad Valenciana
    "ES530": "07",  # Baleares
    "ES611": "04", "ES612": "11", "ES613": "14", "ES614": "18", "ES615": "21",
    "ES616": "23", "ES617": "29", "ES618": "41",  # Andalucía
    "ES620": "30",  # Murcia
    "ES630": "51",  # Ceuta
    "ES640": "52",  # Melilla
    "ES703": "35", "ES704": "35", "ES705": "35", "ES706": "38", "ES707": "38", "ES708": "38", "ES709": "38",  # Canarias
}

_NUTS_TO_CCAA: dict[str, str] = {
    "ES11": "12",  # Galicia
    "ES12": "03", "ES13": "06",  # Asturias, Cantabria
    "ES21": "16", "ES22": "15", "ES23": "17",  # País Vasco, Navarra, La Rioja
    "ES24": "02",  # Aragón
    "ES30": "13",  # Madrid
    "ES41": "07",  # Castilla y León
    "ES42": "08",  # CLM
    "ES43": "11",  # Extremadura
    "ES51": "09",  # Catalunya
    "ES52": "10",  # Valencia
    "ES53": "04",  # Baleares
    "ES61": "01",  # Andalucía
    "ES62": "14",  # Murcia
    "ES63": "18", "ES64": "19",  # Ceuta, Melilla
    "ES70": "05",  # Canarias
}


def _multilang_text(field: Any) -> str | None:
    """Extrae texto preferentemente en español (SPA), si no existe inglés (ENG)."""
    if not field:
        return None
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        return field[0] if field else None
    if isinstance(field, dict):
        for key in ("spa", "SPA", "eng", "ENG"):
            v = field.get(key)
            if v:
                return v[0] if isinstance(v, list) else v
        # Cualquier idioma como fallback
        for v in field.values():
            if v:
                return v[0] if isinstance(v, list) else v
    return None


def _first_str(field: Any) -> str | None:
    if not field:
        return None
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        return field[0] if field else None
    return None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        # TED format: 2026-01-02+01:00 o 2026-01-02
        return datetime.fromisoformat(s.split("+")[0].split("Z")[0]).date()
    except (ValueError, AttributeError):
        return None


def _parse_value(field: Any) -> float | None:
    if field is None:
        return None
    if isinstance(field, (int, float)):
        return float(field)
    if isinstance(field, str):
        try:
            return float(field)
        except ValueError:
            return None
    if isinstance(field, dict):
        v = field.get("amount") or field.get("value")
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                return None
    return None


def map_notice(raw: dict) -> dict | None:
    """Mapea un notice TED a kwargs para Licitacion. None si no procesable."""
    ext_id = raw.get("publication-number")
    if not ext_id:
        return None

    titulo = _multilang_text(raw.get("notice-title"))
    if not titulo:
        return None

    organismo = _multilang_text(raw.get("buyer-name"))
    ciudad = None
    descripcion = _multilang_text(raw.get("description-lot"))

    # NUTS — viene como ['ES511', 'ESP', 'ES511', 'ESP'] (lugar de ejecución)
    pop = raw.get("place-of-performance") or []
    nuts_code = None
    if isinstance(pop, list):
        for code in pop:
            if isinstance(code, str) and code.startswith("ES") and len(code) >= 4 and code != "ESP":
                nuts_code = code[:5]
                break

    provincia = _NUTS_TO_PROVINCIA.get(nuts_code) if nuts_code else None
    ccaa = _NUTS_TO_CCAA.get(nuts_code[:4]) if nuts_code else None

    fecha_pub = _parse_date(_first_str(raw.get("publication-date")))
    fecha_lim = _parse_date(_first_str(raw.get("deadline-receipt-tender-date-lot")))

    importe = _parse_value(raw.get("total-value"))

    # PDF español preferido
    pdf_links = raw.get("links", {}).get("pdf", {})
    enlace = pdf_links.get("SPA") if isinstance(pdf_links, dict) else None
    if not enlace and isinstance(pdf_links, dict):
        enlace = pdf_links.get("ENG") or next(iter(pdf_links.values()), None)
    if not enlace:
        enlace = f"https://ted.europa.eu/es/notice/{ext_id}/pdf"

    # CPV codes
    cpv = raw.get("classification-cpv")
    if isinstance(cpv, str):
        cpv_codes = [cpv]
    elif isinstance(cpv, list):
        cpv_codes = [c for c in cpv if isinstance(c, str)]
    else:
        cpv_codes = None

    return {
        "source": "ted",
        "external_id": ext_id,
        "titulo": titulo[:500],
        "descripcion": descripcion,
        "organismo": organismo,
        "ccaa": ccaa,
        "provincia": provincia,
        "nuts_code": nuts_code,
        "ciudad": ciudad,
        "fecha_publicacion": fecha_pub,
        "fecha_limite": fecha_lim,
        "importe_total": importe,
        "moneda": "EUR",
        "tipo_procedimiento": _first_str(raw.get("procedure-type")),
        "tipo_contrato": _first_str(raw.get("contract-nature")),
        "cpv_codes": cpv_codes,
        "enlace_oficial": enlace,
        "raw_payload": raw,
    }


async def _fetch_page(client: httpx.AsyncClient, query: str, page: int, page_size: int) -> dict:
    body = {
        "query": query,
        "limit": page_size,
        "page": page,
        "fields": [
            "publication-number", "notice-title", "procedure-type", "contract-nature",
            "publication-date", "deadline-receipt-tender-date-lot",
            "total-value", "buyer-name", "place-of-performance",
            "description-lot", "classification-cpv",
            "links",
        ],
    }
    r = await client.post(API_URL, json=body, headers=_HEADERS)
    r.raise_for_status()
    return r.json()


async def sync_recent(session: Session, days: int = 90, max_pages: int = 50) -> dict[str, int]:
    """Sincroniza licitaciones de España de los últimos N días.

    50 páginas × 250/página = 12.500 records máx por run. TED retiene records
    desde 2016 con 180k total — para histórico completo usar backfill separado.
    """
    since = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    query = f"buyer-country IN (ESP) AND publication-date >= {since}"

    page_size = 250
    created = updated = total = errors = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for page in range(1, max_pages + 1):
            # Rate limit safety: TED tolera ~5 req/s, esperamos 0.4s entre páginas
            if page > 1:
                import asyncio as _aio
                await _aio.sleep(0.4)
            try:
                data = await _fetch_page(client, query, page, page_size)
            except (httpx.HTTPStatusError, httpx.ReadTimeout) as e:
                logger.warning("TED page %d failed: %s", page, e)
                errors += 1
                continue

            notices = data.get("notices") or []
            if not notices:
                break

            for raw in notices:
                total += 1
                mapped = map_notice(raw)
                if not mapped:
                    continue

                # Upsert por source+external_id
                stmt = insert(Licitacion).values(**mapped, id=__import__("uuid").uuid4())
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_licitacion_source_extid",
                    set_={k: mapped[k] for k in mapped
                          if k not in ("source", "external_id", "id")},
                )
                try:
                    res = session.execute(stmt)
                    # Heuristic: si inserted, rowcount=1 con ON CONFLICT DO UPDATE; difícil distinguir.
                    # Usamos approximate: si external_id es nuevo en BD → created.
                except Exception as e:
                    logger.warning("Insert failed for %s: %s", mapped["external_id"], e)
                    errors += 1
                    continue

            session.commit()
            logger.info("TED page %d: %d notices", page, len(notices))

            if len(notices) < page_size:
                break

    # Final counts
    total_db = session.execute(select(__import__("sqlalchemy").func.count()).select_from(Licitacion)).scalar() or 0
    return {"fetched": total, "errors": errors, "total_db": int(total_db)}
