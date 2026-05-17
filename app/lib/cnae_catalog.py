"""Catálogo CNAE-2009 cargado desde JSON estático con búsqueda fuzzy ligera."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "cnae_2009.json"


@dataclass(frozen=True)
class CnaeEntry:
    code: str
    description: str


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _normalize(s: str) -> str:
    return _strip_accents(s).lower().strip()


@lru_cache(maxsize=1)
def _load_catalog() -> list[CnaeEntry]:
    with open(DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return [CnaeEntry(code=r["code"], description=r["description"]) for r in raw]


@lru_cache(maxsize=1)
def _index_by_code() -> dict[str, CnaeEntry]:
    return {entry.code: entry for entry in _load_catalog()}


def get_by_code(code: str) -> CnaeEntry | None:
    return _index_by_code().get(code.strip())


def search(query: str, limit: int = 10) -> list[CnaeEntry]:
    q = _normalize(query)
    if not q:
        return []
    catalog = _load_catalog()

    # Match: prefijo de código > coincidencia en descripción
    prefix_matches = [e for e in catalog if e.code.startswith(q)]
    desc_matches = [e for e in catalog if q in _normalize(e.description) and not e.code.startswith(q)]
    return (prefix_matches + desc_matches)[:limit]
