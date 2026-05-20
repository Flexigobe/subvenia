"""HTMX endpoint para typeahead de códigos CNAE-2009.

Permite al usuario buscar el código por descripción ("ferretería", "asesoría
fiscal", "software") o por código numérico. Cubre el caso en que la empresa no
está en BORME — en lugar de pedirle al usuario que aprenda códigos CNAE en
ine.es, le sugerimos.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.lib.cnae_catalog import search as cnae_search

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()


_MIN_QUERY_LEN = 2
_MAX_RESULTS = 12


@router.get("/api/cnae/search", response_class=HTMLResponse)
def cnae_typeahead(
    request: Request,
    q: str = "",
    cnae: str = "",
) -> HTMLResponse:
    """HTMX autocomplete CNAE. Acepta `q=` o `cnae=` (nombre del input)."""
    query = (q or cnae or "").strip()
    if len(query) < _MIN_QUERY_LEN:
        return HTMLResponse("")
    results = cnae_search(query, limit=_MAX_RESULTS)
    return templates.TemplateResponse(
        request,
        "partials/cnae_options.html",
        {"results": results, "q": query},
    )
