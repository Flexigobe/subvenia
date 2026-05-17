"""Ruta pública de exploración de todas las subvenciones."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Subvencion
from app.db.session import get_db

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()

_PAGE_SIZE = 20

_VALID_AMBITO = {"estatal", "autonomico", "local", "ue"}
_VALID_ESTADO = {"abierta", "cerrada", "proximamente"}

_AMBITO_CHOICES = [
    ("", "Todos los ámbitos"),
    ("estatal", "Estatal"),
    ("autonomico", "Autonómica"),
    ("local", "Local"),
    ("ue", "UE"),
]

_ESTADO_CHOICES = [
    ("abierta", "Abierta"),
    ("cerrada", "Cerrada"),
    ("proximamente", "Próximamente"),
]


@router.get("/subvenciones", response_class=HTMLResponse)
def browse(
    request: Request,
    q: str = "",
    ambito: str = "",
    estado: str = "abierta",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Clamp page to >= 1
    if page < 1:
        page = 1

    # Sanitise enum inputs
    if ambito not in _VALID_AMBITO:
        ambito = ""
    if estado not in _VALID_ESTADO:
        estado = "abierta"

    # Build base query
    stmt = select(Subvencion)

    if q:
        stmt = stmt.where(
            or_(
                Subvencion.titulo.ilike(f"%{q}%"),
                Subvencion.descripcion.ilike(f"%{q}%"),
            )
        )

    if ambito:
        stmt = stmt.where(Subvencion.ambito == ambito)

    if estado:
        stmt = stmt.where(Subvencion.estado == estado)

    # Count total for pagination
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count: int = db.execute(count_stmt).scalar_one()

    total_pages = max(1, (total_count + _PAGE_SIZE - 1) // _PAGE_SIZE)

    # Clamp page to valid range
    if page > total_pages:
        page = total_pages

    # Fetch current page
    ordered = stmt.order_by(Subvencion.created_at.desc())
    items = db.execute(
        ordered.offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "browse.html",
        {
            "items": items,
            "q": q,
            "ambito": ambito,
            "estado": estado,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "ambito_choices": _AMBITO_CHOICES,
            "estado_choices": _ESTADO_CHOICES,
        },
    )
