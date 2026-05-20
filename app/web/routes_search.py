"""Rutas web de búsqueda."""

from __future__ import annotations

import hashlib
import json
import uuid
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

    bdns_count = db.query(_func.count(Subvencion.id)).filter(
        Subvencion.source == "bdns",
        Subvencion.estado.in_(("abierta", "proximamente")),
    ).scalar() or 0
    eu_count = db.query(_func.count(Subvencion.id)).filter(
        Subvencion.source == "eu",
        Subvencion.estado.in_(("abierta", "proximamente")),
    ).scalar() or 0
    empresas_count = int(db.execute(
        _text("SELECT reltuples::bigint FROM pg_class WHERE relname = 'empresa'")
    ).scalar() or 0)
    total_count = bdns_count + eu_count

    # Últimas 8 subvenciones publicadas (mezcla BDNS+EU) — para banner home
    today = _date.today()
    latest_news = (
        db.query(Subvencion)
        .filter((Subvencion.fecha_fin.is_(None)) | (Subvencion.fecha_fin >= today))
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
    ranked = await rank_for(db, perfil, limit=30)

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
    return templates.TemplateResponse(request, "subsidy_detail.html", {"sub": sub})
