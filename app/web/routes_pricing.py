"""Ruta pública /comprar — planes de créditos para análisis IA exhaustivo."""

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


_PLANES: list[dict] = [
    {
        "id": "free",
        "nombre": "Gratis",
        "precio": "0€",
        "subtitulo": "Siempre",
        "features": [
            "10 búsquedas al día con IA Gemini",
            "Análisis exhaustivo de hasta 100 candidatos por búsqueda",
            "Acceso completo al catálogo BDNS + UE",
            "Alertas por email diarias",
            "Sin tarjeta",
        ],
        "cta": "Ya estás usándolo",
        "highlight": False,
    },
    {
        "id": "starter",
        "nombre": "Starter",
        "precio": "9€",
        "subtitulo": "al mes",
        "features": [
            "100 búsquedas al día con IA Gemini",
            "Análisis exhaustivo de los 300 candidatos completos",
            "Informes PDF descargables sin marca",
            "Alertas en tiempo real (instantáneas)",
            "Hasta 5 perfiles de empresa guardados",
            "Email de soporte",
        ],
        "cta": "Comprar Starter",
        "highlight": True,
    },
    {
        "id": "pro",
        "nombre": "Pro",
        "precio": "29€",
        "subtitulo": "al mes",
        "features": [
            "Búsquedas ilimitadas con IA",
            "Lectura íntegra del PDF de bases reguladoras del BOE",
            "Validación cruzada con análisis legal",
            "API REST para integración",
            "Perfiles ilimitados",
            "Soporte por WhatsApp + email",
            "Descarga masiva de resultados (CSV/Excel)",
        ],
        "cta": "Comprar Pro",
        "highlight": False,
    },
    {
        "id": "enterprise",
        "nombre": "Enterprise",
        "precio": "Custom",
        "subtitulo": "según uso",
        "features": [
            "Todo lo de Pro",
            "Modelo Gemini 2.5 Pro para casos complejos",
            "Análisis personalizado por sector (farma, biotech, retail, etc.)",
            "Webhooks personalizados",
            "SLA 99.9% + uptime garantizado",
            "Account manager dedicado",
            "Onboarding en remoto",
        ],
        "cta": "Contactar ventas",
        "highlight": False,
    },
]


@router.get("/comprar", response_class=HTMLResponse)
def comprar(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "comprar.html", {"planes": _PLANES})
