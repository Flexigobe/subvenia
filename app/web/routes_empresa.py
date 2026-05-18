"""HTMX endpoint for empresa razón-social autocomplete.

Backed by the local `empresa` table populated from BORME (Plan 5)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Empresa
from app.db.session import get_db
from app.sync.borme_parser import slugify

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


_MIN_QUERY_LEN = 2
_MAX_RESULTS = 10


@router.get("/api/empresa/search", response_class=HTMLResponse)
def empresa_search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """HTMX autocomplete. Returns up to 10 matching empresas as HTML partial."""
    q = q.strip()
    if len(q) < _MIN_QUERY_LEN:
        return HTMLResponse("")
    slug_q = slugify(q)
    if not slug_q:
        return HTMLResponse("")

    rows = (
        db.execute(
            select(Empresa)
            .where(Empresa.slug.like(f"{slug_q}%"))
            .order_by(Empresa.razon_social.asc())
            .limit(_MAX_RESULTS)
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "partials/empresa_options.html",
        {"empresas": rows, "q": q},
    )
