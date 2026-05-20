"""HTMX endpoint for empresa razón-social autocomplete.

Backed by the local `empresa` table populated from BORME (Plan 5)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Empresa
from app.db.session import get_db
from app.lib.cnae_inferer import infer_cnae, infer_cnae_or_default
from app.sync.borme_parser import slugify

# Mapa de keywords-de-provincia → código INE. Permite inferir provincia cuando
# Empresite/Wikidata no la trae, pero el domicilio o la razón social menciona
# una ciudad/provincia. Solo nombres distintivos para evitar falsos positivos.
_CITY_TO_PROVINCIA: dict[str, str] = {
    # Provincia / ciudad principal → código INE
    "ALAVA": "01", "VITORIA": "01", "ARABA": "01",
    "ALBACETE": "02",
    "ALICANTE": "03", "ALACANT": "03", "ELCHE": "03", "ELX": "03",
    "ALMERIA": "04",
    "AVILA": "05",
    "BADAJOZ": "06",
    "BALEARES": "07", "MALLORCA": "07", "PALMA": "07", "MENORCA": "07", "EIVISSA": "07", "IBIZA": "07",
    "BARCELONA": "08", "BCN": "08", "SABADELL": "08", "TERRASSA": "08", "BADALONA": "08",
    "BURGOS": "09",
    "CACERES": "10",
    "CADIZ": "11", "JEREZ": "11",
    "CASTELLON": "12", "CASTELLO": "12",
    "CIUDAD REAL": "13",
    "CORDOBA": "14",
    "CORUNA": "15", "A CORUNA": "15", "LA CORUNA": "15", "SANTIAGO DE COMPOSTELA": "15",
    "CUENCA": "16",
    "GIRONA": "17", "GERONA": "17",
    "GRANADA": "18",
    "GUADALAJARA": "19",
    "GUIPUZCOA": "20", "GIPUZKOA": "20", "DONOSTIA": "20", "SAN SEBASTIAN": "20",
    "HUELVA": "21",
    "HUESCA": "22",
    "JAEN": "23",
    "LEON": "24",
    "LLEIDA": "25", "LERIDA": "25",
    "LA RIOJA": "26", "LOGRONO": "26",
    "LUGO": "27",
    "MADRID": "28", "ALCALA DE HENARES": "28", "ALCOBENDAS": "28", "MOSTOLES": "28",
    "MALAGA": "29", "MARBELLA": "29", "FUENGIROLA": "29",
    "MURCIA": "30", "CARTAGENA": "30",
    "NAVARRA": "31", "PAMPLONA": "31", "NAFARROA": "31",
    "OURENSE": "32", "ORENSE": "32",
    "ASTURIAS": "33", "OVIEDO": "33", "GIJON": "33",
    "PALENCIA": "34",
    "LAS PALMAS": "35", "GRAN CANARIA": "35", "FUERTEVENTURA": "35", "LANZAROTE": "35",
    "PONTEVEDRA": "36", "VIGO": "36",
    "SALAMANCA": "37",
    "TENERIFE": "38", "SANTA CRUZ DE TENERIFE": "38",
    "CANTABRIA": "39", "SANTANDER": "39",
    "SEGOVIA": "40",
    "SEVILLA": "41",
    "SORIA": "42",
    "TARRAGONA": "43", "REUS": "43",
    "TERUEL": "44",
    "TOLEDO": "45",
    "VALENCIA": "46", "VALENCIA-CITY": "46", "GANDIA": "46",
    "VALLADOLID": "47",
    "VIZCAYA": "48", "BIZKAIA": "48", "BILBAO": "48",
    "ZAMORA": "49",
    "ZARAGOZA": "50",
    "CEUTA": "51",
    "MELILLA": "52",
}


def _extract_provincia_from_text(text: str | None) -> str | None:
    """Intenta extraer código de provincia INE desde un texto libre (domicilio
    o razón social). Busca keywords distintivas — devuelve la primera coincidencia.
    """
    if not text:
        return None
    norm = "".join(c for c in unicodedata.normalize("NFD", text.upper()) if unicodedata.category(c) != "Mn")
    for keyword, code in _CITY_TO_PROVINCIA.items():
        # Match palabra completa (evita "LEONESA" matchee LEON)
        if re.search(rf"\b{re.escape(keyword)}\b", norm):
            return code
    return None

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
from app.web._template_globals import inject_globals
inject_globals(templates)

router = APIRouter()


_MIN_QUERY_LEN = 2
_MAX_RESULTS = 50  # hasta 50 results — el dropdown tiene scroll y se carga rápido (<500ms)
# Cargamos N records crudos antes de deduplicar — el BORME guarda múltiples filas por
# empresa (una por cada anuncio), así que tras de-dup nos quedan menos resultados únicos.
_RAW_FETCH_LIMIT = 250


@dataclass
class EmpresaOption:
    """Vista enriquecida de Empresa para el autocomplete: incluye CNAE inferido."""
    razon_social: str
    provincia: str | None
    domicilio: str | None
    objeto_social: str | None
    cnae_sugerido: str | None
    cnae_label: str | None


_LEGAL_SUFFIXES = (
    " SOCIEDAD LIMITADA", " SOCIEDAD ANONIMA", " SOCIEDAD ANÓNIMA",
    " S.L.U.", " S.A.U.", " S.L.L.", " S.L.P.",
    " S.L.", " S.A.", " SLU", " SAU", " SLL", " SLP",
    " SL", " SA", " SC", " SCP", " SCA",
)


def _normalize_razon(r: str) -> str:
    """Normaliza razón social para detectar empresas duplicadas: quita sufijos legales
    típicos (SL, SA, etc.) y la 'S' final solitaria que BORME trunca."""
    s = (r or "").upper().strip()
    changed = True
    while changed:
        changed = False
        for sfx in _LEGAL_SUFFIXES:
            if s.endswith(sfx):
                s = s[: -len(sfx)].rstrip(",. ").strip()
                changed = True
                break
    if s.endswith(" S"):
        s = s[:-2].strip()
    return s


def _completeness(e: Empresa) -> int:
    """Score de "qué tan completos están los datos de esta empresa". Mayor = mejor.
    Provincia vale doble porque sin ella el form no puede auto-rellenar correctamente."""
    score = 0
    if e.provincia:
        score += 10  # provincia es lo más importante para el form
    if e.objeto_social:
        score += 5   # permite inferir CNAE
    if e.domicilio:
        score += 2
    if e.capital_social:
        score += 1
    if e.fecha_constitucion:
        score += 1
    return score


def _dedupe(rows: list[Empresa]) -> list[Empresa]:
    """Agrupa empresas por razón social normalizada y devuelve el mejor record de cada
    grupo. Reglas:
    - Si un grupo tiene records CON provincia y SIN provincia, descartar los sin provincia
      (típico: BORME tiene la empresa con provincia, Empresite la tiene sin provincia).
    - Dentro de los que tienen provincia, dedup por provincia y elegir el más completo.
    - Si el grupo entero no tiene provincia, elegir el más completo.
    - Mantener el orden de primera aparición.
    """
    groups: dict[str, list[Empresa]] = {}
    order: list[str] = []
    for r in rows:
        key = _normalize_razon(r.razon_social)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    result: list[Empresa] = []
    for key in order:
        group = groups[key]
        with_prov = [r for r in group if r.provincia]
        if with_prov:
            best_per_prov: dict[str, Empresa] = {}
            for r in with_prov:
                p = r.provincia
                if p not in best_per_prov or _completeness(r) > _completeness(best_per_prov[p]):
                    best_per_prov[p] = r
            result.extend(best_per_prov.values())
        else:
            best = max(group, key=_completeness)
            result.append(best)
    return result


def _enrich(e: Empresa) -> EmpresaOption:
    """Enriquece un Empresa con CNAE inferido. Estrategia en cascada:
    1. Texto del objeto social (fuente más rica cuando existe)
    2. Razón social (muchas empresas llevan su actividad en el nombre, ej. "Tech",
       "Consulting", "Software Lab", "Integrated Circuits")
    """
    cnae_info = infer_cnae(e.objeto_social) or infer_cnae(e.razon_social)
    return EmpresaOption(
        razon_social=e.razon_social,
        provincia=e.provincia,
        domicilio=e.domicilio,
        objeto_social=e.objeto_social,
        cnae_sugerido=cnae_info[0] if cnae_info else None,
        cnae_label=cnae_info[1] if cnae_info else None,
    )


@router.get("/api/empresa/search", response_class=HTMLResponse)
def empresa_search(
    request: Request,
    q: str = "",
    razon_social: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """HTMX autocomplete. Devuelve hasta 10 empresas enriquecidas con CNAE sugerido.

    Estrategia de match en dos pasos:
    1. Prefijo exacto sobre el slug completo (rápido y preciso).
    2. Si no hay resultados y la query tiene varias palabras, búsqueda tokenizada:
       el slug debe contener TODAS las palabras (en cualquier orden). Esto permite
       encontrar empresas aunque el usuario escriba el nombre en otro orden o con
       palabras extra.

    Acepta tanto `q=` como `razon_social=` para que el form pueda hacer
    `hx-include="this"` sin renombrar el campo.
    """
    q = (q or razon_social or "").strip()
    if len(q) < _MIN_QUERY_LEN:
        return HTMLResponse("")
    slug_q = slugify(q)
    if not slug_q:
        return HTMLResponse("")

    # ── Estrategia mixta: prefix + infix combinados, hasta 50 results ──
    # Dropdown con scroll vertical para que el user pueda ver TODAS las
    # empresas que matchean su búsqueda hasta el límite.
    # Paso 1: prefix matches (más relevantes — empiezan por la query)
    prefix_rows = (
        db.execute(
            select(Empresa)
            .where(Empresa.slug.like(f"{slug_q}%"))
            .order_by(Empresa.razon_social.asc())
            .limit(_RAW_FETCH_LIMIT)
        )
        .scalars()
        .all()
    )

    # Paso 2: infix matches (contienen la query en cualquier posición)
    infix_rows: list[Empresa] = []
    if len(slug_q) >= 3:
        infix_stmt = (
            select(Empresa)
            .where(Empresa.slug.op("LIKE")(f"%{slug_q}%"))
            .where(~Empresa.slug.like(f"{slug_q}%"))
            .limit(_RAW_FETCH_LIMIT)
        )
        infix_rows = list(db.execute(infix_stmt).scalars().all())

    rows = list(prefix_rows) + infix_rows
    existing_ids = {e.id for e in rows}

    # Paso 3: fallback tokenizado multi-palabra si nada
    if not rows:
        tokens = [t for t in slug_q.split() if len(t) >= 2]
        if len(tokens) >= 2:
            stmt = select(Empresa)
            for t in tokens:
                stmt = stmt.where(Empresa.slug.like(f"%{t}%"))
            stmt = stmt.order_by(Empresa.razon_social.asc()).limit(_RAW_FETCH_LIMIT)
            rows = db.execute(stmt).scalars().all()

    deduped = _dedupe(list(rows))
    deduped.sort(key=lambda e: (-_completeness(e), e.razon_social))
    deduped = deduped[:_MAX_RESULTS]

    # ── Cross-source enrichment ──
    # Para cada empresa sin provincia (típicamente de Empresite), buscar una
    # hermana en BORME/Wikidata con el mismo nombre normalizado que SÍ tenga
    # provincia/objeto/domicilio. Una sola query agregada.
    incomplete_keys: list[str] = []
    for e in deduped:
        if not e.provincia:
            k = slugify(_normalize_razon(e.razon_social))
            if k and k not in incomplete_keys:
                incomplete_keys.append(k)

    sister_attrs: dict[str, dict[str, str]] = {}
    if incomplete_keys:
        from sqlalchemy import or_
        sister_rows = (
            db.execute(
                select(Empresa)
                .where(Empresa.provincia.isnot(None))
                .where(or_(*[Empresa.slug.like(f"{k}%") for k in incomplete_keys[:20]]))
                .limit(200)
            )
            .scalars()
            .all()
        )
        for s in sister_rows:
            key = slugify(_normalize_razon(s.razon_social))
            attrs = {
                "provincia": s.provincia,
                "objeto_social": s.objeto_social,
                "domicilio": s.domicilio,
            }
            current = sister_attrs.get(key)
            if not current or len([v for v in attrs.values() if v]) > len([v for v in current.values() if v]):
                sister_attrs[key] = attrs

    # Construye opciones — siempre con provincia + CNAE rellenos cuando sea posible.
    # Cadena de fallbacks:
    #   provincia: e.provincia → sister.provincia → extraer del domicilio →
    #              extraer de razón social → None (queda vacío, user elige)
    #   cnae:      infer(objeto) → infer(razón) → fallback catálogo → "8299" default
    empresas: list[EmpresaOption] = []
    for e in deduped:
        # === Provincia ===
        prov = e.provincia
        if not prov:
            key = slugify(_normalize_razon(e.razon_social))
            sister = sister_attrs.get(key) or {}
            prov = sister.get("provincia")
            if not prov:
                prov = (
                    _extract_provincia_from_text(e.domicilio)
                    or _extract_provincia_from_text(sister.get("domicilio"))
                    or _extract_provincia_from_text(e.razon_social)
                )

        # === Objeto social y domicilio (heredados si existen) ===
        key = slugify(_normalize_razon(e.razon_social))
        sister = sister_attrs.get(key) or {}
        obj = e.objeto_social or sister.get("objeto_social")
        dom = e.domicilio or sister.get("domicilio")

        # === CNAE — honesto: vacío si no se detecta con confianza ===
        # Orden:
        #   1. e.cnae_inferido (cached por bulk job — con patrones ampliados)
        #   2. infer_cnae(objeto_social) live
        #   3. infer_cnae(razón_social) live
        #   4. Si nada → "" (vacío) para que la UI pida al usuario que lo escriba
        # El antiguo fallback 8299 ("Otros servicios") engañaba: el matcher con
        # 8299 acepta cualquier subvención y los resultados son aleatorios.
        if e.cnae_inferido and e.cnae_inferido != "8299":
            cnae_sugerido = e.cnae_inferido
            cnae_label = e.cnae_inferido_label or ""
        else:
            cnae_info = infer_cnae(obj, allow_catalog_fallback=False) or \
                        infer_cnae(e.razon_social, allow_catalog_fallback=False)
            if cnae_info:
                cnae_sugerido, cnae_label = cnae_info
            else:
                # SIN datos suficientes — la UI mostrará warning y dejará vacío
                cnae_sugerido = ""
                cnae_label = ""

        empresas.append(EmpresaOption(
            razon_social=e.razon_social,
            provincia=prov,
            domicilio=dom,
            objeto_social=obj,
            cnae_sugerido=cnae_sugerido,
            cnae_label=cnae_label,
        ))

    return templates.TemplateResponse(
        request,
        "partials/empresa_options.html",
        {"empresas": empresas, "q": q},
    )
