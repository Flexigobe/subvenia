"""Cliente para libreborme.net — fuente pública gratis de BORME."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)
_HEADERS = {"User-Agent": "subvenciones-app/0.1", "Accept": "application/json"}


async def fetch_company(nif: str) -> dict[str, Any] | None:
    """Devuelve datos básicos de la empresa o None si no se encuentra / hay error.

    Silencioso: nunca propaga excepciones de red; logguea warning si hay fallo.
    """
    url = f"https://libreborme.net/api/company/{nif}/"
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                logger.warning("libreborme %s for %s", r.status_code, nif)
                return None
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:  # ValueError covers JSON decode errors
        logger.warning("libreborme request failed for %s: %s", nif, exc)
        return None

    # Normalize the response shape we care about
    return {
        "razon_social": data.get("name"),
        "provincia_text": data.get("province"),
        "raw": data,
    }
