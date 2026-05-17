"""Mapeo del detail endpoint BDNS al modelo Subvencion + heurística de finalidad."""

from __future__ import annotations

import unicodedata
from datetime import date as date_t
from typing import Any

_NIVEL1_TO_AMBITO: dict[str, str] = {
    "ESTATAL": "estatal",
    "AUTONÓMICA": "autonomico",
    "AUTONOMICA": "autonomico",
    "LOCAL": "local",
    "EUROPEA": "ue",
}

# (keyword_lowercase_normalized, finalidad_token). Primer match gana, varias keywords pueden mapear al mismo token.
_FINALIDAD_KEYWORDS: list[tuple[str, str]] = [
    ("digital", "digitalizacion"),
    ("ti c", "digitalizacion"),  # TIC con espacio (post-normalización)
    ("i+d", "i+d"),
    ("i+i", "i+d"),
    ("investigaci", "i+d"),
    ("desarrollo experimental", "i+d"),
    ("contrat", "contratacion"),
    ("empleo", "contratacion"),
    ("energ", "eficiencia_energetica"),
    ("renov", "eficiencia_energetica"),
    ("internacional", "internacionalizacion"),
    ("export", "internacionalizacion"),
    ("formaci", "formacion"),
    ("formativ", "formacion"),
    ("educati", "formacion"),
    ("innovaci", "innovacion"),
]


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _normalize(s: str) -> str:
    return _strip_accents(s).lower()


def _to_date(value: str | None) -> date_t | None:
    if not value:
        return None
    try:
        return date_t.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def infer_finalidad(text: str | None) -> list[str]:
    """A partir de texto libre (típicamente descripcionFinalidad), devuelve tokens normalizados.

    Si nada matchea pero hay texto, devuelve ['otros'] para que el record sea elegible para
    el filtro lenient pero con score bajo. Si no hay texto, devuelve [].
    """
    if not text:
        return []
    norm = _normalize(text)
    matched: list[str] = []
    seen: set[str] = set()
    for kw, token in _FINALIDAD_KEYWORDS:
        if kw in norm and token not in seen:
            matched.append(token)
            seen.add(token)
    if not matched:
        matched.append("otros")
    return matched


def map_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Mapea respuesta del detail endpoint BDNS al dict listo para upsert en Subvencion."""
    organo = detail.get("organo") or {}
    nivel1 = (organo.get("nivel1") or "").upper()
    ambito = _NIVEL1_TO_AMBITO.get(nivel1, "estatal")

    organismo = organo.get("nivel3") or organo.get("nivel2") or organo.get("nivel1")

    sectores = detail.get("sectores") or []
    cnae_elegible = [s.get("codigo") for s in sectores if s.get("codigo")]

    tipos_benef = detail.get("tiposBeneficiarios") or []
    beneficiarios = (
        {"tipos": [b.get("descripcion") for b in tipos_benef if b.get("descripcion")]}
        if tipos_benef
        else None
    )

    anuncios = detail.get("anuncios") or []
    enlace_oficial = None
    if anuncios and anuncios[0].get("url"):
        enlace_oficial = anuncios[0]["url"]
    elif detail.get("urlBasesReguladoras"):
        enlace_oficial = detail["urlBasesReguladoras"]

    fecha_inicio = _to_date(detail.get("fechaInicioSolicitud")) or _to_date(detail.get("fechaRecepcion"))
    fecha_fin = _to_date(detail.get("fechaFinSolicitud"))

    descripcion = detail.get("descripcionBasesReguladoras") or detail.get("descripcion")

    return {
        "source": "bdns",
        "external_id": str(detail.get("codigoBDNS")),
        "titulo": detail.get("descripcion") or "",
        "organismo": organismo,
        "ambito": ambito,
        "ccaa": None,
        "fecha_inicio": fecha_inicio,
        "fecha_fin": fecha_fin,
        "importe_total": detail.get("presupuestoTotal"),
        "importe_max_beneficiario": None,
        "porcentaje": None,
        "beneficiarios": beneficiarios,
        "cnae_elegible": cnae_elegible,
        "finalidad": infer_finalidad(detail.get("descripcionFinalidad")),
        "descripcion": descripcion,
        "enlace_oficial": enlace_oficial,
        "raw_payload": detail,
        "estado": "abierta" if detail.get("abierto") else "cerrada",
    }
