"""Cliente para VIES (VAT Information Exchange System) — validador oficial UE público y gratuito.
Reemplaza libreborme.net (pasó a pago en 2026).

Nota importante: España devuelve "---" en los campos name/address de VIES por política nacional;
VIES solo garantiza que el NIF/CIF está registrado como VAT en la AEAT. El campo razon_social
quedará None para NIFs españoles a menos que VIES llegue a publicar los datos."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)
_HEADERS = {"Accept": "application/json", "User-Agent": "subvenciones-app/0.1"}
_VIES_REST_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/{nif}"
_TIMEOUT_S = 15.0

# Sentinel que VIES devuelve cuando el estado miembro no publica los datos
_VIES_NO_DATA = "---"


async def fetch_company(nif: str) -> dict[str, Any] | None:
    """Consulta VIES y devuelve datos normalizados de la empresa.

    Args:
        nif: NIF/CIF español sin prefijo "ES" (ej. "B12345674").

    Returns:
        {"razon_social": str | None, "provincia_text": str | None, "raw": dict}
        si VIES confirma que el NIF es un VAT válido registrado.
        None si VIES indica inválido, hay error de red, o el NIF no existe.

    Nota: España devuelve "---" para name/address en VIES (política nacional);
    razon_social y provincia_text serán None en ese caso aunque el NIF sea válido.
    """
    url = _VIES_REST_URL.format(nif=nif)
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_S, headers=_HEADERS, follow_redirects=True
        ) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                logger.warning("VIES %s for %s", r.status_code, nif)
                return None
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("VIES request failed for %s: %s", nif, exc)
        return None

    # isValid es el flag correcto según la API REST actual (comprobado 2026-05-17)
    valid_flag = data.get("isValid")
    if not valid_flag:
        return None

    # Extrae name — España devuelve "---" pero otros estados miembro publican el nombre
    raw_name = data.get("name") or data.get("traderName") or ""
    name = raw_name.strip() if raw_name.strip() != _VIES_NO_DATA else None

    # Extrae address
    raw_address = data.get("address") or ""
    address = raw_address.strip() if raw_address.strip() != _VIES_NO_DATA else ""
    provincia_text = _extract_provincia(address) if address else None

    return {
        "razon_social": name,
        "provincia_text": provincia_text,
        "raw": data,
    }


def _extract_provincia(address: str) -> str | None:
    """Heurística para extraer la provincia/ciudad del campo address de VIES.

    Las direcciones VIES suelen tener formato:
      "C. EJEMPLO 1\\n28001 MADRID"
    La última línea suele ser código postal + provincia/ciudad.
    """
    if not address:
        return None
    lines = [ln.strip() for ln in address.split("\n") if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    # Última línea típicamente "CP CIUDAD" → eliminar código postal (5 dígitos)
    parts = last.split(maxsplit=1)
    if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == 5:
        return parts[1].title()
    return last.title()
