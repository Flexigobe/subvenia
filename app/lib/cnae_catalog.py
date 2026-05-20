"""Catálogo CNAE-2009 cargado desde JSON estático con búsqueda fuzzy ligera.

Cada entrada tiene description oficial + keywords adicionales (sinónimos populares,
variantes ortográficas, palabras-clave que la gente usa). El campo `keywords` es
opcional para retrocompatibilidad — si está vacío, solo se busca en description.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "cnae_2009.json"


@dataclass(frozen=True)
class CnaeEntry:
    code: str
    description: str
    keywords: str = ""


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(s).lower()).strip()


@lru_cache(maxsize=1)
def _load_catalog() -> list[CnaeEntry]:
    with open(DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return [
        CnaeEntry(
            code=r["code"],
            description=r["description"],
            keywords=r.get("keywords", ""),
        )
        for r in raw
    ]


@lru_cache(maxsize=1)
def _index_by_code() -> dict[str, CnaeEntry]:
    return {entry.code: entry for entry in _load_catalog()}


@lru_cache(maxsize=1)
def _index_normalized() -> list[tuple[CnaeEntry, str]]:
    """Pre-computa el texto normalizado de cada entrada (description + keywords)."""
    return [
        (e, _normalize(e.description + " " + (e.keywords or "")))
        for e in _load_catalog()
    ]


def get_by_code(code: str) -> CnaeEntry | None:
    return _index_by_code().get(code.strip())


def search(query: str, limit: int = 10) -> list[CnaeEntry]:
    """Devuelve hasta `limit` entradas CNAE que matchean la query (código o texto)."""
    q = _normalize(query)
    if not q:
        return []

    # Búsqueda numérica: prefijo de código
    if query.strip().isdigit():
        digits = query.strip()
        prefix = [e for e in _load_catalog() if e.code.startswith(digits)]
        return prefix[:limit]

    # Búsqueda textual: scoring sobre description + keywords
    scored: list[tuple[int, CnaeEntry]] = []
    for entry, haystack in _index_normalized():
        words = haystack.split()
        if q in words:
            score = 200
        elif any(w.startswith(q) for w in words):
            score = 150
        elif q in haystack:
            score = 100
        else:
            continue
        desc_norm = _normalize(entry.description)
        if q in desc_norm:
            score += 20
        first_word = desc_norm.split()[0] if desc_norm else ""
        if q == first_word:
            score += 50
        scored.append((score, entry))

    scored.sort(key=lambda x: (-x[0], x[1].code))
    return [e for _, e in scored[:limit]]
