"""Servicio de matching: orquesta filter + (en futuro) LLM scorer."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Subvencion
from app.matching.filter import Candidate, EmpresaProfile, find_candidates


@dataclass(frozen=True)
class RankedResult:
    subvencion: Subvencion
    score: int
    razon: str | None  # Plan 2 lo llenará con el LLM
    rank: int


def rank_for(session: Session, perfil: EmpresaProfile, limit: int = 30) -> list[RankedResult]:
    candidates: list[Candidate] = find_candidates(session, perfil, limit=limit)
    return [
        RankedResult(subvencion=c.subvencion, score=c.score, razon=None, rank=i + 1)
        for i, c in enumerate(candidates)
    ]
