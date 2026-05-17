"""Filtro SQL + pre-ranking determinista para candidatos de subvención."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Subvencion

# Mapeo simplificado provincia INE → CCAA
_PROVINCIA_TO_CCAA: dict[str, str] = {
    "01": "PV", "02": "CM", "03": "VC", "04": "AN", "05": "CL", "06": "EX",
    "07": "IB", "08": "CT", "09": "CL", "10": "EX", "11": "AN", "12": "VC",
    "13": "CM", "14": "AN", "15": "GA", "16": "CM", "17": "CT", "18": "AN",
    "19": "CM", "20": "PV", "21": "AN", "22": "AR", "23": "AN", "24": "CL",
    "25": "CT", "26": "RI", "27": "GA", "28": "MD", "29": "AN", "30": "MC",
    "31": "NC", "32": "GA", "33": "AS", "34": "CL", "35": "CN", "36": "GA",
    "37": "CL", "38": "CN", "39": "CB", "40": "CL", "41": "AN", "42": "CL",
    "43": "CT", "44": "AR", "45": "CM", "46": "VC", "47": "CL", "48": "PV",
    "49": "CL", "50": "AR", "51": "CE", "52": "ML",
}


@dataclass(frozen=True)
class EmpresaProfile:
    cnae: str
    tamano: str  # micro|pequena|mediana|grande
    provincia: str  # código INE 2 dígitos
    finalidad: list[str] = field(default_factory=list)

    @property
    def ccaa(self) -> str | None:
        return _PROVINCIA_TO_CCAA.get(self.provincia)


@dataclass(frozen=True)
class Candidate:
    subvencion: Subvencion
    score: int  # 0-100


def _compute_score(sub: Subvencion, perfil: EmpresaProfile) -> int:
    """Score determinista 0-100 basado en:
    - CNAE exacto: +40 ; CNAE genérico (lista vacía): +20
    - Finalidad solapada (cualquiera): +30
    - Cercanía a fecha_fin: hasta +20 (más cerca = más score)
    - Tamaño elegible: +10
    """
    score = 0

    if perfil.cnae in (sub.cnae_elegible or []):
        score += 40
    elif not sub.cnae_elegible:
        score += 20

    if set(perfil.finalidad) & set(sub.finalidad or []):
        score += 30

    if sub.fecha_fin:
        days_to_end = (sub.fecha_fin - date.today()).days
        if days_to_end >= 0:
            # Más cerca = más urgente y normalmente mejor priorizarlo
            urgency = max(0, 20 - (days_to_end // 7))  # 20 si <1 semana, baja con el tiempo
            score += min(20, urgency)

    benef = sub.beneficiarios or {}
    if perfil.tamano in benef.get("tamanos", []):
        score += 10

    return min(100, max(0, score))


def find_candidates(session: Session, perfil: EmpresaProfile, limit: int = 30) -> list[Candidate]:
    """Filtra y pre-rankea las subvenciones más relevantes para `perfil`.

    Filtros SQL aplicados:
    - estado = 'abierta'
    - fecha_fin >= hoy (o NULL)
    - cnae_elegible contiene el CNAE del perfil O está vacío
    - finalidad solapa con la del perfil
    - ámbito 'estatal' o 'ue' o (ámbito autonómico y CCAA coincide)
    """
    stmt = select(Subvencion).where(Subvencion.estado == "abierta")

    today = date.today()
    stmt = stmt.where((Subvencion.fecha_fin.is_(None)) | (Subvencion.fecha_fin >= today))

    # CNAE: matching jerárquico CNAE-2009. Los records BDNS suelen guardar prefijos
    # (`['62']` significa "todo el sector 62: actividades de informática") y los códigos
    # van hasta 4 dígitos. Match si el cnae_elegible incluye CUALQUIER prefijo del cnae
    # del usuario (ej. usuario 6201 matchea registros con cnae_elegible=['6'], ['62'],
    # ['620'] o ['6201']). O si la lista está vacía (wildcard genérico).
    cnae_prefixes = [perfil.cnae[:n] for n in range(1, len(perfil.cnae) + 1)]
    stmt = stmt.where(
        (Subvencion.cnae_elegible.overlap(cnae_prefixes))
        | (func.cardinality(Subvencion.cnae_elegible) == 0)
    )

    # Finalidad: lenient — solapa con la del perfil, O record sin finalidad clasificada
    # (cardinality 0), O finalidad clasificada como ['otros'] (record genérico sin tema claro).
    # Los no-matches obtienen score bajo en _compute_score y caen al final del ranking.
    if perfil.finalidad:
        stmt = stmt.where(
            (Subvencion.finalidad.overlap(perfil.finalidad))
            | (func.cardinality(Subvencion.finalidad) == 0)
            | (Subvencion.finalidad.contains(["otros"]))
        )

    # Ámbito: estatal y UE siempre visibles. Local también (la mayoría son ayuntamientos sin
    # ccaa rellenada, el usuario decide si le interesa). Autonómica solo si CCAA coincide.
    ccaa = perfil.ccaa
    if ccaa:
        stmt = stmt.where(
            (Subvencion.ambito.in_(["estatal", "ue", "local"]))
            | (Subvencion.ccaa == ccaa)
        )
    else:
        stmt = stmt.where(Subvencion.ambito.in_(["estatal", "ue", "local"]))

    rows = session.execute(stmt.limit(500)).scalars().all()

    candidates = [Candidate(subvencion=sub, score=_compute_score(sub, perfil)) for sub in rows]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]
