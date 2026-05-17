"""Rutas web de búsqueda."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

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
