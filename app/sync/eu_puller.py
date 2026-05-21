"""Cliente para el EU Funding & Tenders Portal — segunda fuente de subvenciones.

API: POST https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA
Respuesta: {"results": [...], "totalResults": N, "pageNumber": M, "pageSize": K}

Cada result tiene:
  - metadata.identifier[0]    — ID del topic (ej. "HORIZON-CL4-2025-01")
  - metadata.title[0]         — título del topic
  - metadata.callTitle[0]     — título de la convocatoria (call)
  - metadata.callIdentifier[0]— identificador de la convocatoria
  - metadata.deadlineDate[0]  — fecha límite ISO 8601 "2026-12-31T00:00:00.000+0000"
  - metadata.startDate[0]     — fecha apertura
  - metadata.status[0]        — "31094501" Forthcoming, "31094502" Open, "31094503" Closed
  - metadata.frameworkProgramme[0] — código del programa marco
  - metadata.typesOfAction    — lista de tipo de acción
  - summary (top-level)       — descripción breve
  - url (top-level)           — URL JSON del topic (no el portal HTML)

API behaviour notes (verified 2026-05):
  - Server-side sort/filter params are silently ignored (sort, sortField, filter, status, facets
    all return identical results). Client-side filtering is the only option.
  - With text="***" the API returns primarily historical/closed records (0-2 open per 50).
  - Using text="<current_year> <next_year>" yields ~24-50 open records per page by matching
    calls whose deadline dates include those year strings — far better hit rate.
  - Results are capped at ~4 950 items (99 pages × 50) regardless of totalResults (644k+).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import UTC, datetime
from datetime import date as date_t
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Subvencion

logger = logging.getLogger(__name__)

EU_API_URL = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EU_API_KEY = "SEDIA"

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "subvenciones-app/0.1",
}

# status code → estado
_STATUS_MAP: dict[str, str] = {
    "31094501": "proximamente",   # Forthcoming
    "31094502": "abierta",        # Open
    "31094503": "cerrada",        # Closed
}

_FINALIDAD_KEYWORDS: list[tuple[str, str]] = [
    ("digital", "digitalizacion"),
    ("research", "i+d"),
    ("innov", "innovacion"),
    ("employ", "contratacion"),
    ("energy", "eficiencia_energetica"),
    ("renew", "eficiencia_energetica"),
    ("internation", "internacionalizacion"),
    ("export", "internacionalizacion"),
    ("educati", "formacion"),
    ("train", "formacion"),
    ("climate", "medio_ambiente"),
    ("green", "medio_ambiente"),
    ("health", "social"),
    ("social", "social"),
]


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _infer_finalidad(text: str | None) -> list[str]:
    if not text:
        return []
    norm = _strip_accents(text).lower()
    matched: list[str] = []
    seen: set[str] = set()
    for kw, token in _FINALIDAD_KEYWORDS:
        if kw in norm and token not in seen:
            matched.append(token)
            seen.add(token)
    return matched or ["otros"]


def _parse_date(value: Any) -> date_t | None:
    """Parse ISO 8601 string or Unix ms timestamp to date.

    The EU API returns strings like "2026-12-31T00:00:00.000+0000".
    """
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=UTC).date()
        # Take first 10 chars: "2026-12-31"
        return date_t.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _first(lst: Any, default: Any = None) -> Any:
    """Return first element of a list, or default if empty/not a list."""
    if isinstance(lst, list):
        return lst[0] if lst else default
    return lst if lst is not None else default


# Pattern to strip HTML markup conservadoramente: tags vacíos y atributos
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_MULTINEWLINE_RE = re.compile(r"\n{3,}")


def _strip_html(html: str | None) -> str | None:
    """Convierte un fragmento HTML conservando saltos de párrafo legibles.

    El campo `descriptionByte` del portal F&T trae descripciones con <p>, <strong>,
    <h4>, <a href> y similares. Para texto plano legible reemplazamos los block-level
    tags por saltos de línea antes de eliminar el resto.
    """
    if not html:
        return None
    s = str(html)
    # Block-level tags → \n
    s = re.sub(r"</?(p|div|h[1-6]|br|li)[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(ul|ol)[^>]*>", "\n", s, flags=re.IGNORECASE)
    # Eliminar resto de tags
    s = _HTML_TAG_RE.sub("", s)
    # Entidades comunes
    s = (s.replace("&nbsp;", " ")
           .replace("&amp;", "&")
           .replace("&lt;", "<")
           .replace("&gt;", ">")
           .replace("&quot;", '"')
           .replace("&#39;", "'"))
    # Normalizar whitespace
    s = _WS_RE.sub(" ", s)
    s = _MULTINEWLINE_RE.sub("\n\n", s).strip()
    return s or None


def _parse_budget_overview(value: Any) -> dict[str, Any]:
    """Parsea el JSON-as-string `budgetOverview` y extrae cifras útiles.

    Estructura: `{"budgetYearsColumns":[...], "budgetTopicActionMap":{<id>:[{action, expectedGrants,
    minContribution, maxContribution, budgetYearMap:{year:amount}, ...}]}}`

    Devuelve: {
      "total":            suma de budgetYearMap.values() en EUR,
      "max_beneficiario": max(maxContribution) si > 0,
      "min_beneficiario": min(minContribution) si > 0,
      "expected_grants":  nº de grants esperados,
      "deadline_dates":   lista de deadlines,
      "years":            ["2026", "2027"],
    }
    """
    out: dict[str, Any] = {
        "total": 0.0,
        "max_beneficiario": None,
        "min_beneficiario": None,
        "expected_grants": 0,
        "deadline_dates": [],
        "years": [],
    }
    if not value:
        return out
    s = value if isinstance(value, str) else (_first(value) or "")
    try:
        data = json.loads(s) if isinstance(s, str) else s
    except (json.JSONDecodeError, TypeError):
        return out

    out["years"] = data.get("budgetYearsColumns") or []
    actions_map = data.get("budgetTopicActionMap") or {}
    max_b, min_b = None, None
    for action_list in actions_map.values():
        for a in action_list or []:
            for amt in (a.get("budgetYearMap") or {}).values():
                try:
                    out["total"] += float(amt)
                except (ValueError, TypeError):
                    pass
            try:
                eg = int(a.get("expectedGrants") or 0)
                out["expected_grants"] += eg
            except (ValueError, TypeError):
                pass
            mx = a.get("maxContribution")
            if mx and float(mx) > 0:
                max_b = max(max_b or 0.0, float(mx))
            mn = a.get("minContribution")
            if mn and float(mn) > 0:
                min_b = min(min_b or float("inf"), float(mn))
            for dl in a.get("deadlineDates") or []:
                if dl and dl not in out["deadline_dates"]:
                    out["deadline_dates"].append(dl)
    out["max_beneficiario"] = max_b
    out["min_beneficiario"] = min_b if min_b != float("inf") else None
    return out


def _parse_links(value: Any) -> list[dict[str, Any]]:
    """Parsea el JSON-as-string `links` (submission URLs y MGA descriptions)."""
    if not value:
        return []
    s = value if isinstance(value, str) else (_first(value) or "")
    try:
        return json.loads(s) if isinstance(s, str) else (s or [])
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_actions(value: Any) -> list[dict[str, Any]]:
    """Parsea el JSON-as-string `actions` (typesOfAction, submission procedure, deadlines)."""
    if not value:
        return []
    s = value if isinstance(value, str) else (_first(value) or "")
    try:
        return json.loads(s) if isinstance(s, str) else (s or [])
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_cft_documents(value: Any) -> list[dict[str, Any]]:
    """Parsea documentos de procurement (cftDocuments JSON-as-string)."""
    if not value:
        return []
    s = value if isinstance(value, str) else (_first(value) or "")
    try:
        data = json.loads(s) if isinstance(s, str) else s
    except (json.JSONDecodeError, TypeError):
        return []
    return (data or {}).get("cftDocuments") or []


async def fetch_page(page: int = 1, page_size: int = 50, text: str = "***") -> dict[str, Any]:
    """Hits the EU search endpoint and returns the raw JSON response.

    POST request with apiKey in query string; empty form body.
    Returns the response dict with keys: results, totalResults, pageNumber, pageSize.

    The `text` param accepts any keyword string. Using year strings like "2026 2027"
    increases the proportion of open/future calls returned (API ignores all sort/filter
    params; text search is the only effective client-side pre-filter available).
    """
    params = {
        "apiKey": EU_API_KEY,
        "text": text,
        "pageSize": page_size,
        "pageNumber": page,
        "languages": "es,en",
    }
    async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as client:
        r = await client.post(EU_API_URL, params=params, data={})
        r.raise_for_status()
        return r.json()


_ACCEPTED_LANGUAGES = {"es", "en"}


def parse_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map a raw EU API result to the Subvencion field dict.

    El portal F&T devuelve metadatos extremadamente ricos pero anidados como JSON-as-string
    en distintos campos. Esta función extrae todo lo aprovechable para que la página de
    detalle no quede vacía.

    Returns None si el record no es ni español ni inglés.
    """
    md = raw.get("metadata") or {}

    # Language filter
    lang = (_first(md.get("language")) or "").lower()
    if lang and lang not in _ACCEPTED_LANGUAGES:
        return None

    # Filtrar procurement (cftId presente): son tenders/contratos del eTendering portal,
    # NO subvenciones. El usuario busca "subvenciones que pueda pedir", no licitaciones.
    if md.get("cftId"):
        return None

    identifier = _first(md.get("identifier"), "")
    external_id = str(identifier).strip() if identifier else ""

    # Title (preferimos metadata.title; fallback a top-level summary / callTitle)
    title = _first(md.get("title")) or raw.get("summary") or _first(md.get("callTitle")) or ""
    if not isinstance(title, str):
        title = str(title)

    # Organismo: priorizamos nombres legibles. frameworkProgramme a veces es un
    # código numérico (ej. "111111") — lo descartamos si es solo dígitos.
    framework_raw = _first(md.get("frameworkProgramme")) or ""
    framework = framework_raw if (framework_raw and not str(framework_raw).strip().isdigit()) else None
    call_title = _first(md.get("callTitle"))
    ca_name_raw = _first(md.get("caName")) or ""
    ca_name = ca_name_raw if (ca_name_raw and not str(ca_name_raw).strip().isdigit()) else None
    # Detectar EuropeAid (cooperación al desarrollo) por el identifier
    ext_id = _first(md.get("identifier")) or ""
    is_europeaid = str(ext_id).startswith("EuropeAid/")

    # Patrones de call_title que claramente NO son organismos (son cohorts,
    # call IDs, cluster names sueltos, etc.). Si call_title matchea esto,
    # NO lo usamos como organismo.
    _NOT_ORGANISMO_PATTERNS = [
        r"^cohorte\s+del\s+ciclo",
        r"^call\s+\d+\s*-\s*(single|two)[\s-]?stage",
        r"^(batteries?|mobility|energy|digital|space|industry|health|culture)\b",
        r"^cluster\s+\d",
        r"^civil\s+security",
        r"^innovative\s+models?",
        r"^enhancing",
        r"^accelerating",
    ]

    def _is_real_organismo(s: str) -> bool:
        if not s:
            return False
        s_lower = s.lower().strip()
        for pat in _NOT_ORGANISMO_PATTERNS:
            if re.search(pat, s_lower):
                return False
        return True

    if framework and call_title and _is_real_organismo(framework):
        organismo = f"{framework} · {call_title}"
    elif is_europeaid:
        organismo = "EuropeAid (DG INTPA · Cooperación al Desarrollo)"
    elif framework and _is_real_organismo(framework):
        organismo = framework
    elif ca_name and not re.fullmatch(r"\d+", str(ca_name).strip()) and _is_real_organismo(str(ca_name)):
        organismo = str(ca_name)
    else:
        # Fallback robusto: nunca mostrar call_title genérico como organismo.
        organismo = "Comisión Europea (F&T Portal)"

    # Fechas
    deadline_raw = _first(md.get("deadlineDate"))
    start_raw = _first(md.get("startDate"))

    # Portal URL (HTML legible)
    portal_url = (
        f"https://ec.europa.eu/info/funding-tenders/opportunities/portal"
        f"/screen/opportunities/topic-details/{external_id}"
        if external_id else _first(md.get("url")) or raw.get("url")
    )

    # Estado
    status_code = _first(md.get("status"), "31094503")
    estado = _STATUS_MAP.get(str(status_code), "cerrada")

    # ── Descripción rica: descriptionByte (HTML largo) → texto plano ──
    description_html = _first(md.get("descriptionByte"))
    description = _strip_html(description_html) if description_html else None
    # Fallback: usar summary o description corta
    if not description:
        description = raw.get("summary") or _strip_html(_first(md.get("description")))

    # ── Presupuesto: parsea budgetOverview JSON ──
    budget = _parse_budget_overview(md.get("budgetOverview"))
    importe_total = budget["total"] if budget["total"] > 0 else None
    # Fallback: campo `budget` directo (presente en EuropeAid calls)
    if not importe_total:
        budget_direct = _first(md.get("budget"))
        try:
            if budget_direct:
                importe_total = float(str(budget_direct).replace(",", "").replace(".", ""))
                if importe_total <= 0 or importe_total > 1e12:
                    importe_total = None
        except (ValueError, TypeError):
            pass
    importe_max = budget["max_beneficiario"]

    # ── Tipos de acción / MGA (Model Grant Agreement) ──
    types_of_action = md.get("typesOfAction") or []
    actions = _parse_actions(md.get("actions"))
    submission_procedure = None
    if actions:
        sp = (actions[0].get("submissionProcedure") or {}) if isinstance(actions[0], dict) else {}
        submission_procedure = sp.get("description")

    # ── Links de submission ──
    links = _parse_links(md.get("links"))
    submission_url = None
    for link in links:
        if isinstance(link, dict) and link.get("url"):
            submission_url = link["url"]
            break

    # ── Documentos cft (procurement) ──
    documents = _parse_cft_documents(md.get("cftDocuments"))

    # ── Conditions HTML (eligibilidad) ──
    conditions_html = _first(md.get("topicConditions"))
    conditions = _strip_html(conditions_html) if conditions_html else None

    # ── Keywords / tags / focus area ──
    keywords = md.get("keywords") or []
    tags = md.get("tags") or []
    focus_area = md.get("focusArea") or []

    # Finalidad: combina título + types_of_action + tags para mejor inferencia
    text_for_finalidad = " ".join([
        title,
        " ".join(str(t) for t in types_of_action),
        " ".join(str(t) for t in tags[:10]),
    ])
    finalidad = _infer_finalidad(text_for_finalidad)

    # ── Beneficiarios estructurados ──
    beneficiarios: dict[str, Any] = {
        "tipos": [{"descripcion": "Consorcios, empresas, organizaciones de investigación"}],
        "tamanos": ["pequena", "mediana", "grande"],  # EU calls usualmente aceptan todos
    }
    if "SME" in (types_of_action and " ".join(types_of_action) or "").upper():
        beneficiarios["tamanos"] = ["pequena", "mediana"]
        beneficiarios["tipos"] = [{"descripcion": "PYMEs (SMEs) — pequeñas y medianas empresas"}]

    # ── Construir extra dict que mejorará el template ──
    extra: dict[str, Any] = {
        "callIdentifier": _first(md.get("callIdentifier")),
        "callTitle": call_title,
        "frameworkProgramme": framework,
        "programmePeriod": md.get("programmePeriod") or [],
        "typesOfAction": types_of_action,
        "submissionProcedure": submission_procedure,
        "submissionUrl": submission_url,
        "conditions": conditions,
        "conditions_html": conditions_html,
        "keywords": keywords[:20],
        "tags": tags[:20],
        "focusArea": focus_area,
        "expectedGrants": budget["expected_grants"],
        "budgetMin": budget["min_beneficiario"],
        "budgetYears": budget["years"],
        "deadlineDates": budget["deadline_dates"],
        "documents": [
            {
                "name": (d.get("documentTitle") or "Documento"),
                "type": d.get("documentType"),
                "publication_date": d.get("hermesDocumentReferences", [{}])[0].get("publicationDate")
                                    if d.get("hermesDocumentReferences") else None,
            }
            for d in documents if isinstance(d, dict)
        ],
        "sedeElectronica": submission_url,  # alias usado por el template
        "urlBasesReguladoras": portal_url,
    }

    # Combinar raw + extra para que el template tenga TODO disponible
    enriched_payload = dict(raw)
    enriched_payload["_extra"] = extra
    # Aliases para que el template de subsidy_detail los reconozca
    enriched_payload.setdefault("tiposBeneficiarios", beneficiarios["tipos"])
    enriched_payload.setdefault("tipoConvocatoria", submission_procedure)
    enriched_payload.setdefault("textInicio", None)
    enriched_payload.setdefault("textFin", None)
    enriched_payload.setdefault("documentos", extra["documents"])
    enriched_payload.setdefault("urlBasesReguladoras", portal_url)
    enriched_payload.setdefault("sedeElectronica", submission_url or portal_url)
    enriched_payload.setdefault("instrumentos", [
        {"descripcion": t} for t in types_of_action
    ])
    enriched_payload.setdefault("sectores", [
        {"descripcion": t, "codigo": None} for t in tags[:8]
    ])
    enriched_payload.setdefault("codigoBDNS", None)
    enriched_payload.setdefault("_eu_extra", extra)

    return {
        "source": "eu",
        "external_id": external_id,
        "titulo": title,
        "organismo": organismo,
        "ambito": "ue",
        "ccaa": None,
        "fecha_inicio": _parse_date(start_raw),
        "fecha_fin": _parse_date(deadline_raw),
        "importe_total": importe_total,
        "importe_max_beneficiario": importe_max,
        "porcentaje": None,
        "beneficiarios": beneficiarios,
        "cnae_elegible": [],
        "finalidad": finalidad,
        "descripcion": description,
        "enlace_oficial": portal_url,
        "raw_payload": enriched_payload,
        "estado": estado,
    }


