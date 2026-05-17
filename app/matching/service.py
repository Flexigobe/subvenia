"""Servicio de matching: orquesta filter + LLM scorer."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Subvencion
from app.matching.filter import Candidate, EmpresaProfile, find_candidates
from app.matching.scorer_llm import score_batch


@dataclass(frozen=True)
class RankedResult:
    subvencion: Subvencion
    score: int
    razon: str | None
    rank: int


async def rank_for(
    session: Session, perfil: EmpresaProfile, limit: int = 30
) -> list[RankedResult]:
    candidates: list[Candidate] = find_candidates(session, perfil, limit=limit)
    llm_scores = await score_batch(perfil, candidates)
    # Replace deterministic score with LLM score, keep candidate's subvencion
    rescored = [
        RankedResult(subvencion=c.subvencion, score=s, razon=r, rank=0)
        for c, (s, r) in zip(candidates, llm_scores)
    ]
    rescored.sort(key=lambda x: x.score, reverse=True)
    return [
        RankedResult(subvencion=x.subvencion, score=x.score, razon=x.razon, rank=i + 1)
        for i, x in enumerate(rescored)
    ]
