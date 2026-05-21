"""Ruta pública para licitaciones públicas (TED EU).

Filtros:
  - q: búsqueda libre en título / descripción / organismo
  - provincia: código INE 2 dígitos
  - estado: vigentes (default), abierta, cerrada, todas
  - categoria: prefijo CPV (45 = construcción, 72 = software, etc.)
  - importe_min: importe_total mínimo
  - orden: recientes (default) / cierre_proximo / importe_desc / importe_asc
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Licitacion
from app.db.session import get_db

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()

_PAGE_SIZE = 20

# Categorías CPV principales (2 primeros dígitos del código)
_CATEGORIA_CHOICES = [
    ("", "Cualquier categoría"),
    ("33", "Equipos médicos y farmacéuticos"),
    ("45", "Construcción y obra civil"),
    ("48", "Software paquetizado"),
    ("50", "Mantenimiento y reparación"),
    ("60", "Transporte"),
    ("71", "Ingeniería y arquitectura"),
    ("72", "Servicios TI / software a medida"),
    ("73", "Investigación y desarrollo"),
    ("79", "Servicios empresariales"),
    ("80", "Educación y formación"),
    ("85", "Servicios sanitarios y sociales"),
    ("90", "Limpieza y medio ambiente"),
    ("92", "Servicios culturales y deportivos"),
]

_ESTADO_CHOICES = [
    ("vigentes", "Vigentes"),
    ("abierta", "Cerrarán pronto"),
]

_ORDEN_CHOICES = [
    ("recientes", "Más recientes"),
    ("cierre_proximo", "Cierra antes"),
    ("importe_desc", "Más importe"),
    ("importe_asc", "Menos importe"),
]

_VALID_ESTADO = {"vigentes", "abierta", "cerrada", "todas"}
_VALID_ORDEN = {"recientes", "cierre_proximo", "importe_desc", "importe_asc"}
_VALID_CATEGORIA = {c[0] for c in _CATEGORIA_CHOICES if c[0]}


@router.get("/licitaciones", response_class=HTMLResponse)
def browse_licitaciones(
    request: Request,
    q: str = "",
    provincia: str = "",
    estado: str = "vigentes",
    categoria: str = "",
    importe_min: int = 0,
    orden: str = "recientes",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if page < 1:
        page = 1

    if estado not in _VALID_ESTADO:
        estado = "vigentes"
    if orden not in _VALID_ORDEN:
        orden = "recientes"
    if categoria not in _VALID_CATEGORIA:
        categoria = ""
    if importe_min < 0:
        importe_min = 0

    stmt = select(Licitacion)

    if q:
        stmt = stmt.where(
            or_(
                Licitacion.titulo.ilike(f"%{q}%"),
                Licitacion.descripcion.ilike(f"%{q}%"),
                Licitacion.organismo.ilike(f"%{q}%"),
            )
        )

    if provincia and len(provincia) == 2 and provincia.isdigit():
        stmt = stmt.where(Licitacion.provincia == provincia)

    today = date.today()
    # Política zero cerradas: nunca aparecen, da igual qué pida el usuario.
    # Filtro base: solo vigentes (fecha_limite futura o NULL si no se sabe).
    stmt = stmt.where(
        or_(Licitacion.fecha_limite.is_(None), Licitacion.fecha_limite >= today)
    )
    if estado == "abierta":
        # Que cierren pronto (próximos 30 días)
        from datetime import timedelta
        stmt = stmt.where(
            Licitacion.fecha_limite >= today,
            Licitacion.fecha_limite <= today + timedelta(days=30),
        )

    # Categoría CPV: filtrar por prefijo (2 chars) en el array
    if categoria:
        # Equivalente a EXISTS sobre unnest(cpv_codes) LIKE 'XX%'
        from sqlalchemy import text
        stmt = stmt.where(
            text(f"EXISTS (SELECT 1 FROM unnest(cpv_codes) AS c WHERE c LIKE '{categoria}%')")
        )

    if importe_min > 0:
        stmt = stmt.where(Licitacion.importe_total >= importe_min)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count: int = db.execute(count_stmt).scalar_one()
    total_pages = max(1, (total_count + _PAGE_SIZE - 1) // _PAGE_SIZE)
    if page > total_pages:
        page = total_pages

    if orden == "cierre_proximo":
        ordered = stmt.order_by(
            Licitacion.fecha_limite.asc().nullslast(),
            Licitacion.fecha_publicacion.desc().nullslast(),
        )
    elif orden == "importe_desc":
        ordered = stmt.order_by(
            Licitacion.importe_total.desc().nullslast(),
            Licitacion.fecha_publicacion.desc().nullslast(),
        )
    elif orden == "importe_asc":
        ordered = stmt.order_by(
            Licitacion.importe_total.asc().nullslast(),
            Licitacion.fecha_publicacion.desc().nullslast(),
        )
    else:
        ordered = stmt.order_by(
            Licitacion.fecha_publicacion.desc().nullslast(),
            Licitacion.created_at.desc(),
        )

    items = db.execute(
        ordered.offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE)
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "licitaciones.html",
        {
            "items": items,
            "q": q,
            "provincia": provincia,
            "estado": estado,
            "categoria": categoria,
            "importe_min": importe_min,
            "orden": orden,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "categoria_choices": _CATEGORIA_CHOICES,
            "estado_choices": _ESTADO_CHOICES,
            "orden_choices": _ORDEN_CHOICES,
        },
    )
