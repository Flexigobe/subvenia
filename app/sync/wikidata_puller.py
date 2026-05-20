"""Ingiere empresas españolas desde Wikidata como SEGUNDA fuente.

BORME solo publica empresas que han hecho cambios mercantiles recientes — empresas
grandes/históricas (BBVA, Santander, Mercadona...) que llevan años sin tocar nada en
el Registro no aparecen ahí. Wikidata mantiene ~13k empresas españolas curadas por la
comunidad, incluyendo todas las matrices grandes. Lo usamos como complemento.

Estrategia:
- Una consulta SPARQL paginada al endpoint público (query.wikidata.org/sparql).
- Cada resultado se inserta en la tabla `empresa` con `hoja_rm="WD:Qxxxx"` (prefijo WD
  para distinguir de hojas BORME reales).
- Si una empresa ya existe en BORME con la misma razón social, NO se sobreescribe —
  el dedup en el autocomplete se encarga del resto.
- Provincias Wikidata vienen como "provincia de Córdoba", "Madrid", etc. — mapeamos
  a códigos INE de 2 dígitos.
- objeto_social se rellena con la industria/sector que Wikidata tenga para esa empresa.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Empresa
from app.sync.borme_parser import slugify

logger = logging.getLogger(__name__)

_WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
_USER_AGENT = "subvenciones-app/1.0 (https://github.com/flexigobe/subvenciones-app)"


# Mapeo de nombre de provincia/CCAA Wikidata → código INE 2 dígitos.
# Wikidata devuelve cosas como "provincia de Córdoba", "Madrid", "Cantabria",
# "Comunidad de Madrid", "Gran Bilbao" (comarca de Vizcaya), etc.
_PROVINCIA_NAME_TO_INE: dict[str, str] = {
    # Provincias directas
    "alava": "01", "araba": "01", "vitoria-gasteiz": "01",
    "albacete": "02",
    "alicante": "03", "alacant": "03",
    "almeria": "04",
    "avila": "05",
    "badajoz": "06",
    "baleares": "07", "illes balears": "07", "mallorca": "07", "menorca": "07", "ibiza": "07", "eivissa": "07",
    "barcelona": "08",
    "burgos": "09",
    "caceres": "10",
    "cadiz": "11",
    "castellon": "12", "castello": "12",
    "ciudad real": "13",
    "cordoba": "14",
    "la coruna": "15", "a coruna": "15", "coruna": "15",
    "cuenca": "16",
    "girona": "17", "gerona": "17",
    "granada": "18",
    "guadalajara": "19",
    "guipuzcoa": "20", "gipuzkoa": "20", "san sebastian": "20", "donostia": "20",
    "huelva": "21",
    "huesca": "22",
    "jaen": "23",
    "leon": "24",
    "lleida": "25", "lerida": "25",
    "la rioja": "26", "rioja": "26",
    "lugo": "27",
    "madrid": "28", "comunidad de madrid": "28",
    "malaga": "29",
    "murcia": "30", "region de murcia": "30",
    "navarra": "31", "nafarroa": "31", "pamplona": "31",
    "ourense": "32", "orense": "32",
    "asturias": "33", "principado de asturias": "33", "oviedo": "33",
    "palencia": "34",
    "las palmas": "35", "gran canaria": "35", "fuerteventura": "35", "lanzarote": "35",
    "pontevedra": "36", "vigo": "36",
    "salamanca": "37",
    "tenerife": "38", "santa cruz de tenerife": "38", "la palma": "38", "la gomera": "38", "el hierro": "38",
    "cantabria": "39", "santander": "39",
    "segovia": "40",
    "sevilla": "41", "seville": "41",
    "soria": "42",
    "tarragona": "43",
    "teruel": "44",
    "toledo": "45",
    "valencia": "46",
    "valladolid": "47",
    "vizcaya": "48", "bizkaia": "48", "bilbao": "48", "gran bilbao": "48",
    "zamora": "49",
    "zaragoza": "50",
    "ceuta": "51",
    "melilla": "52",
}


def _normalize_provincia_label(label: str) -> str | None:
    """Convierte 'provincia de Córdoba' / 'Comunidad de Madrid' / 'Gran Bilbao' → INE."""
    if not label:
        return None
    text = unicodedata.normalize("NFD", label)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    # Quitar prefijos comunes
    text = re.sub(r"^(provincia de|provincia|comunidad de|comunidad autonoma de|comunidad)\s+", "", text)
    text = text.strip()
    return _PROVINCIA_NAME_TO_INE.get(text)


_SPARQL_QUERY = """
SELECT DISTINCT ?qid ?label ?industryLabel ?provinciaLabel ?nif ?inception WHERE {
  ?company wdt:P17 wd:Q29 .
  ?company wdt:P31/wdt:P279* wd:Q4830453 .
  ?company rdfs:label ?label .
  FILTER(LANG(?label) = "es")
  BIND(STRAFTER(STR(?company), "entity/") AS ?qid)
  OPTIONAL {
    ?company wdt:P452 ?industry .
    ?industry rdfs:label ?industryLabel .
    FILTER(LANG(?industryLabel) = "es")
  }
  OPTIONAL {
    ?company wdt:P159 ?hq .
    ?hq wdt:P131 ?prov .
    ?prov rdfs:label ?provinciaLabel .
    FILTER(LANG(?provinciaLabel) = "es")
  }
  OPTIONAL { ?company wdt:P3416 ?nif }
  OPTIONAL { ?company wdt:P571 ?inception }
}
LIMIT %d OFFSET %d
"""


async def _fetch_page(client: httpx.AsyncClient, limit: int, offset: int) -> list[dict]:
    response = await client.get(
        _WIKIDATA_ENDPOINT,
        params={"query": _SPARQL_QUERY % (limit, offset)},
        headers={"Accept": "application/sparql-results+json", "User-Agent": _USER_AGENT},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    return data["results"]["bindings"]


def _row_to_empresa_dict(row: dict) -> dict | None:
    """Convierte un binding SPARQL en un dict para insert/upsert. Devuelve None si la
    fila no tiene los campos mínimos (label)."""
    label = row.get("label", {}).get("value", "").strip()
    if not label:
        return None
    qid = row.get("qid", {}).get("value", "").strip()
    if not qid:
        return None
    industry = row.get("industryLabel", {}).get("value")
    provincia_label = row.get("provinciaLabel", {}).get("value")
    provincia_code = _normalize_provincia_label(provincia_label) if provincia_label else None
    nif = row.get("nif", {}).get("value")
    inception = row.get("inception", {}).get("value")

    fecha_constitucion = None
    if inception and len(inception) >= 10:
        try:
            from datetime import date
            fecha_constitucion = date.fromisoformat(inception[:10])
        except (ValueError, TypeError):
            pass

    return {
        "slug": slugify(label),
        "razon_social": label,
        "provincia": provincia_code,
        "domicilio": provincia_label,  # mejor que nada
        "objeto_social": industry,
        "hoja_rm": f"WD:{qid}",
        "fecha_constitucion": fecha_constitucion,
    }


async def sync_wikidata(session: Session, batch_size: int = 1000) -> dict[str, int]:
    """Descarga todas las empresas españolas de Wikidata y las upserta en la tabla
    empresa. Idempotente: si una empresa ya existe (mismo hoja_rm WD:Qxxxx), se
    actualiza con los datos más recientes."""
    stats = {"fetched": 0, "inserted": 0, "skipped": 0, "errors": 0}
    offset = 0
    async with httpx.AsyncClient() as client:
        while True:
            try:
                logger.info("Fetching Wikidata page offset=%d limit=%d", offset, batch_size)
                rows = await _fetch_page(client, batch_size, offset)
            except httpx.HTTPError as exc:
                logger.error("Wikidata fetch error at offset=%d: %s", offset, exc)
                stats["errors"] += 1
                break
            if not rows:
                break

            stats["fetched"] += len(rows)
            for row in rows:
                empresa = _row_to_empresa_dict(row)
                if not empresa:
                    stats["skipped"] += 1
                    continue
                stmt = pg_insert(Empresa).values(**empresa)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["hoja_rm"],
                    set_={
                        "razon_social": stmt.excluded.razon_social,
                        "provincia": stmt.excluded.provincia,
                        "domicilio": stmt.excluded.domicilio,
                        "objeto_social": stmt.excluded.objeto_social,
                        "slug": stmt.excluded.slug,
                        "fecha_constitucion": stmt.excluded.fecha_constitucion,
                    },
                )
                session.execute(stmt)
                stats["inserted"] += 1
            session.commit()

            if len(rows) < batch_size:
                break
            offset += batch_size
            # Polite delay between pages
            await asyncio.sleep(1.0)
    return stats
