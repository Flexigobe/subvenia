"""Ruta pública /comprar — manifiesto 'Por qué es gratis'.

Mantenemos la URL /comprar (con backlinks SEO ya indexados) pero la página
ya no muestra planes de pago. Radar Ayudas es gratis y sin pago.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()


@router.get("/comprar", response_class=HTMLResponse)
def comprar(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "comprar.html", {})
