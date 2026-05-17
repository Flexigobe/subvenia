"""Orquesta las fuentes de enrichment del NIF.

Plan 3 cambió libreborme (pasó a pago en 2026) por VIES (validador VAT oficial UE,
público y gratuito). Nota: España devuelve "---" para name/address en VIES — el servicio
confirma que el NIF existe en el censo VAT pero razon_social puede ser None."""

from __future__ import annotations

from typing import Any

from app.enrich.vies import fetch_company


async def enrich_nif(nif: str) -> dict[str, Any] | None:
    """Intenta encontrar datos de la empresa por NIF. Devuelve dict con keys
    razon_social y opcionalmente provincia_text, o None si VIES no devuelve datos."""
    result = await fetch_company(nif)
    # Devolvemos el resultado si el NIF es válido en VIES (aunque razon_social sea None)
    # El caller (routes_enrich) decide cómo presentar datos parciales al usuario.
    if result is not None:
        return result
    return None
