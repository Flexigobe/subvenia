"""BORME PDF text extraction + per-entry parser.

PDFs of section BORME-A contain ~50-100 company entries per province per day.
Each entry has a deterministic structure starting with `NNNNNN - RAZÓN SOCIAL.`
followed by one or more actos and structured fields (Domicilio, Capital,
Datos registrales, etc.). This parser is regex-based — robust to most format
variations observed but may miss exotic cases.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# ─── Slugify ──────────────────────────────────────────────────────────────────

_SUFFIX_PATTERN = re.compile(
    r"\b(?:s\.?l\.?n\.?e\.?|s\.?l\.?u\.?|s\.?a\.?u\.?|s\.?l\.?l\.?p\.?|s\.?l\.?p\.?|s\.?a\.?p\.?|s\.?c\.?p\.?|s\.?l\.?|s\.?a\.?|s\.?c\.?|coop\.?)\b\.?",
    flags=re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def slugify(razon_social: str) -> str:
    if not razon_social:
        return ""
    s = _strip_accents(razon_social).lower()
    # Strip trailing punctuation
    s = s.strip(" .,;:")
    # Strip suffixes (may appear multiple times: "ACME S.L. (en liquidación) S.L." — rare but happens)
    s = _SUFFIX_PATTERN.sub("", s)
    # Collapse whitespace + remove other punctuation
    s = re.sub(r"[.,;:()\\/]", " ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


# ─── PDF text extraction ──────────────────────────────────────────────────────


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Return concatenated text from all pages of the PDF."""
    from pypdf import PdfReader

    if not pdf_bytes:
        return ""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        logger.warning("pypdf failed to open: %s", exc)
        return ""
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:
            logger.warning("pypdf extract_text failed on page: %s", exc)
            parts.append("")
    return "\n".join(parts)


# ─── Entry parser ─────────────────────────────────────────────────────────────

# An entry starts with a 5-7 digit number, optional whitespace, a dash, more whitespace,
# then the razón social (UPPERCASE letters/spaces/punctuation) ending with a period.
_ENTRY_HEADER_RE = re.compile(
    r"(?:^|\n)\s*(\d{4,7})\s*-\s*([^.\n]{3,200}?)\.\s*",
    flags=re.MULTILINE,
)

# Acto type keywords. Order matters: longer match first.
_ACTO_KEYWORDS = sorted([
    "Cambio de denominación social",
    "Cambio de objeto social",
    "Cambio de domicilio",
    "Declaración de unipersonalidad",
    "Pérdida del carácter de unipersonalidad",
    "Ampliación de objeto social",
    "Reducción de objeto social",
    "Ampliación de capital",
    "Reducción de capital",
    "Declaración de concurso",
    "Sociedad unipersonal",
    "Sociedad disuelta",
    "Fusión por absorción",
    "Datos registrales",
    "Constitución",
    "Modificación",
    "Nombramientos",
    "Reelecciones",
    "Disolución",
    "Liquidador",
    "Reactivación",
    "Escisión",
    "Apoderamientos",
    "Cese",
], key=len, reverse=True)

_ACTO_PATTERN = re.compile(
    r"(?:^|\.\s+|\n)(" + "|".join(re.escape(k) for k in _ACTO_KEYWORDS) + r")(?:\.|\:)",
)

_HOJA_RM_RE = re.compile(r"(H\s+[A-Z]\s+\d+(?:\s*,\s*I/A\s+\d+)?)")
_FECHA_REGISTRAL_RE = re.compile(r"\((\d{1,2}\.\d{1,2}\.\d{2})\)\s*\.?\s*$")
_CAPITAL_RE = re.compile(r"Capital:\s*([\d.,]+)\s*Euros", flags=re.IGNORECASE)
_COMIENZO_OPS_RE = re.compile(r"Comienzo de operaciones:\s*(\d{1,2}\.\d{1,2}\.\d{2,4})")
_DOMICILIO_RE = re.compile(
    r"Domicilio:\s*(.+?)(?=(?:Capital|Objeto social|Datos registrales|Nombramientos|Cese|Reelecciones|Constitución|Modificación|Disolución|Cambio|Declaración|Ampliación|Reducción|$))",
    flags=re.IGNORECASE | re.DOTALL,
)
_OBJETO_RE = re.compile(
    r"Objeto social:\s*(.+?)(?=(?:Capital|Domicilio|Datos registrales|Nombramientos|Cese|Reelecciones|Constitución|Modificación|Disolución|Cambio|Declaración|Ampliación|Reducción|$))",
    flags=re.IGNORECASE | re.DOTALL,
)