def _upsert(session: Session, parsed: dict[str, Any]) -> bool:
    """Insert or update a Subvencion by (source='eu', external_id).

    Returns True if created, False if updated.
    """
    existing = session.execute(
        select(Subvencion).where(
            Subvencion.source == "eu",
            Subvencion.external_id == parsed["external_id"],
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(Subvencion(**parsed))
        return True

    for k, v in parsed.items():
        setattr(existing, k, v)
    return False


# Keywords usadas para barrer múltiples sectores en cada sync.
# El EU SEDIA search ignora todos los filtros de tema; la query de texto es el único
# pre-filtro real disponible. Cada keyword devuelve un subset diferente.
# Esta lista expandida (~50 keywords) maximiza cobertura — cada sector EU principal
# está representado para capturar el máximo de calls open disponibles para España.
_SECTOR_QUERIES: list[str] = [
    "",  # placeholder — se reemplaza por current/next year
    # Geografía
    "Spain España",
    "Iberian Peninsula",
    "Mediterranean",
    "Atlantic Area",
    # Tamaño empresa
    "SME PYME small medium",
    "startup",
    "microenterprise",
    # Sectores principales (Horizon Europe, CEF, LIFE, Digital, etc.)
    "research innovation",
    "Horizon Europe",
    "Horizon",
    "ERC European Research Council",
    "MSCA Marie Sklodowska Curie",
    "EIC European Innovation Council",
    "EIT European Institute Technology",
    "digital transformation",
    "Digital Europe Programme",
    "green energy renewable",
    "European Green Deal",
    "climate change adaptation",
    "agriculture food",
    "common agricultural policy CAP",
    "fisheries aquaculture",
    "rural development",
    "education training Erasmus",
    "youth",
    "health medical pharma",
    "EU4Health",
    "cancer mission",
    "AI artificial intelligence machine learning",
    "biotechnology biotech genomics",
    "manufacturing industry",
    "industrial",
    "automotive",
    "aerospace space defence",
    "tourism culture creative",
    "Creative Europe",
    "cybersecurity",
    "transport mobility",
    "Connecting Europe Facility CEF",
    "circular economy",
    "waste recycling",
    "water resources",
    "biodiversity nature LIFE",
    "social employment",
    "European Social Fund ESF",
    "skills training",
    "migration integration",
    "gender equality",
    "human rights democracy",
    "humanitarian aid",
    "development cooperation",
    "neighbourhood Africa Asia",
    "trade exports international",
    "fiscal customs",
    "innovation procurement",
    "twin transition",
    "data economy",
    "blockchain quantum",
    "robotics automation",
    "smart cities",
    "blue economy ocean",
    "construction buildings",
    "energy efficiency",
    "hydrogen",
    "battery storage",
    "electric vehicles charging",
]


async def sync_all(
    session: Session,
    max_pages: int = 50,
    page_size: int = 50,
    min_useful: int = 20,
) -> dict[str, int]:
    """Page through the EU search endpoint and upsert open / proximamente grants.

    Skips Closed records (status=31094503): users cannot apply to them and they
    dominate the default sort order.  Stops as soon as `min_useful` records have
    been persisted OR `max_pages` is reached.

    The search text is set to "<current_year> <next_year>" which significantly
    increases the proportion of open/future calls returned compared to "***".
    Server-side sort and filter params are silently ignored by the EU API
    (verified 2026-05), so this year-keyword approach is the most effective
    practical pre-filter available on the client side.

    Operational limit (verified 2026-05): the SEDIA search API is a full EU
    document index, not a grants-only index.  With any text query it yields
    only ~20-60 unique open grant topics across its accessible pages (644k
    total documents but ~99% are historical closed grants or non-grant docs).
    Setting min_useful=20 ensures we stop quickly once useful records are found
    rather than scanning all 30-50 pages for diminishing returns.

    Args:
        session:    SQLAlchemy session.
        max_pages:  Hard cap on pages fetched (default 50).
        page_size:  Items per page requested from the API (default 50).
        min_useful: Stop as soon as this many open/proximamente records have
                    been persisted (default 20, reflecting the API's practical
                    yield of unique open grants per sync run).

    Returns:
        {
            "created":        N,  # new rows inserted
            "updated":        M,  # existing rows refreshed
            "skipped_closed": K,  # closed records skipped (not upserted)
            "total":          N+M,
            "pages":          P,  # last page number consumed
        }
    """
    # Set primer placeholder al año actual+próximo
    now = datetime.now(tz=UTC)
    queries = list(_SECTOR_QUERIES)
    queries[0] = f"{now.year} {now.year + 1}"

    created = updated = skipped_closed = 0
    seen_ids: set[str] = set()
    last_page = 0

    # Itera por cada keyword sectorial; cada una trae records distintos gracias a
    # que el SEDIA search rankea diferente.
    for search_text in queries:
        page = 1
        useful_this_query = 0
        while page <= max_pages:
            try:
                payload = await fetch_page(page=page, page_size=page_size, text=search_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("EU sync error for query %r page %d: %s", search_text, page, exc)
                break
            results = payload.get("results") or []
            if not results:
                break

            for raw in results:
                parsed = parse_item(raw)
                if parsed is None:
                    continue
                if not parsed["external_id"]:
                    continue
                if parsed["external_id"] in seen_ids:
                    continue
                seen_ids.add(parsed["external_id"])
                if parsed["estado"] == "cerrada":
                    skipped_closed += 1
                    continue
                if _upsert(session, parsed):
                    created += 1
                else:
                    updated += 1
                useful_this_query += 1

            session.commit()
            last_page = page
            # Si esta query ya dio min_useful records, pasar a la siguiente keyword
            if useful_this_query >= min_useful:
                break
            total_pages = payload.get("totalPages")
            if total_pages is not None and page >= total_pages:
                break
            page += 1

    return {
        "created": created,
        "updated": updated,
        "skipped_closed": skipped_closed,
        "total": created + updated,
        "queries_run": len(queries),
        "pages": page,
    }
