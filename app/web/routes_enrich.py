"""HTMX endpoint para auto-completar el formulario del NIF."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.enrich.service import enrich_nif
from app.lib.nif_validator import validate_nif

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()


@router.get("/api/enrich", response_class=HTMLResponse)
async def enrich_endpoint(request: Request, nif: str = "") -> HTMLResponse:
    """Devuelve un HTML partial para que HTMX rellene los campos del formulario.

    El partial usa hx-swap-oob para inyectar el value del input razon_social y
    también renderiza un pequeño status text bajo el NIF.
    """
    if not nif.strip():
        # No content: HTMX swap will replace target with empty html
        return HTMLResponse("")

    nif_result = validate_nif(nif)
    if not nif_result.valid:
        raise HTTPException(status_code=400, detail=f"El NIF {nif} no es válido")

    data = await enrich_nif(nif_result.normalized)
    return templates.TemplateResponse(
        request,
        "partials/enrich_result.html",
        {"data": data, "nif": nif_result.normalized},
    )
