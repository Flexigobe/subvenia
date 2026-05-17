"""Orquesta las fuentes de enrichment del NIF. De momento solo libreborme; el plan original
incluía OpenCorporates pero su free tier desapareció (£2.250/año mínimo)."""

from __future__ import annotations

from typing import Any

from app.enrich.libreborme import fetch_company


async def enrich_nif(nif: str) -> dict[str, Any] | None:
    """Intenta encontrar datos de la empresa por NIF. Devuelve dict con keys
    razon_social y opcionalmente provincia_text, o None si ninguna fuente responde."""
    # NB: cuando se añadan más fuentes (Plan 3+) merge aquí prioritizando libreborme.
    result = await fetch_company(nif)
    if result and result.get("razon_social"):
        return result
    return None
