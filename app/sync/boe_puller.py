"""Cliente para la API oficial del BOE (datosabiertos.boe.es).

Endpoint: GET /datosabiertos/api/boe/sumario/{aaaammdd}
Formato: XML estructurado por secciones → departamentos → epígrafes → items

Las convocatorias de subvenciones se publican en:
- Sección III "Otras disposiciones" — Resoluciones, Órdenes, Convocatorias.
- Epígrafes típicos: "Subvenciones", "Becas", "Ayudas", "Premios" (filtramos premios).

Ventaja sobre BDNS: el BOE las publica el DÍA cero. BDNS las indexa 1-3 días después.
Permite mostrar "última hora oficial" en /noticias antes que ningún otro agregador.

Licencia: las condiciones BOE permiten reutilización con cita "Fuente: AEBOE".
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

_API_BASE = "https://www.boe.es/datosabiertos/api"
_HEADERS = {"Accept": "application/xml", "User-Agent": "subvenciones-app/0.1"}

# Filtros para detectar items que SON convocatorias de subvenciones / ayudas a empresas.
# Excluimos los que NO interesan (premios individuales, becas tesis, etc.).
_EPIGRAFE_KEYWORDS = re.compile(
    r"\b(subvenciones?|ayudas?|convocatorias?|becas?\s+empresa|emprendimiento)\b",
    re.IGNORECASE,
)
_TITULO_EXCLUYE = re.compile(
    r"\b(beca\s+de\s+tesis|premios?\s+nacional|premios?\s+literari|tesis\s+doctoral|"
    r"convenio\s+entre|concesi[óo]n\s+directa\s+a\s+|"
    r"nombramiento|cese|jubilaci[óo]n)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BoeItem:
    """Anuncio del BOE: convocatoria de subvención, ayuda o similar."""
    identificador: str          # BOE-A-2026-10667
    fecha_publicacion: date
    departamento: str           # MINISTERIO DE HACIENDA
    epigrafe: str               # Subvenciones
    titulo: str
    url_pdf: str
    url_html: str
    url_xml: str


async def fetch_sumario(fecha: date) -> str | None:
    """Descarga el sumario XML del BOE para una fecha. Devuelve None si 404 (sin publicación).

    El BOE no publica domingos ni festivos. Es legítimo recibir 404 para esos días.
    """
    url = f"{_API_BASE}/boe/sumario/{fecha.strftime('%Y%m%d')}"
    async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS) as client:
        try:
            r = await client.get(url)
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                logger.warning("BOE sumario %s returned %d", fecha, r.status_code)
                return None
            return r.text
        except Exception as exc:
            logger.warning("BOE sumario %s error: %s", fecha, exc)
            return None


def parse_sumario(xml_content: str) -> list[BoeItem]:
    """Extrae los items que pueden ser convocatorias de subvenciones.

    El XML tiene estructura:
      response/data/sumario/diario/seccion[@codigo]/departamento/epigrafe/item

    Filtramos:
      - Solo Sección III "Otras disposiciones" (donde van las convocatorias).
      - Solo epígrafes que contengan keywords de subvenciones.
      - Excluimos premios individuales / becas tesis / nombramientos.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        logger.warning("BOE XML parse error: %s", exc)
        return []

    # Fecha de la publicación
    fecha_str = root.findtext(".//metadatos/fecha_publicacion") or ""
    try:
        fecha_pub = date.fromisoformat(f"{fecha_str[:4]}-{fecha_str[4:6]}-{fecha_str[6:8]}")
    except (ValueError, TypeError):
        return []

    items: list[BoeItem] = []
    for seccion in root.findall(".//seccion"):
        seccion_codigo = seccion.get("codigo", "")
        # Las convocatorias normalmente están en sección III "Otras disposiciones".
        # Algunas también en V "Anuncios" (subasta de bienes, etc., no nos interesan).
        if seccion_codigo not in ("3", "III"):
            continue
        for departamento in seccion.findall(".//departamento"):
            depto_nombre = departamento.get("nombre", "")
            for epigrafe in departamento.findall(".//epigrafe"):
                epi_nombre = epigrafe.get("nombre", "")
                if not _EPIGRAFE_KEYWORDS.search(epi_nombre):
                    continue
                for item in epigrafe.findall(".//item"):
                    titulo = (item.findtext("titulo") or "").strip()
                    if not titulo or _TITULO_EXCLUYE.search(titulo):
                        continue
                    identificador = (item.findtext("identificador") or "").strip()
                    if not identificador:
                        continue
                    url_pdf = (item.findtext("url_pdf") or "").strip()
                    url_html = (item.findtext("url_html") or "").strip()
                    url_xml = (item.findtext("url_xml") or "").strip()
                    items.append(BoeItem(
                        identificador=identificador,
                        fecha_publicacion=fecha_pub,
                        departamento=depto_nombre[:120],
                        epigrafe=epi_nombre[:80],
                        titulo=titulo[:500],
                        url_pdf=url_pdf,
                        url_html=url_html,
                        url_xml=url_xml,
                    ))
    return items


async def fetch_last_n_days(days: int = 7) -> list[BoeItem]:
    """Bajo y parsea los últimos N días de sumarios BOE en PARALELO.

    Devuelve los items de subvenciones/ayudas en orden cronológico descendente.
    Tolera días sin publicación (domingos, festivos) → 404.
    """
    import asyncio
    today = date.today()
    fechas = [today - timedelta(days=offset) for offset in range(days)]
    # Fetch paralelo de los 7 días
    xmls = await asyncio.gather(*[fetch_sumario(f) for f in fechas], return_exceptions=True)
    all_items: list[BoeItem] = []
    for xml in xmls:
        if isinstance(xml, Exception) or xml is None:
            continue
        try:
            all_items.extend(parse_sumario(xml))
        except Exception:
            continue
    all_items.sort(key=lambda i: (i.fecha_publicacion, i.identificador), reverse=True)
    return all_items
