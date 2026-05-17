"""Ruta pública de fuentes y novedades de subvenciones."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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


@router.get("/noticias", response_class=HTMLResponse)
def news(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "news.html", {"groups": _GROUPS})
