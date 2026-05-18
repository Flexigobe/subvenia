"""Static legal pages: privacy policy + terms of use.

Spanish RGPD-compliant content for a public service that captures emails
for optional subscription to subvención alerts."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


@router.get("/privacidad", response_class=HTMLResponse)
def privacidad(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/privacidad.html", {})


@router.get("/terminos", response_class=HTMLResponse)
def terminos(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal/terminos.html", {})
