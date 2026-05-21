"""Ruta pública de exploración del catálogo de subvenciones.

Filtros disponibles:
  - q: búsqueda libre en título/descripción/organismo
  - ambito: estatal/autonomico/local/ue
  - estado: vigentes (default, abierta+proximamente con fecha_fin futura) / abierta / cerrada / todas
  - finalidad: token del vocabulario (cultura, social, formacion, i+d, etc.)
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

from app.db.models import Subvencion
from app.db.session import get_db

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()

_PAGE_SIZE = 20

_VALID_AMBITO = {"estatal", "autonomico", "local", "ue"}
# vigentes = abierta + proximamente con fecha_fin futura (uso real)
# abierta = solo estado='abierta'
# cerrada = solo estado='cerrada'
# todas = sin filtro
_VALID_ESTADO = {"vigentes", "abierta", "cerrada", "todas"}

_VALID_FINALIDAD = {
    "social", "cultura", "formacion", "comercio", "turismo", "agricultura",
    "innovacion", "i+d", "internacionalizacion", "eficiencia_energetica",
    "contratacion", "medio_ambiente", "digitalizacion", "otros",
}

_VALID_ORDEN = {"recientes", "cierre_proximo", "importe_desc", "importe_asc"}

_AMBITO_CHOICES = [
    ("", "Todos los ámbitos"),
    ("estatal", "Estatal"),
    ("autonomico", "Autonómica"),
    ("local", "Local"),
    ("ue", "Unión Europea"),
]

_ESTADO_CHOICES = [
    ("vigentes", "Vigentes (abiertas o por abrir)"),
    ("abierta", "Solo abiertas"),
    ("cerrada", "Cerradas"),
    ("todas", "Todas"),
]

_FINALIDAD_CHOICES = [
    ("", "Cualquier finalidad"),
    ("social", "Social y sanitario"),
    ("cultura", "Cultura"),
    ("formacion", "Formación y empleo"),
    ("comercio", "Comercio"),
    ("turismo", "Turismo"),
    ("agricultura", "Agricultura, pesca, alimentación"),
    ("innovacion", "Innovación"),
    ("i+d", "I+D"),
    ("internacionalizacion", "Internacionalización"),
    ("eficiencia_energetica", "Eficiencia energética / industria"),
    ("contratacion", "Empleo / contratación"),
    ("digitalizacion", "Digitalización"),
    ("medio_ambiente", "Medio ambiente"),
    ("otros", "Otras"),
]

_ORDEN_CHOICES = [
    ("recientes", "Más recientes"),
    ("cierre_proximo", "Cierra antes"),
    ("importe_desc", "Más importe"),
    ("importe_asc", "Menos importe"),
]


@router.get("/subvenciones", response_class=HTMLResponse)
def browse(
    request: Request,
    q: str = "",
    ambito: str = "",
    estado: str = "vigentes",
    finalidad: str = "",
    importe_min: int = 0,
    orden: str = "recientes",
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if page < 1:
        page = 1

    # Sanitise inputs
    if ambito not in _VALID_AMBITO:
        ambito = ""
    if estado not in _VALID_ESTADO:
        estado = "vigentes"
    if finalidad not in _VALID_FINALIDAD:
        finalidad = ""
    if orden not in _VALID_ORDEN:
        orden = "recientes"
    if importe_min < 0:
        importe_min = 0

    from app.db.queries import is_open_filter as _is_open

    stmt = select(Subvencion)

    # POLÍTICA ZERO CERRADAS: siempre filtramos las cerradas, da igual qué pida
    # el usuario. No mostramos nunca convocatorias cuya fecha ya pasó o están
    # claramente caducadas (fecha_fin NULL con fecha_inicio >1 año).
    stmt = stmt.where(_is_open())

    # Búsqueda libre en título, descripción y organismo
    if q:
        stmt = stmt.where(
            or_(
                Subvencion.titulo.ilike(f"%{q}%"),
                Subvencion.descripcion.ilike(f"%{q}%"),
                Subvencion.organismo.ilike(f"%{q}%"),
            )
        )

    if ambito:
        stmt = stmt.where(Subvencion.ambito == ambito)

    today = date.today()
    # Mantener compat: estado="abierta"/"vigentes" son lo mismo ahora; "cerrada"
    # se ignora porque las cerradas NO aparecen nunca por política.
    # "proximamente" sigue siendo válido (abierta no ha empezado pero está cerca).

    if finalidad:
        # Usar overlap con el array de finalidad de la subvención
        from sqlalchemy.dialects.postgresql import ARRAY
        from sqlalchemy import String, cast
        stmt = stmt.where(Subvencion.finalidad.any(finalidad))

    if importe_min > 0:
        stmt = stmt.where(Subvencion.importe_total >= importe_min)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_count: int = db.execute(count_stmt).scalar_one()

    total_pages = max(1, (total_count + _PAGE_SIZE - 1) // _PAGE_SIZE)
    if page > total_pages:
        page = total_pages

    # Ordenación
    if orden == "cierre_proximo":
        # NULLs al final, fecha_fin asc
        ordered = stmt.order_by(
            Subvencion.fecha_fin.asc().nullslast(),
            Subvencion.created_at.desc(),
        )
    elif orden == "importe_desc":
        ordered = stmt.order_by(
            Subvencion.importe_total.desc().nullslast(),
            Subvencion.created_at.desc(),
        )
    elif orden == "importe_asc":
        ordered = stmt.order_by(
            Subvencion.importe_total.asc().nullslast(),
            Subvencion.created_at.desc(),
        )
    else:  # recientes (default)
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
            "finalidad": finalidad,
            "importe_min": importe_min,
            "orden": orden,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "ambito_choices": _AMBITO_CHOICES,
            "estado_choices": _ESTADO_CHOICES,
            "finalidad_choices": _FINALIDAD_CHOICES,
            "orden_choices": _ORDEN_CHOICES,
        },
    )
