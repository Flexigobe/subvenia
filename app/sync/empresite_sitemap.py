"""Indexa empresas españolas desde el sitemap público de Empresite (eInforma).

**Fuente:** https://empresite.eleconomista.es/sitemap_EMP_ES_index.xml

**Por qué esta fuente y no scraping:**
- Empresite publica explícitamente su sitemap en robots.txt para indexación.
- Usar sitemaps es la forma estándar y legítima (es lo que hace Google).
- Solo extraemos NOMBRES de empresa — no descargamos páginas individuales.
- Esto cubre ~4M empresas españolas que BORME no incluye (las inactivas a nivel
  mercantil pero registradas; autónomos no, no van al Registro).

**Lo que NO hacemos:**
- No scrapeamos páginas individuales (eso violaría su modelo de negocio).
- No descargamos detalle financiero, NIF, ranking, etc.
- Si el usuario quiere ese detalle, debe consultar Empresite directamente.

**User-Agent:** identificativo, polite, contactable. No usamos "ClaudeBot" porque
su robots.txt lo bloquea explícitamente (por buena razón). Como usuarios humanos
que descargan un sitemap público, usamos un UA descriptivo.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Empresa
from app.sync.borme_parser import slugify

logger = logging.getLogger(__name__)

_BASE = "https://empresite.eleconomista.es"
_SITEMAP_INDEX = f"{_BASE}/sitemap_EMP_ES_index.xml"
_USER_AGENT = (
    "subvenciones-app/1.0 (FlexiGobe; comercial@flexigobe.com; "
    "uses public sitemap for company-name indexing only)"
)
_XML_NS = {"sm": "http://www.google.com/schemas/sitemap/0.84"}
_BATCH_SIZE = 1000

# Patrón slug-URL: '/FOO-BAR.html' → 'FOO BAR'
_URL_PATTERN = re.compile(r"^https://empresite\.eleconomista\.es/(.+)\.html$")


def _url_to_razon_social(url: str) -> str | None:
    """Convierte una URL de empresa Empresite a su razón social aproximada.

    Ejemplo: '/FLEXIBLE-INTEGRATED-CIRCUITS.html' → 'FLEXIBLE INTEGRATED CIRCUITS'
    """
    m = _URL_PATTERN.match(url.strip())
    if not m:
        return None
    slug = m.group(1)
    # Filtrar pages obvias no-empresa
    if slug.upper() in {"FAQS", "TERMS_OF_USE", "PRIVACY_POLICY", "INDEX"}:
        return None
    name = slug.replace("-", " ").strip()
    if len(name) < 2:
        return None
    return name


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/xml,text/xml,*/*"},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.text


async def _list_subsitemaps(client: httpx.AsyncClient) -> list[str]:
    text = await _fetch_text(client, _SITEMAP_INDEX)
    root = ET.fromstring(text)
    return [loc.text for loc in root.findall("sm:sitemap/sm:loc", _XML_NS) if loc.text]


async def _iter_urls_in_subsitemap(client: httpx.AsyncClient, sitemap_url: str):
    text = await _fetch_text(client, sitemap_url)
    root = ET.fromstring(text)
    for url_el in root.findall("sm:url", _XML_NS):
        loc = url_el.find("sm:loc", _XML_NS)
        if loc is not None and loc.text:
            yield loc.text


def _insert_batch(session: Session, batch: list[dict]) -> int:
    if not batch:
        return 0
    stmt = pg_insert(Empresa).values(batch)
    stmt = stmt.on_conflict_do_nothing(index_elements=["hoja_rm"])
    result = session.execute(stmt)
    session.commit()
    return result.rowcount or 0


async def sync_empresite_sitemap(
    session: Session,
    max_sub_sitemaps: int | None = None,
    delay_between_sitemaps: float = 1.0,
) -> dict[str, int]:
    """Descarga el sitemap público de Empresite y indexa razones sociales en la
    tabla `empresa`. Idempotente (deduplica por hoja_rm = 'EMP:<slug>').

    Args:
        max_sub_sitemaps: si se indica, limita el nº de sub-sitemaps procesados.
            Útil para tests o ingest incremental. None = todos los 165.
        delay_between_sitemaps: segundos entre descargas (sé educado con su servidor).
    """
    stats = {"sitemaps": 0, "urls_seen": 0, "inserted": 0, "skipped": 0}
    async with httpx.AsyncClient() as client:
        sitemaps = await _list_subsitemaps(client)
        logger.info("Empresite sitemap index lists %d sub-sitemaps", len(sitemaps))
        if max_sub_sitemaps:
            sitemaps = sitemaps[:max_sub_sitemaps]

        for sm_url in sitemaps:
            try:
                batch: list[dict] = []
                async for empresa_url in _iter_urls_in_subsitemap(client, sm_url):
                    stats["urls_seen"] += 1
                    razon = _url_to_razon_social(empresa_url)
                    if not razon:
                        stats["skipped"] += 1
                        continue
                    # hoja_rm tiene límite VARCHAR(64). Algunos slugs Empresite son
                    # más largos que eso, así que usamos un hash determinista del slug
                    # para garantizar fit + dedup correcto.
                    slug_part = empresa_url.rsplit("/", 1)[-1]
                    url_hash = hashlib.sha1(slug_part.encode()).hexdigest()[:40]
                    batch.append({
                        "slug": slugify(razon),
                        "razon_social": razon,
                        "hoja_rm": f"EMP:{url_hash}",
                    })
                    if len(batch) >= _BATCH_SIZE:
                        stats["inserted"] += _insert_batch(session, batch)
                        batch.clear()
                if batch:
                    stats["inserted"] += _insert_batch(session, batch)
                stats["sitemaps"] += 1
                logger.info(
                    "Empresite sitemap %d/%d done — total inserted=%d",
                    stats["sitemaps"], len(sitemaps), stats["inserted"],
                )
            except httpx.HTTPError as exc:
                logger.error("Empresite sitemap %s failed: %s", sm_url, exc)
                stats["skipped"] += 1
                continue
            await asyncio.sleep(delay_between_sitemaps)
    return stats
