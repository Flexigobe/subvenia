"""BORME sumario XML fetcher + PDF downloader.

Source: BOE Datos Abiertos (official Spanish government open data, CC-BY-NC-ND).
URL pattern: https://www.boe.es/datosabiertos/api/borme/sumario/{YYYYMMDD}

BORME publishes Mon-Fri. Weekends/holidays → 404 (returned as []).
No rate limit verified, but we use a 30s timeout and reasonable batching.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.boe.es/datosabiertos/api/borme/sumario"
_HEADERS = {"Accept": "application/xml", "User-Agent": "subvenciones-app/0.5"}
_TIMEOUT = 30.0


# Province name → INE 2-digit code. The BORME-A titles use province names; we need
# the INE code to align with the empresa.provincia column (which uses INE 01-52).
# Include common variants (with/without accents, bilingual forms).
PROVINCIA_NAME_TO_INE: dict[str, str] = {
    "ÁLAVA": "01", "ALAVA": "01",
    "ALBACETE": "02",
    "ALICANTE": "03", "ALICANTE/ALACANT": "03",
    "ALMERÍA": "04", "ALMERIA": "04",
    "ÁVILA": "05", "AVILA": "05",
    "BADAJOZ": "06",
    "BALEARES": "07", "ILLES BALEARS": "07",
    "BARCELONA": "08",
    "BURGOS": "09",
    "CÁCERES": "10", "CACERES": "10",
    "CÁDIZ": "11", "CADIZ": "11",
    "CASTELLÓN": "12", "CASTELLON": "12", "CASTELLÓN/CASTELLÓ": "12", "CASTELLÓN/CASTELLO": "12",
    "CIUDAD REAL": "13",
    "CÓRDOBA": "14", "CORDOBA": "14",
    "A CORUÑA": "15", "LA CORUÑA": "15", "CORUÑA": "15", "A CORUNA": "15",
    "CUENCA": "16",
    "GIRONA": "17", "GERONA": "17",
    "GRANADA": "18",
    "GUADALAJARA": "19",
    "GIPUZKOA": "20", "GUIPÚZCOA": "20", "GUIPUZCOA": "20",
    "HUELVA": "21",
    "HUESCA": "22",
    "JAÉN": "23", "JAEN": "23",
    "LEÓN": "24", "LEON": "24",
    "LLEIDA": "25", "LÉRIDA": "25", "LERIDA": "25",
    "LA RIOJA": "26", "RIOJA": "26",
    "LUGO": "27",
    "MADRID": "28",
    "MÁLAGA": "29", "MALAGA": "29",
    "MURCIA": "30",
    "NAVARRA": "31",
    "OURENSE": "32", "ORENSE": "32",
    "ASTURIAS": "33", "PRINCIPADO DE ASTURIAS": "33",
    "PALENCIA": "34",
    "LAS PALMAS": "35",
    "PONTEVEDRA": "36",
    "SALAMANCA": "37",
    "SANTA CRUZ DE TENERIFE": "38", "S/C TENERIFE": "38", "TENERIFE": "38",
    "CANTABRIA": "39",
    "SEGOVIA": "40",
    "SEVILLA": "41",
    "SORIA": "42",
    "TARRAGONA": "43",
    "TERUEL": "44",
    "TOLEDO": "45",
    "VALENCIA": "46", "VALENCIA/VALÈNCIA": "46", "VALENCIA/VALENCIA": "46",
    "VALLADOLID": "47",
    "BIZKAIA": "48", "VIZCAYA": "48",
    "ZAMORA": "49",
    "ZARAGOZA": "50",
    "CEUTA": "51",
    "MELILLA": "52",
}


def _normalize_provincia_name(name: str) -> str:
    return name.strip().upper()


def _provincia_to_ine(name: str) -> str | None:
    """Maps a BORME province title to INE 2-digit code. None on unknown / index pages."""
    normalized = _normalize_provincia_name(name)
    return PROVINCIA_NAME_TO_INE.get(normalized)


async def fetch_sumario(target: date, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Fetch the BORME sumario for `target` date.

    Returns:
        list of dicts: [{"identificador": "BORME-A-2025-91-03", "titulo": "ALICANTE/ALACANT",
                         "url_pdf": "https://...", "provincia": "03"}]
        Items where the title can't be mapped to a province (e.g., "ÍNDICE ALFABÉTICO") are
        kept but with provincia=None. Empty list if BORME wasn't published (weekend/holiday).
    """
    url = f"{_BASE_URL}/{target.strftime('%Y%m%d')}"
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS)
    try:
        try:
            r = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("BORME sumario fetch failed for %s: %s", target, exc)
            return []
        if r.status_code == 404:
            logger.info("BORME sumario %s → 404 (no publication that day)", target)
            return []
        if r.status_code >= 500:
            logger.warning("BORME sumario %s → %s", target, r.status_code)
            return []
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "xml" not in ctype:
            logger.warning("BORME sumario %s: unexpected content-type %s", target, ctype)
            return []

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as exc:
            logger.warning("BORME sumario %s: XML parse error: %s", target, exc)
            return []

        items: list[dict] = []
        # Find every section A item (we ignore B and C — only A has empresa actos)
        for seccion in root.iter("seccion"):
            if seccion.get("codigo") != "A":
                continue
            for item in seccion.iter("item"):
                identificador = (item.findtext("identificador") or "").strip()
                titulo = (item.findtext("titulo") or "").strip()
                url_pdf_el = item.find("url_pdf")
                url_pdf = (url_pdf_el.text or "").strip() if url_pdf_el is not None else ""
                if not identificador or not url_pdf:
                    continue
                # Skip the alphabetical index (id ends in -99)
                if identificador.endswith("-99"):
                    continue
                provincia = _provincia_to_ine(titulo)
                if provincia is None:
                    logger.debug("BORME province not mapped: %r (id=%s)", titulo, identificador)
                items.append({
                    "identificador": identificador,
                    "titulo": titulo,
                    "url_pdf": url_pdf,
                    "provincia": provincia,
                })
        return items
    finally:
        if owns:
            await client.aclose()


async def fetch_pdf(url: str, client: httpx.AsyncClient | None = None) -> bytes | None:
    """Download a BORME PDF. Returns the bytes, or None on error."""
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=60.0, headers=_HEADERS)
    try:
        try:
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning("BORME PDF %s → %s", url, r.status_code)
                return None
            return r.content
        except httpx.HTTPError as exc:
            logger.warning("BORME PDF fetch failed for %s: %s", url, exc)
            return None
    finally:
        if owns:
            await client.aclose()
