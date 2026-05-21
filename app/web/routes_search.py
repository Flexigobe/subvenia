"""Rutas web de búsqueda."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.models import Search, SearchResult, Subvencion
from app.db.session import get_db
from app.lib.nif_validator import validate_nif
from app.matching.filter import EmpresaProfile
from app.matching.service import rank_for

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()


# Datos estáticos para el formulario
_PROVINCIAS: list[tuple[str, str]] = [
    ("01", "Álava"), ("02", "Albacete"), ("03", "Alicante"), ("04", "Almería"),
    ("05", "Ávila"), ("06", "Badajoz"), ("07", "Baleares"), ("08", "Barcelona"),
    ("09", "Burgos"), ("10", "Cáceres"), ("11", "Cádiz"), ("12", "Castellón"),
    ("13", "Ciudad Real"), ("14", "Córdoba"), ("15", "A Coruña"), ("16", "Cuenca"),
    ("17", "Girona"), ("18", "Granada"), ("19", "Guadalajara"), ("20", "Guipúzcoa"),
    ("21", "Huelva"), ("22", "Huesca"), ("23", "Jaén"), ("24", "León"),
    ("25", "Lleida"), ("26", "La Rioja"), ("27", "Lugo"), ("28", "Madrid"),
    ("29", "Málaga"), ("30", "Murcia"), ("31", "Navarra"), ("32", "Ourense"),
    ("33", "Asturias"), ("34", "Palencia"), ("35", "Las Palmas"), ("36", "Pontevedra"),
    ("37", "Salamanca"), ("38", "S/C Tenerife"), ("39", "Cantabria"), ("40", "Segovia"),
    ("41", "Sevilla"), ("42", "Soria"), ("43", "Tarragona"), ("44", "Teruel"),
    ("45", "Toledo"), ("46", "Valencia"), ("47", "Valladolid"), ("48", "Vizcaya"),
    ("49", "Zamora"), ("50", "Zaragoza"), ("51", "Ceuta"), ("52", "Melilla"),
]

_FINALIDADES: list[dict[str, str]] = [
    {"value": "digitalizacion", "label": "Digitalización"},
    {"value": "i+d", "label": "I+D"},
    {"value": "contratacion", "label": "Contratación"},
    {"value": "eficiencia_energetica", "label": "Eficiencia energética"},
    {"value": "internacionalizacion", "label": "Internacionalización"},
    {"value": "formacion", "label": "Formación"},
    {"value": "innovacion", "label": "Innovación"},
    {"value": "otros", "label": "Otros"},
]


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Home con stats live + últimas convocatorias publicadas (BDNS+EU)."""
    from datetime import date as _date

    from sqlalchemy import func as _func, text as _text

    from app.db.queries import is_open_filter as _is_open

    bdns_count = db.query(_func.count(Subvencion.id)).filter(
        Subvencion.source == "bdns",
        Subvencion.estado.in_(("abierta", "proximamente")),
        _is_open(),
    ).scalar() or 0
    eu_count = db.query(_func.count(Subvencion.id)).filter(
        Subvencion.source == "eu",
        Subvencion.estado.in_(("abierta", "proximamente")),
        _is_open(),
    ).scalar() or 0
    empresas_count = int(db.execute(
        _text("SELECT reltuples::bigint FROM pg_class WHERE relname = 'empresa'")
    ).scalar() or 0)
    total_count = bdns_count + eu_count

    # Últimas 8 subvenciones publicadas (mezcla BDNS+EU) — para banner home
    today = _date.today()
    latest_news = (
        db.query(Subvencion)
        .filter(_is_open())
        .order_by(Subvencion.created_at.desc())
        .limit(8)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "provincias": _PROVINCIAS,
            "finalidades": _FINALIDADES,
            "stats": {
                "bdns": bdns_count,
                "eu": eu_count,
                "total": total_count,
                "empresas": empresas_count,
                "cnaes": 236,
            },
            "latest_news": latest_news,
            "today": today,
            "today_iso": today.strftime("%Y.%m.%d"),
        },
    )


_TIPOS_SOLICITANTE_VALIDOS = {"empresa", "ong", "particular", "ayuntamiento", "investigacion"}


