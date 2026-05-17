"""Rutas web de búsqueda."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from uuid import UUID
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.models import Search, SearchResult
from app.db.session import get_db
from app.lib.nif_validator import validate_nif
from app.matching.filter import EmpresaProfile
from app.matching.service import rank_for

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "home.html",
        {"provincias": _PROVINCIAS, "finalidades": _FINALIDADES},
    )


@router.post("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    nif: Annotated[str, Form()],
    cnae: Annotated[str, Form()],
    tamano: Annotated[str, Form()],
    provincia: Annotated[str, Form()],
    finalidad: Annotated[list[str], Form()],
    razon_social: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Validar NIF
    nif_result = validate_nif(nif)
    if not nif_result.valid:
        raise HTTPException(status_code=400, detail=f"El NIF {nif} no es válido")

    # Validar al menos una finalidad
    if not finalidad:
        raise HTTPException(status_code=422, detail="Selecciona al menos una finalidad")

    # Persistir la búsqueda como lead
    ip = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip.encode()).hexdigest() if ip else None

    search_row = Search(
        id=uuid.uuid4(),
        nif=nif_result.normalized,
        razon_social=razon_social,
        cnae=cnae,
        tamano=tamano,
        provincia=provincia,
        finalidad=finalidad,
        ip_hash=ip_hash,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(search_row)
    db.flush()

    # Matching
    perfil = EmpresaProfile(cnae=cnae, tamano=tamano, provincia=provincia, finalidad=finalidad)
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

    top3 = ranked[:3]
    rest = ranked[3:]

    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "nif": nif_result.normalized,
            "razon_social": razon_social,
            "top3": top3,
            "rest": rest,
            "total": len(ranked),
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
