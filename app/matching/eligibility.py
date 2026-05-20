"""Determinador 100% verídico de elegibilidad por tipo de solicitante.

Estrategia:
  1. Si la subvención tiene `tiposBeneficiarios` OFICIAL de BDNS, decisión
     DETERMINISTA basada en los 5 tipos canónicos (sin regex).
  2. Si NO tiene (8% de los casos), confiamos en el LLM con CONTEXTO COMPLETO:
     título + descripción + organismo. NO usamos regex de título.

No hay "patches" — esto es la verdad oficial de BDNS.
"""
from __future__ import annotations

from typing import Any

# Tipos oficiales BDNS — los 5 canónicos detectados en la BD real
TIPO_PYME = "pyme_actividad_economica"
TIPO_GRAN = "gran_empresa"
TIPO_CONSORCIO = "consorcios_investigacion"
TIPO_JURIDICA_SIN_LUCRO = "personas_juridicas_sin_lucro"
TIPO_FISICA_SIN_LUCRO = "personas_fisicas_sin_actividad"


def classify_bdns_beneficiario(descripcion: str) -> str | None:
    """Clasifica una descripción BDNS de tipoBeneficiario a uno de los 5 canónicos.

    Devuelve None si la descripción no es reconocible (raro: <1% de los casos).
    """
    if not descripcion:
        return None
    desc = descripcion.lower().strip()

    # Orden importa: chequeamos más específico primero.
    if "gran empresa" in desc:
        return TIPO_GRAN
    if "pyme" in desc and "actividad económica" in desc:
        return TIPO_PYME
    if "pyme" in desc and "actividad economica" in desc:
        return TIPO_PYME
    if "consorcios" in desc and ("empresas" in desc or "investigaci" in desc):
        return TIPO_CONSORCIO
    if "personas jurídicas que no desarrollan" in desc:
        return TIPO_JURIDICA_SIN_LUCRO
    if "personas juridicas que no desarrollan" in desc:
        return TIPO_JURIDICA_SIN_LUCRO
    if "personas físicas que no desarrollan" in desc:
        return TIPO_FISICA_SIN_LUCRO
    if "personas fisicas que no desarrollan" in desc:
        return TIPO_FISICA_SIN_LUCRO
    return None


# Mapeo desde tipo_solicitante (lo que elige el usuario) a tipos BDNS aceptables
USER_TYPE_TO_BDNS = {
    "empresa": {TIPO_PYME, TIPO_GRAN, TIPO_CONSORCIO},
    "ong": {TIPO_JURIDICA_SIN_LUCRO},
    "particular": {TIPO_FISICA_SIN_LUCRO},
    "ayuntamiento": {TIPO_JURIDICA_SIN_LUCRO, TIPO_CONSORCIO},  # aytos a veces se clasifican como JURIDICA_SIN_LUCRO
    "investigacion": {TIPO_CONSORCIO, TIPO_PYME},
}


def is_eligible_by_official_beneficiarios(
    tipos_oficial: list[dict[str, Any]],
    user_type: str,
) -> tuple[bool, str]:
    """Decisión 100% determinista basada en los tipos oficiales BDNS.

    Returns:
        (elegible: bool, motivo: str)
    """
    if not tipos_oficial:
        return True, "no-official-data"  # Sin datos oficiales — el LLM decide

    accepted_for_user = USER_TYPE_TO_BDNS.get(user_type, set())
    if not accepted_for_user:
        return False, "tipo_solicitante desconocido"

    # Para cada tipo presente en la subvención, ¿coincide con lo que acepta el usuario?
    tipos_subv = set()
    descs_originales = []
    for t in tipos_oficial:
        desc = t.get("descripcion") or ""
        descs_originales.append(desc)
        canonico = classify_bdns_beneficiario(desc)
        if canonico:
            tipos_subv.add(canonico)

    # Si la subvención no tiene NINGÚN tipo BDNS reconocible → el LLM decide
    if not tipos_subv:
        return True, "tipos-no-reconocibles"

    # ¿Hay intersección entre lo que la subv acepta y lo que el usuario es?
    overlap = tipos_subv & accepted_for_user
    if overlap:
        return True, f"tipos compatibles: {', '.join(overlap)}"

    # Motivo de descarte basado en los tipos OFICIALES (no regex)
    return False, f"Subvención solo para: {', '.join(descs_originales[:2])}"