@router.post("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    razon_social: Annotated[str, Form()],
    cnae: Annotated[str, Form()],
    provincia: Annotated[str, Form()],
    tamano: Annotated[str, Form()] = "",
    nif: Annotated[str, Form()] = "",
    finalidad: Annotated[list[str], Form()] = [],
    tipo_solicitante: Annotated[str, Form()] = "empresa",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # razón social is required; nif and finalidad are optional.
    # When finalidad is empty, the filter shows ALL applicable subvenciones (no topic filter).
    # The result page labels each subvencion with its inferred finalidad so the user can
    # narrow down visually.

    # Validate NIF only if provided
    nif_normalized = ""
    if nif.strip():
        nif_result = validate_nif(nif)
        if not nif_result.valid:
            raise HTTPException(status_code=400, detail=f"El NIF {nif} no es válido")
        nif_normalized = nif_result.normalized

    # Persistir la búsqueda como lead
    ip = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip.encode()).hexdigest() if ip else None

    # Default tamaño = "pequena" (PYME) cuando el form no lo envía: el formulario
    # editorial sólo pide razón social + CNAE + provincia, y el column tiene NOT NULL.
    tamano_normalized = tamano or "pequena"

    search_row = Search(
        id=uuid.uuid4(),
        nif=nif_normalized,
        razon_social=razon_social,
        cnae=cnae,
        tamano=tamano_normalized,
        provincia=provincia,
        finalidad=finalidad,
        ip_hash=ip_hash,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(search_row)
    db.flush()

    # Sanitizar tipo_solicitante
    tipo_solic = tipo_solicitante if tipo_solicitante in _TIPOS_SOLICITANTE_VALIDOS else "empresa"

    # Matching — el perfil incluye tipo_solicitante para filtrar tiposBeneficiarios
    perfil = EmpresaProfile(
        cnae=cnae,
        tamano=tamano_normalized,
        provincia=provincia,
        finalidad=finalidad,
        tipo_solicitante=tipo_solic,
    )

    # Si el matching peta o tarda demasiado, NO devolvemos 500. Caemos al
    # filter determinista (sin LLM). Política conservadora: SOLO marcamos como
    # aplicable las que tienen el CNAE del usuario explícitamente en cnae_elegible
    # (alta confianza). El resto va a "descartadas" para evitar falsos positivos.
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        # limit=20: balance entre cobertura y tiempo de respuesta. 30 candidatos
        # tardaban 60-80s en Render Starter, demasiado cerca del HTTP timeout (60s).
        # Con 20 candidatos × 6 por batch = 4 batches paralelos a Gemini → ~25-40s total.
        ranked = await rank_for(db, perfil, limit=20)
    except Exception as exc:
        _log.exception("rank_for() failed in /search, falling back to filter-only: %s", exc)
        from app.matching.filter import find_candidates as _find, cnae_match_variants as _cnae_var
        from app.matching.service import RankedResult as _Ranked
        candidates = _find(db, perfil, limit=20)
        user_cnae_variants = set(_cnae_var(perfil.cnae))
        ranked = []
        for idx, c in enumerate(candidates):
            sub = c.subvencion
            sub_cnaes = set(sub.cnae_elegible or [])
            # Aplicable solo si CNAE del usuario está EXPLÍCITAMENTE listado.
            # cnae_elegible vacío → no aplicable en fallback (no podemos confirmar).
            has_explicit_match = bool(sub_cnaes & user_cnae_variants)
            ranked.append(_Ranked(
                subvencion=sub,
                score=c.score if has_explicit_match else max(0, c.score - 40),
                razon=None,
                rank=idx,
                applicable=has_explicit_match,
                match_reasons=(
                    "CNAE compatible (matching IA temporalmente no disponible — verificar requisitos)",
                ) if has_explicit_match else (),
                exclusion_reasons=(
                    "Matching IA no disponible y CNAE no listado explícitamente — verificar manualmente",
                ) if not has_explicit_match else (),
                urgency_days=-1,
            ))

    # Persistir search_results
    for r in ranked:
        db.add(SearchResult(
            search_id=search_row.id,
            subvencion_id=r.subvencion.id,
            score=r.score,
            razon=r.razon,
            rank=r.rank,
        ))
    db.commit()

    # Separa: aplicables vs no aplicables (transparencia: el usuario ve POR QUÉ no le tocan)
    applicables = [r for r in ranked if r.applicable]
    no_aplicables = [r for r in ranked if not r.applicable]
    top3 = applicables[:3]
    rest = applicables[3:]

    perfil_json = json.dumps({
        "cnae": cnae,
        "tamano": tamano,
        "provincia": provincia,
        "finalidad": finalidad,
    })

    today = _date.today()
    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "nif": nif_normalized,
            "razon_social": razon_social,
            "top3": top3,
            "rest": rest,
            "no_aplicables": no_aplicables,
            "total": len(ranked),
            "total_aplicables": len(applicables),
            "perfil_json": perfil_json,
            "today_iso": today.strftime("%Y.%m.%d"),
        },
    )


@router.get("/subsidy/{subsidy_id}", response_class=HTMLResponse)
def subsidy_detail(
    request: Request,
    subsidy_id: UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from app.db.models import Subvencion

    sub = db.get(Subvencion, subsidy_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subvención no encontrada")

    # Política cero convocatorias caducadas: 404 si está claramente cerrada.
    # Casos cerrada:
    # - estado='cerrada' (marcado explícitamente por sync o limpieza manual)
    # - fecha_fin pasada
    # - fecha_fin=NULL y fecha_inicio >1 año (huérfana antigua)
    today = _date.today()
    one_year_ago = today - timedelta(days=365)
    is_closed = (
        sub.estado == "cerrada"
        or (sub.fecha_fin is not None and sub.fecha_fin < today)
        or (sub.fecha_fin is None and sub.fecha_inicio is not None and sub.fecha_inicio < one_year_ago)
    )
    if is_closed:
        raise HTTPException(
            status_code=404,
            detail="Convocatoria cerrada. Solo mostramos convocatorias abiertas.",
        )

    return templates.TemplateResponse(
        request, "subsidy_detail.html",
        {"sub": sub, "today": today},
    )