def _parse_date_short(s: str) -> date | None:
    """Parse 'dd.mm.yy' (2-digit year) or 'dd.mm.yyyy' (4-digit). Returns None on failure."""
    s = s.strip()
    parts = s.split(".")
    if len(parts) != 3:
        return None
    try:
        d, m, y = (int(p) for p in parts)
    except ValueError:
        return None
    if y < 100:
        # 2-digit year: 00-49 → 2000-2049, 50-99 → 1950-1999
        y += 2000 if y < 50 else 1900
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _parse_capital(s: str) -> Decimal | None:
    """Parse Spanish-formatted number: '3.000,00' → Decimal('3000.00')."""
    if not s:
        return None
    cleaned = s.replace(".", "").replace(",", ".").strip()
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _clean(text: str) -> str:
    """Collapse whitespace and strip."""
    return _WHITESPACE_RE.sub(" ", text).strip(" .,;:")


def _extract_actos(body: str) -> list[dict]:
    """Find all acto types in the body, in order of appearance."""
    actos: list[dict] = []
    seen = set()
    for match in _ACTO_PATTERN.finditer(body):
        tipo = match.group(1)
        if tipo in seen:
            continue
        seen.add(tipo)
        actos.append({"tipo": tipo})
    return actos


def parse_pdf_text(text: str, provincia_code: str | None = None) -> list[dict]:
    """Find every entry in `text` and return a list of dicts ready to upsert.

    Args:
        text: Concatenated plain text extracted from a BORME-A PDF.
        provincia_code: INE 2-digit province code (e.g. "03" for Alicante).

    Returns:
        List of dicts with keys: slug, razon_social, provincia, domicilio,
        objeto_social, hoja_rm, capital_social, fecha_constitucion,
        fecha_ultima_act, actos (list), estado, raw_text.
    """
    entries: list[dict] = []
    if not text:
        return entries

    # Find all entry header positions
    matches = list(_ENTRY_HEADER_RE.finditer(text))
    if not matches:
        return entries

    for i, m in enumerate(matches):
        razon_social = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]

        if len(razon_social) < 3 or len(razon_social) > 200:
            continue

        # Actos
        actos = _extract_actos(body)
        estado = "activa"
        for a in actos:
            if a["tipo"] in ("Disolución", "Sociedad disuelta"):
                estado = "disuelta"
            elif a["tipo"] == "Declaración de concurso":
                estado = "concursal"

        # Domicilio
        dom_m = _DOMICILIO_RE.search(body)
        domicilio = _clean(dom_m.group(1))[:500] if dom_m else None

        # Objeto social
        obj_m = _OBJETO_RE.search(body)
        objeto_social = _clean(obj_m.group(1))[:1000] if obj_m else None

        # Capital
        cap_m = _CAPITAL_RE.search(body)
        capital_social = _parse_capital(cap_m.group(1)) if cap_m else None

        # Fecha de constitución (Comienzo de operaciones)
        com_m = _COMIENZO_OPS_RE.search(body)
        fecha_constitucion = _parse_date_short(com_m.group(1)) if com_m else None

        # Hoja RM y fecha de última actualización (Datos registrales)
        hoja_rm = None
        fecha_ultima_act = None
        hoja_m = _HOJA_RM_RE.search(body)
        if hoja_m:
            hoja_rm = _clean(hoja_m.group(1))[:64]
        fecha_m = _FECHA_REGISTRAL_RE.search(body)
        if fecha_m:
            fecha_ultima_act = _parse_date_short(fecha_m.group(1))

        entries.append({
            "slug": slugify(razon_social),
            "razon_social": razon_social,
            "provincia": provincia_code,
            "domicilio": domicilio,
            "objeto_social": objeto_social,
            "hoja_rm": hoja_rm,
            "capital_social": capital_social,
            "fecha_constitucion": fecha_constitucion,
            "fecha_ultima_act": fecha_ultima_act,
            "actos": actos if actos else None,
            "estado": estado,
            "raw_text": (m.group(0) + body).strip()[:2000],
        })

    return entries
