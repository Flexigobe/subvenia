"""Ruta pública de fuentes y novedades de subvenciones."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.models import Subvencion
from app.db.session import get_db

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()

_GROUPS: list[dict] = [
    {
        "title": "España — Estatal",
        "description": "Fuentes oficiales del Estado español.",
        "sources": [
            {"name": "BDNS — Base de Datos Nacional de Subvenciones", "url": "https://www.infosubvenciones.es/bdnstrans/", "desc": "Buscador oficial de todas las convocatorias publicadas por administraciones españolas."},
            {"name": "BOE — Boletín Oficial del Estado", "url": "https://www.boe.es/diario_boe/", "desc": "Publicación diaria oficial; las nuevas convocatorias estatales aparecen aquí antes que en ningún otro sitio."},
            {"name": "Red.es", "url": "https://www.red.es/es/ayudas", "desc": "Kit Digital y otros programas de digitalización para PYMES y autónomos."},
            {"name": "CDTI — Centro para el Desarrollo Tecnológico Industrial", "url": "https://www.cdti.es", "desc": "Convocatorias de I+D+i: Neotec, Cervera, proyectos PID, etc."},
            {"name": "ENISA", "url": "https://www.enisa.es", "desc": "Préstamos participativos para PYMES, emprendedores y empresas innovadoras."},
            {"name": "ICEX — España Exportación e Inversiones", "url": "https://www.icex.es", "desc": "Ayudas a la internacionalización de empresas españolas."},
            {"name": "SEPE — Servicio Público de Empleo Estatal", "url": "https://www.sepe.es/HomeSepe/empresas/empresas-incentivos.html", "desc": "Bonificaciones y ayudas a la contratación."},
            {"name": "IDAE — Instituto para la Diversificación y Ahorro de la Energía", "url": "https://www.idae.es/ayudas-y-financiacion", "desc": "Eficiencia energética, energías renovables, autoconsumo."},
        ],
    },
    {
        "title": "España — Autonómicas",
        "description": "Cada comunidad publica sus convocatorias en su diario oficial. Buscar en BDNS suele ser más cómodo, pero las novedades aparecen antes en estos boletines.",
        "sources": [
            {"name": "DOGC — Diari Oficial de la Generalitat de Catalunya", "url": "https://dogc.gencat.cat/", "desc": "Convocatorias de la Generalitat de Catalunya."},
            {"name": "BOCM — Boletín Oficial de la Comunidad de Madrid", "url": "https://www.bocm.es/", "desc": "Convocatorias de la Comunidad de Madrid."},
            {"name": "DOG — Diario Oficial de Galicia", "url": "https://www.xunta.gal/diario-oficial-galicia", "desc": "Convocatorias de la Xunta de Galicia."},
            {"name": "BOJA — Boletín Oficial de la Junta de Andalucía", "url": "https://www.juntadeandalucia.es/eboja", "desc": "Convocatorias de la Junta de Andalucía."},
            {"name": "DOCV — Diari Oficial de la Generalitat Valenciana", "url": "https://dogv.gva.es/", "desc": "Convocatorias de la Generalitat Valenciana."},
            {"name": "BOPV — Boletín Oficial del País Vasco", "url": "https://www.euskadi.eus/y22-bopv/es/bopv2/datos/Ultimo.shtml", "desc": "Convocatorias del Gobierno Vasco."},
            {"name": "Mapa de boletines autonómicos (BOE)", "url": "https://www.boe.es/legislacion/enlaces/boletines_autonomicos.php", "desc": "Listado oficial de TODOS los boletines autonómicos con enlaces directos."},
        ],
    },
    {
        "title": "Unión Europea",
        "description": "Programas europeos a los que España puede optar como Estado miembro.",
        "sources": [
            {"name": "Funding & Tenders Portal (Comisión Europea)", "url": "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/home", "desc": "Portal oficial de toda la financiación UE: Horizon Europe, Digital Europe, LIFE, Erasmus+, EU4Health, etc."},
            {"name": "Horizon Europe", "url": "https://research-and-innovation.ec.europa.eu/funding/funding-opportunities/funding-programmes-and-open-calls/horizon-europe_en", "desc": "Programa marco de I+D — el más grande de Europa, ~95 mil M€ 2021-2027."},
            {"name": "Digital Europe Programme", "url": "https://digital-strategy.ec.europa.eu/en/activities/digital-programme", "desc": "Digitalización (IA, ciberseguridad, supercomputación, competencias digitales)."},
            {"name": "EIC — European Innovation Council", "url": "https://eic.ec.europa.eu/index_en", "desc": "Subvenciones y blended finance para startups y scale-ups innovadoras (EIC Accelerator, Pathfinder)."},
            {"name": "EIT — European Institute of Innovation & Technology", "url": "https://eit.europa.eu/", "desc": "Comunidades de conocimiento sectoriales (EIT Health, EIT Food, EIT Digital, etc.) con convocatorias propias."},
            {"name": "Fondos Next Generation EU en España", "url": "https://planderecuperacion.gob.es/", "desc": "Plan de Recuperación, Transformación y Resiliencia — los famosos fondos PRTR."},
        ],
    },
]


# Cache en memoria del BOE — TTL 30 minutos
import time as _time
_boe_cache: dict = {"items": [], "ts": 0.0}
_BOE_TTL = 30 * 60  # 30 min


async def _get_boe_items(limit: int = 10) -> list:
    """Fetch últimos items del BOE con cache 30 min.

    El BOE solo publica 1 vez al día (~8h), así que 30 min de cache es seguro.
    La primera visita después del TTL paga la latencia (~3s), las siguientes
    sirven desde memoria (<1ms). Si BOE falla devolvemos cache aunque expirado.
    """
    now = _time.time()
    if _boe_cache["items"] and (now - _boe_cache["ts"]) < _BOE_TTL:
        return _boe_cache["items"][:limit]

    try:
        from app.sync.boe_puller import fetch_last_n_days
        items = await fetch_last_n_days(days=7)
        _boe_cache["items"] = items
        _boe_cache["ts"] = now
        return items[:limit]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("BOE fetch failed: %s", exc)
        # Si tenemos algo en cache aunque sea viejo, mejor eso que nada
        return _boe_cache["items"][:limit] if _boe_cache["items"] else []


@router.get("/noticias", response_class=HTMLResponse)
async def news(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Feed real de novedades: últimas subvenciones publicadas, próximas a cerrar
    y fuentes oficiales. La página se refresca automáticamente con cada sync diaria.
    """
    today = date.today()
    one_week = today + timedelta(days=7)
    one_month = today + timedelta(days=30)
    boe_items = await _get_boe_items(limit=15)

    # 1) Últimas 30 añadidas: ordenadas por created_at DESC, filtradas a las que
    #    aún están abiertas (fecha_fin >= hoy o null) Y publicadas en últimos 90 días
    # El filtro de created_at es CRÍTICO — sin él escanea toda la tabla (163k).
    from app.db.queries import is_open_filter as _is_open
    cutoff_90 = today - timedelta(days=90)
    latest = (
        db.query(Subvencion)
        .filter(
            Subvencion.created_at >= cutoff_90,
            _is_open(),
        )
        .order_by(Subvencion.created_at.desc())
        .limit(30)
        .all()
    )

    # 2) Cerrando esta semana (próximos 7 días, urgentes)
    closing_week = (
        db.query(Subvencion)
        .filter(
            Subvencion.fecha_fin.between(today, one_week)
        )
        .order_by(Subvencion.fecha_fin.asc())
        .limit(20)
        .all()
    )

    # 3) Cerrando este mes (8-30 días)
    closing_month = (
        db.query(Subvencion)
        .filter(
            Subvencion.fecha_fin > one_week,
            Subvencion.fecha_fin <= one_month,
        )
        .order_by(Subvencion.fecha_fin.asc())
        .limit(20)
        .all()
    )

    # 4) Próximas a abrir (fecha_inicio futura, próximos 30 días)
    next_open = (
        db.query(Subvencion)
        .filter(
            Subvencion.fecha_inicio > today,
            Subvencion.fecha_inicio <= one_month,
        )
        .order_by(Subvencion.fecha_inicio.asc())
        .limit(15)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "news.html",
        {
            "groups": _GROUPS,
            "latest": latest,
            "closing_week": closing_week,
            "closing_month": closing_month,
            "next_open": next_open,
            "boe_items": boe_items,
            "today": today,
        },
    )
