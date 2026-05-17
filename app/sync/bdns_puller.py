"""Cliente HTTP para la BDNS (Base de Datos Nacional de Subvenciones)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.config import get_settings

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
