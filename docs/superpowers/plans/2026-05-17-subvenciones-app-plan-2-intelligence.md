# Plan 2 — Inteligencia y datos completos (subvenciones-app)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir la app de Plan 1 (estructura funcional pero búsquedas a 0 resultados por datos vacíos) en algo realmente utilizable: datos BDNS completos vía detail endpoint, scoring LLM con razón, enriquecimiento del NIF, y segunda fuente (UE Funding & Tenders).

**Architecture:** Reutiliza la arquitectura de Plan 1 (FastAPI + Postgres + APScheduler + Jinja2 + HTMX). Añade nuevos módulos: `app/sync/bdns_enricher.py`, `app/sync/eu_puller.py`, `app/enrich/libreborme.py` (+ service), `app/matching/scorer_llm.py`, `app/sync/catalogs.py`. Nuevas tablas: `cnae` (precargada), `bdns_catalog` (jsonb por tipo de catálogo). El detail endpoint BDNS es `/api/convocatorias?numConv={X}` con rate-limit 10 req/s.

**Tech Stack:** Sumar a Plan 1 → `google-generativeai>=0.8.3` para Gemini 2.5 Flash. Sin cambios de DB engine.

**Pre-requisitos confirmados antes de empezar:**
- ✅ `GEMINI_API_KEY` ya guardada en `.env` local (no en git).
- ✅ libreborme.net es público sin auth.
- ✅ UE Funding & Tenders Portal (`https://api.tech.ec.europa.eu/search-api/prod/rest/search`) público sin auth.
- ✅ BDNS detail endpoint confirmado: `GET https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv={X}` (query param). Ver [docs/superpowers/specs/2026-05-17-subvenciones-app-design.md](../specs/2026-05-17-subvenciones-app-design.md) o memoria `reference_bdns_api.md`.
- ❌ OpenCorporates eliminado del scope: subieron pricing a £2.250/año. Solo libreborme + fallback manual.

---

## Orden de ejecución y rationale

1. **Task 1 — BDNS detail enricher**: backfill de los 6.394 records existentes. Sin esto el resto no aporta porque seguimos a 0 resultados.
2. **Task 2 — BDNS catalogs**: trae los catálogos oficiales (finalidades, beneficiarios, regiones, actividades) para usarlos como referencia. Pequeño.
3. **Task 3 — Matching update**: ajusta filtro+scoring ahora que los datos son ricos. Después de esto la web ya devuelve resultados reales.
4. **Task 4 — NIF enrichment + HTMX**: auto-rellenado de razón social en el formulario home.
5. **Task 5 — Gemini scorer**: añade razón en lenguaje natural + scoring inteligente sobre el top 30.
6. **Task 6 — UE Funding & Tenders sync**: segunda fuente (UE).

Cada tarea hace su PR/commit limpio. La 1 y la 3 son las más impactantes para UX; pueden mergearse antes y desplegar.

---

## Task 1: BDNS detail enricher

**Files:**
- Create: `app/sync/bdns_enricher.py`
- Create: `app/sync/bdns_mappers.py` (mapeo detail → modelo)
- Create: `tests/fixtures/bdns/detail_sample.json`
- Create: `tests/unit/test_bdns_enricher.py`
- Modify: `app/sync/runner.py` (añadir job de enrichment incremental)
- Modify: `app/sync/bdns_puller.py:sync_all` (después de upsert listing, encolar para enrichment)

### Step 1: Capturar fixture real del detail endpoint

- [ ] Activar venv y hacer fetch real para guardar el ejemplo en `tests/fixtures/bdns/detail_sample.json`:
  ```bash
  source .venv/bin/activate
  python -c "
  import httpx, json
  r = httpx.get('https://www.infosubvenciones.es/bdnstrans/api/convocatorias',
               params={'numConv': '906115'},
               headers={'Accept': 'application/json', 'User-Agent': 'subvenciones-app/0.1'},
               timeout=30)
  print('status:', r.status_code)
  print(json.dumps(r.json(), indent=2, ensure_ascii=False))" > /tmp/sample.json
  ```
  Inspecciona la salida y crea `tests/fixtures/bdns/detail_sample.json` con un JSON real (anonimizando si quieres pero mantén los nombres de campos exactos).

  Si `906115` ya no existe en BDNS, escoge cualquier otro `numeroConvocatoria` actual del DB:
  ```bash
  psql postgresql://subvenciones:subvenciones@localhost:5432/subvenciones \
    -tAc "SELECT external_id FROM subvencion LIMIT 1"
  ```

### Step 2: TDD del mapper (sin red)

- [ ] Escribir test que falla en `tests/unit/test_bdns_enricher.py`:
  ```python
  import json
  from datetime import date
  from pathlib import Path

  FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "bdns" / "detail_sample.json"


  def test_map_detail_to_subvencion_fields():
      from app.sync.bdns_mappers import map_detail

      detail = json.loads(FIXTURE.read_text())
      mapped = map_detail(detail)

      # Campos obligatorios
      assert mapped["source"] == "bdns"
      assert mapped["external_id"] == detail.get("codigoBDNS") or str(detail.get("codigoBDNS"))
      # Si presupuestoTotal venía en la fixture, debe quedar mapeado:
      if detail.get("presupuestoTotal") is not None:
          assert mapped["importe_total"] is not None
      # Si fechaFinSolicitud venía, debe ser un date object
      if detail.get("fechaFinSolicitud"):
          assert isinstance(mapped["fecha_fin"], date)
      # CNAE/sectores
      if detail.get("sectores"):
          assert isinstance(mapped["cnae_elegible"], list)
      # Finalidad inferida de descripcionFinalidad por keywords (al menos lista, vacía o no)
      assert isinstance(mapped["finalidad"], list)
      # Enlace oficial desde anuncios[0].url si existe
      if detail.get("anuncios") and detail["anuncios"][0].get("url"):
          assert mapped["enlace_oficial"] == detail["anuncios"][0]["url"]
      # estado depende de abierto
      assert mapped["estado"] in ("abierta", "cerrada", "proximamente")
      assert mapped["raw_payload"] == detail
  ```
- [ ] `pytest tests/unit/test_bdns_enricher.py -v` → FAIL (ImportError).

### Step 3: Implementar el mapper

- [ ] Crear `app/sync/bdns_mappers.py` con:
  - `_FINALIDAD_KEYWORDS: dict[str, list[str]]` — mapeo keyword → token normalizado de finalidad. Cubrir al menos: `"digital"→digitalizacion`, `"i+d"|"investigaci"|"i+i"→i+d`, `"contrat"→contratacion`, `"energ"|"renov"→eficiencia_energetica`, `"internacional"|"export"→internacionalizacion`, `"formaci"→formacion`, `"innov"→innovacion`. Si nada matchea: `["otros"]`.
  - `_NIVEL1_TO_AMBITO`: mismo mapeo que `bdns_puller.py` (reutilizar import).
  - `_to_date(s: str | None) -> date | None`
  - `infer_finalidad(text: str | None) -> list[str]`: normaliza accents+lowercase, busca keywords. Si vacío devuelve `[]`.
  - `map_detail(detail: dict) -> dict[str, Any]`:
    ```python
    organo = detail.get("organo") or {}
    nivel1 = (organo.get("nivel1") or "").upper()
    ambito = _NIVEL1_TO_AMBITO.get(nivel1, "estatal")
    sectores = detail.get("sectores") or []
    return {
        "source": "bdns",
        "external_id": str(detail.get("codigoBDNS")),
        "titulo": detail.get("descripcion") or "",
        "organismo": organo.get("nivel3") or organo.get("nivel2") or organo.get("nivel1"),
        "ambito": ambito,
        "ccaa": None,  # no expuesto directamente, podemos enriquecer luego
        "fecha_inicio": _to_date(detail.get("fechaInicioSolicitud")) or _to_date(detail.get("fechaRecepcion")),
        "fecha_fin": _to_date(detail.get("fechaFinSolicitud")),
        "importe_total": detail.get("presupuestoTotal"),
        "importe_max_beneficiario": None,
        "porcentaje": None,
        "beneficiarios": {
            "tipos": [b.get("descripcion") for b in (detail.get("tiposBeneficiarios") or []) if b.get("descripcion")]
        } if detail.get("tiposBeneficiarios") else None,
        "cnae_elegible": [s.get("codigo") for s in sectores if s.get("codigo")],
        "finalidad": infer_finalidad(detail.get("descripcionFinalidad")),
        "descripcion": detail.get("descripcionBasesReguladoras") or detail.get("descripcion"),
        "enlace_oficial": (detail.get("anuncios") or [{}])[0].get("url") or detail.get("urlBasesReguladoras"),
        "raw_payload": detail,
        "estado": "abierta" if detail.get("abierto") else "cerrada",
    }
    ```
- [ ] `pytest tests/unit/test_bdns_enricher.py::test_map_detail_to_subvencion_fields -v` → PASS.

### Step 4: TDD del fetcher con httpx_mock

- [ ] Añadir test que falla:
  ```python
  import json
  from pathlib import Path
  import pytest

  FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "bdns" / "detail_sample.json"


  @pytest.mark.asyncio
  async def test_fetch_detail_returns_parsed_json(httpx_mock):
      payload = json.loads(FIXTURE.read_text())
      httpx_mock.add_response(
          url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=906115",
          json=payload,
      )
      from app.sync.bdns_enricher import fetch_detail
      result = await fetch_detail("906115")
      assert result["codigoBDNS"] == payload["codigoBDNS"]


  @pytest.mark.asyncio
  async def test_fetch_detail_returns_none_on_204(httpx_mock):
      httpx_mock.add_response(
          url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=NOEXISTE",
          status_code=204,
      )
      from app.sync.bdns_enricher import fetch_detail
      result = await fetch_detail("NOEXISTE")
      assert result is None
  ```

### Step 5: Implementar fetcher

- [ ] Crear `app/sync/bdns_enricher.py` con:
  ```python
  from __future__ import annotations
  import asyncio, logging
  from datetime import date
  from typing import Any

  import httpx
  from sqlalchemy import select
  from sqlalchemy.orm import Session

  from app.config import get_settings
  from app.db.models import Subvencion
  from app.sync.bdns_mappers import map_detail

  logger = logging.getLogger(__name__)
  settings = get_settings()
  _RATE_LIMIT_SLEEP = 0.1  # 10 req/s safe rate
  _HEADERS = {"Accept": "application/json", "User-Agent": "subvenciones-app/0.1"}


  async def fetch_detail(num_conv: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
      """Devuelve el JSON del detail endpoint o None si 204/404.

      Reintento en 429 con backoff 2s (hasta 3 intentos).
      """
      url = f"{settings.bdns_base_url}/convocatorias"
      params = {"numConv": num_conv}
      owns_client = client is None
      if owns_client:
          client = httpx.AsyncClient(timeout=30.0, headers=_HEADERS)
      try:
          for attempt in range(3):
              r = await client.get(url, params=params)
              if r.status_code == 429:
                  await asyncio.sleep(2 * (attempt + 1))
                  continue
              if r.status_code in (204, 404):
                  return None
              r.raise_for_status()
              return r.json()
          return None
      finally:
          if owns_client:
              await client.aclose()


  async def enrich_existing(session: Session, batch_size: int = 200, max_records: int | None = None) -> dict[str, int]:
      """Backfill: por cada Subvencion con external_id BDNS sin enriquecer (importe_total IS NULL
      AND fecha_fin IS NULL), llama al detail y actualiza el row.

      Returns: {"enriched": N, "skipped": M, "errors": E}
      """
      stmt = select(Subvencion).where(
          Subvencion.source == "bdns",
          Subvencion.importe_total.is_(None),
          Subvencion.fecha_fin.is_(None),
      )
      if max_records:
          stmt = stmt.limit(max_records)
      rows = session.execute(stmt).scalars().all()
      enriched = skipped = errors = 0

      async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS) as client:
          for sub in rows:
              try:
                  detail = await fetch_detail(sub.external_id, client=client)
                  if detail is None:
                      skipped += 1
                  else:
                      mapped = map_detail(detail)
                      for k, v in mapped.items():
                          if k == "raw_payload" or v is not None and v != []:
                              setattr(sub, k, v)
                      enriched += 1
                      if enriched % batch_size == 0:
                          session.commit()
                          logger.info("Enriched %d/%d", enriched, len(rows))
              except Exception as exc:
                  errors += 1
                  logger.warning("Error enriching %s: %s", sub.external_id, exc)
              await asyncio.sleep(_RATE_LIMIT_SLEEP)
          session.commit()
      return {"enriched": enriched, "skipped": skipped, "errors": errors, "total": len(rows)}


  async def enrich_one(session: Session, external_id: str) -> bool:
      """Enriquece un único record. Útil para el cron incremental."""
      detail = await fetch_detail(external_id)
      if detail is None:
          return False
      sub = session.execute(
          select(Subvencion).where(Subvencion.source == "bdns", Subvencion.external_id == external_id)
      ).scalar_one_or_none()
      if sub is None:
          return False
      for k, v in map_detail(detail).items():
          if k == "raw_payload" or (v is not None and v != []):
              setattr(sub, k, v)
      session.commit()
      return True
  ```
- [ ] `pytest tests/unit/test_bdns_enricher.py -v` → todos PASS (4 tests).

### Step 6: Test del enrich_existing con DB

- [ ] Añadir test que siembra 2 Subvencion (una con importe_total=None y otra con valor existente), mockea httpx para `numConv=X1` y verifica que solo X1 fue enriquecida.
  ```python
  @pytest.mark.asyncio
  async def test_enrich_existing_only_processes_empty_records(db_session, httpx_mock):
      from app.db.models import Subvencion
      from app.sync.bdns_enricher import enrich_existing

      # Una vacía (target del enrich)
      sub_empty = Subvencion(
          source="bdns", external_id="EMPTY-1", titulo="t", ambito="estatal",
          cnae_elegible=[], finalidad=[], estado="abierta",
      )
      # Una ya enriquecida (debe ignorarse)
      sub_filled = Subvencion(
          source="bdns", external_id="FILLED-1", titulo="t", ambito="estatal",
          cnae_elegible=[], finalidad=[], estado="abierta",
          importe_total=1000, fecha_fin=date(2026, 12, 31),
      )
      db_session.add_all([sub_empty, sub_filled])
      db_session.commit()

      payload = json.loads(FIXTURE.read_text())
      payload["codigoBDNS"] = "EMPTY-1"
      httpx_mock.add_response(
          url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=EMPTY-1",
          json=payload,
      )

      stats = await enrich_existing(db_session)
      assert stats["enriched"] == 1
      assert stats["total"] == 1
  ```
- [ ] PASS.

### Step 7: Hook al cron y al listing sync

- [ ] En `app/sync/runner.py`, añadir un segundo job programado a las **03:30 Europe/Madrid** que llama `enrich_existing`. Reusar el patrón de `run_bdns_sync`.
  ```python
  async def run_bdns_enricher() -> None:
      """Backfill incremental: enriquece records BDNS que aún tengan campos vacíos."""
      logger.info("Starting BDNS enrichment pass")
      with SessionLocal() as session:
          stats = await enrich_existing(session, max_records=1000)
      logger.info("BDNS enrichment done: %s", stats)
  ```
  Añadir el `add_job` con `CronTrigger(hour=3, minute=30)`.

### Step 8: Backfill manual de los 6.394 + commit

- [ ] Ejecutar backfill local (ojo, ~11 minutos):
  ```bash
  source .venv/bin/activate
  python -c "
  import asyncio
  from app.db.session import SessionLocal
  from app.sync.bdns_enricher import enrich_existing
  with SessionLocal() as s:
      print(asyncio.run(enrich_existing(s)))
  "
  ```
  Esperar a que termine. Verificar con:
  ```bash
  psql postgresql://subvenciones:subvenciones@localhost:5432/subvenciones -c "
  SELECT
    COUNT(*) AS total,
    COUNT(importe_total) AS con_importe,
    COUNT(fecha_fin) AS con_fecha_fin,
    COUNT(NULLIF(cnae_elegible, '{}')) AS con_cnae,
    COUNT(NULLIF(finalidad, '{}')) AS con_finalidad
  FROM subvencion;"
  ```
  Esperado: la mayoría con importe y fecha_fin (los pocos sin son los que devuelven 204).

- [ ] Pasada final completa de tests: `pytest tests/ -v 2>&1 | tail -3` → al menos +5 nuevos.

- [ ] Commit:
  ```bash
  git add app/sync/bdns_enricher.py app/sync/bdns_mappers.py app/sync/runner.py \
          tests/fixtures/bdns/detail_sample.json tests/unit/test_bdns_enricher.py
  git -c commit.gpgsign=false commit -m "feat(sync): BDNS detail enricher with rate-limited fetch + daily backfill cron"
  ```

---

## Task 2: BDNS catalogs sync (finalidades, beneficiarios, regiones, actividades)

**Files:**
- Create: `app/sync/catalogs.py`
- Create: `migrations/versions/0002_catalogs.py`
- Modify: `app/db/models.py` (nueva tabla `bdns_catalog`)
- Create: `tests/unit/test_catalogs.py`

### Step 1: Modelo + migración

- [ ] En `app/db/models.py` añadir:
  ```python
  class BdnsCatalog(Base):
      __tablename__ = "bdns_catalog"
      kind: Mapped[str] = mapped_column(String(32), primary_key=True)  # finalidades|beneficiarios|...
      payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
      updated_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
      )
  ```
- [ ] `alembic revision --autogenerate -m "add bdns_catalog table"` → renombrar a `0002_catalogs.py`. Verificar que el SQL es correcto. Aplicar: `alembic upgrade head`.

### Step 2: Sync function + test

- [ ] Crear `app/sync/catalogs.py` con:
  ```python
  CATALOG_ENDPOINTS = {
      "finalidades": "/api/finalidades?vpd=GE",
      "beneficiarios": "/api/beneficiarios?vpd=GE",
      "instrumentos": "/api/instrumentos",
      "regiones": "/api/regiones",
      "actividades": "/api/actividades",
  }


  async def sync_catalogs(session: Session) -> dict[str, int]:
      base = get_settings().bdns_base_url.replace("/api", "")  # strip /api since paths have /api
      out = {}
      async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
          for kind, path in CATALOG_ENDPOINTS.items():
              r = await client.get(f"{base}{path}")
              r.raise_for_status()
              data = r.json()
              # upsert
              existing = session.get(BdnsCatalog, kind)
              if existing:
                  existing.payload = data
              else:
                  session.add(BdnsCatalog(kind=kind, payload=data))
              out[kind] = len(data) if isinstance(data, list) else 1
      session.commit()
      return out
  ```
- [ ] Test con httpx_mock simulando 5 catálogos, verifica upsert y conteos.
- [ ] Hook en `runner.py`: job mensual el día 1 a las 04:00. Por ahora también lanzarlo a mano una vez para popular.
- [ ] Commit: `feat(sync): BDNS catalogs sync (finalidades, beneficiarios, regiones, actividades, instrumentos)`.

---

## Task 3: Matching update (datos ricos)

**Files:**
- Modify: `app/matching/filter.py`
- Modify: `tests/unit/test_matching_filter.py`

### Cambios al filtro

- [ ] Si la query del usuario tiene `finalidad`, hacer el filtro **lenient**: incluir registros cuyo `finalidad && perfil.finalidad` **OR** cuyo `finalidad = '{}'` (subvenciones que no se pudieron clasificar igualmente se muestran al final, score bajo). Antes era estricto.
- [ ] El score determinista (`_compute_score`) ya cubre el caso de finalidad sin overlap (no suma los +30) — no requiere cambios.
- [ ] Añadir 2 tests:
  ```python
  def test_filter_includes_subvencion_with_empty_finalidad_too(db_session, perfil_pyme_digital):
      from app.matching.filter import find_candidates
      db_session.add_all([
          _make_subvencion(external_id="MATCH", cnae_elegible=["6201"], finalidad=["digitalizacion"]),
          _make_subvencion(external_id="GENERIC", cnae_elegible=["6201"], finalidad=[]),
      ])
      db_session.commit()
      results = find_candidates(db_session, perfil_pyme_digital, limit=30)
      ids = [c.subvencion.external_id for c in results]
      assert "TEST-MATCH" in ids
      assert "TEST-GENERIC" in ids
      # El que matchea finalidad debe ir antes
      assert ids.index("TEST-MATCH") < ids.index("TEST-GENERIC")
  ```
- [ ] Commit: `feat(matching): allow records without finalidad as fallback matches`.

---

## Task 4: NIF enrichment (libreborme + HTMX endpoint)

**Files:**
- Create: `app/enrich/__init__.py`
- Create: `app/enrich/libreborme.py`
- Create: `app/enrich/service.py`
- Create: `app/web/routes_enrich.py`
- Create: `app/web/templates/partials/enrich_result.html` (HTMX response partial)
- Modify: `app/web/templates/home.html` (añadir hx-get en blur del NIF)
- Modify: `app/main.py` (incluir nuevo router)
- Create: `tests/unit/test_enrich.py`

### libreborme API

- libreborme.net tiene API gratis sin auth: `GET https://libreborme.net/api/company/{nif}/`. Respuesta JSON con campos `cif`, `name`, `address`, `province`, etc. Si no encuentra: 404.

### Implementación

- [ ] `app/enrich/libreborme.py`:
  ```python
  async def fetch_company(nif: str) -> dict | None:
      """Devuelve {razon_social, provincia, cnae_estimado?} o None si no encuentra."""
      url = f"https://libreborme.net/api/company/{nif}/"
      async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "subvenciones-app/0.1"}) as client:
          r = await client.get(url)
          if r.status_code == 404:
              return None
          r.raise_for_status()
          data = r.json()
          return {
              "razon_social": data.get("name"),
              "provincia_text": data.get("province"),
          }
  ```
- [ ] `app/enrich/service.py`: orquestador. Por ahora solo libreborme. Si libreborme falla, devuelve `None` sin levantar excepción.
- [ ] `app/web/routes_enrich.py`: endpoint `GET /api/enrich/{nif}` que devuelve HTMX HTML parcial. Validar NIF primero con `validate_nif`; si inválido 400 con mensaje.
- [ ] El partial `enrich_result.html` rellena los campos `razon_social` y `provincia` del form con `hx-swap-oob` (out-of-band swap).
- [ ] En `home.html` cambiar el input NIF para que dispare HTMX:
  ```html
  <input type="text" name="nif" required pattern=".{8,10}" placeholder="B12345678"
         class="w-full border border-gray-300 rounded px-3 py-2"
         hx-get="/api/enrich" hx-trigger="blur changed" hx-target="#enrich-result" hx-include="this">
  ```
  Y añadir `<div id="enrich-result"></div>` debajo. Server lee `request.query_params['nif']`.
- [ ] Tests (mockear httpx):
  - libreborme 200 → devuelve dict con razon_social.
  - libreborme 404 → None silencioso.
  - libreborme 500 → None silencioso, log warning.
  - Endpoint HTMX `/api/enrich/B12345674` con mock → HTML contiene "Flexigobe" (o lo que devuelva el mock).
  - Endpoint con NIF inválido → 400 con mensaje claro.
- [ ] Commit: `feat(enrich): NIF auto-completion via libreborme.net with HTMX`.

---

## Task 5: Gemini scorer (LLM scoring + razón)

**Files:**
- Create: `app/matching/scorer_llm.py`
- Modify: `app/matching/service.py` (integrar scoring LLM al final del pipeline)
- Modify: `app/config.py` (añadir `gemini_api_key: str = ""` y `gemini_model: str = "gemini-2.5-flash"`)
- Modify: `pyproject.toml` (añadir `google-generativeai>=0.8.3`)
- Create: `tests/unit/test_scorer_llm.py`

### Diseño

- 3 llamadas batch a Gemini por búsqueda (10 subvenciones por call). Pre-rank determinista deja top 30 candidatos, LLM puntúa cada uno con `score 0-100` + `razon` (1 frase).
- Cache: `_score_cache[empresa_perfil_hash, subvencion_id] = (score, razon, expires_at)` en memoria (TTL 7 días). Para Plan 2 con memoria del proceso vale; Plan 4 mueve a Redis si hace falta.
- Fallback: si Gemini timeout (>8s) o error → usar score determinista del pre-rank, razon = None.

### Implementación

- [ ] Añadir `google-generativeai` a `pyproject.toml`, `pip install -e ".[dev]"`.
- [ ] `app/matching/scorer_llm.py`:
  ```python
  import asyncio, hashlib, json, time
  from dataclasses import dataclass
  import google.generativeai as genai
  from app.config import get_settings

  _PROMPT_TEMPLATE = """Eres asesor de subvenciones para PYMES españolas. Para cada subvención
  evalúa cómo de bien encaja con esta empresa y devuelve un JSON array con un objeto por
  subvención (en el mismo orden) con campos {"score": int 0-100, "razon": "una frase en español"}.

  EMPRESA: cnae={cnae}, tamano={tamano}, provincia={provincia}, finalidad={finalidad}

  SUBVENCIONES:
  {items}

  Responde SOLO con el array JSON, sin texto adicional."""

  _cache: dict[str, tuple[int, str | None, float]] = {}
  _CACHE_TTL = 7 * 86400  # 7 días


  def _cache_key(perfil_hash: str, sub_id: str) -> str:
      return f"{perfil_hash}:{sub_id}"


  def _perfil_hash(perfil) -> str:
      blob = f"{perfil.cnae}|{perfil.tamano}|{perfil.provincia}|{sorted(perfil.finalidad)}"
      return hashlib.sha256(blob.encode()).hexdigest()[:16]


  async def score_batch(perfil, subvenciones, timeout: float = 8.0) -> list[tuple[int, str | None]]:
      """Devuelve [(score, razon), ...] paralelo de longitud len(subvenciones).
      Si LLM falla, devuelve [(score_det, None), ...] usando el score que ya viene del candidate.
      """
      settings = get_settings()
      if not settings.gemini_api_key:
          return [(s.score, None) for s in subvenciones]
      genai.configure(api_key=settings.gemini_api_key)
      model = genai.GenerativeModel(settings.gemini_model)
      ph = _perfil_hash(perfil)

      # Aplicar cache primero
      results: list[tuple[int, str | None] | None] = [None] * len(subvenciones)
      to_score: list[tuple[int, object]] = []  # (idx, candidate)
      now = time.time()
      for idx, c in enumerate(subvenciones):
          key = _cache_key(ph, str(c.subvencion.id))
          cached = _cache.get(key)
          if cached and cached[2] > now:
              results[idx] = (cached[0], cached[1])
          else:
              to_score.append((idx, c))

      # Batchear los no cacheados en grupos de 10
      for batch_start in range(0, len(to_score), 10):
          batch = to_score[batch_start: batch_start + 10]
          items_text = "\n".join(
              f"{i+1}. id={c.subvencion.external_id}: {c.subvencion.titulo or ''} | "
              f"finalidad={c.subvencion.finalidad} | cnae={c.subvencion.cnae_elegible} | "
              f"organismo={c.subvencion.organismo}"
              for i, (_, c) in enumerate(batch)
          )
          prompt = _PROMPT_TEMPLATE.format(
              cnae=perfil.cnae, tamano=perfil.tamano,
              provincia=perfil.provincia, finalidad=perfil.finalidad,
              items=items_text,
          )
          try:
              # Gemini SDK is sync; run in thread
              resp = await asyncio.wait_for(
                  asyncio.to_thread(model.generate_content, prompt),
                  timeout=timeout,
              )
              text = resp.text.strip()
              if text.startswith("```"):
                  text = text.strip("`").split("\n", 1)[-1].rsplit("\n", 1)[0]
              parsed = json.loads(text)
              for (idx, c), item in zip(batch, parsed):
                  score = max(0, min(100, int(item.get("score", c.score))))
                  razon = (item.get("razon") or "")[:280] or None
                  results[idx] = (score, razon)
                  _cache[_cache_key(ph, str(c.subvencion.id))] = (score, razon, now + _CACHE_TTL)
          except Exception:
              for idx, c in batch:
                  results[idx] = (c.score, None)

      return [r if r is not None else (subvenciones[i].score, None) for i, r in enumerate(results)]
  ```
- [ ] Modificar `app/matching/service.py:rank_for`:
  ```python
  async def rank_for(session, perfil, limit=30):
      candidates = find_candidates(session, perfil, limit=limit)
      llm_scores = await score_batch(perfil, candidates)
      out = []
      for i, (c, (s, r)) in enumerate(zip(candidates, llm_scores)):
          out.append(RankedResult(subvencion=c.subvencion, score=s, razon=r, rank=0))
      out.sort(key=lambda x: x.score, reverse=True)
      for i, x in enumerate(out):
          # rank field via dataclass.replace since frozen
          out[i] = RankedResult(subvencion=x.subvencion, score=x.score, razon=x.razon, rank=i+1)
      return out
  ```
  Ahora `rank_for` es **async**. Actualizar la llamada en `app/web/routes_search.py` para `await`-earla y la ruta `POST /search` para ser `async def`.
- [ ] Tests:
  - Mock `google.generativeai` (monkey patch del módulo) para devolver un JSON predecible.
  - Test que con `gemini_api_key=""` se hace bypass y devuelve scores deterministas.
  - Test que con timeout simulado se hace fallback.
  - Test que la caché evita 2ª llamada para mismo perfil+subvencion.
- [ ] Actualizar `tests/unit/test_routes_search.py:test_search_returns_results_html` — ahora la respuesta puede tener `razon` rendered en results.html. Añadir asserción opcional.
- [ ] Modificar `results.html` para mostrar `razon` debajo del título de cada card top 3 si está presente:
  ```html
  {% if r.razon %}<p class="text-xs text-gray-700 italic mt-1">{{ r.razon }}</p>{% endif %}
  ```
- [ ] Commit: `feat(matching): Gemini 2.5 Flash scoring with razon and 7d cache`.

---

## Task 6: UE Funding & Tenders sync

**Files:**
- Create: `app/sync/eu_puller.py`
- Modify: `app/sync/runner.py` (job nuevo)
- Create: `tests/fixtures/eu/page_sample.json`
- Create: `tests/unit/test_eu_puller.py`

### API UE

- Endpoint público: `POST https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA&text=*&pageNumber=1&pageSize=100&languages=es,en`
- Body JSON con filtros. Documentación: https://webgate.ec.europa.eu/funding-tenders-opportunities/help/en/topics-and-applications/search-funding
- Fields per result: `metadata.title`, `metadata.deadline`, `metadata.budget`, `metadata.identifier`, `metadata.programme`, `metadata.callIdentifier`, `metadata.url`, etc.

### Implementación

- [ ] `app/sync/eu_puller.py`:
  - `async def fetch_page(page, page_size, body) -> dict` con httpx POST.
  - `def parse_item(raw) -> dict` mapeo similar a `bdns_mappers.map_detail`:
    - `source="eu"`, `ambito="ue"`, `external_id=metadata["identifier"]`, `titulo=metadata["title"]`, `fecha_fin=metadata["deadline"]`, `importe_total=metadata.get("budget")`, `descripcion=metadata.get("summary")`, `enlace_oficial=metadata["url"]`, `raw_payload=metadata`.
    - `finalidad` se infiere igual que BDNS con keywords.
    - `cnae_elegible=[]` (UE no usa CNAE, sectores son distintos).
  - `async def sync_all(session, ...) -> dict[str, int]` paginado, igual pattern que BDNS.
- [ ] Cron job en `runner.py` a las **03:45** (después de BDNS).
- [ ] Smoke real: ejecutar a mano, verificar conteos.
- [ ] 4 tests (fetch, parse, upsert, sync_all).
- [ ] Commit: `feat(sync): EU Funding & Tenders Portal sync`.

---

## Pasada final + merge

- [ ] `pytest tests/ -v 2>&1 | tail -5` debe mostrar **65+ tests passing** (54 previos + ~11 nuevos).
- [ ] Smoke completo: arrancar uvicorn, hacer una búsqueda real desde el navegador con NIF válido + finalidad → ver subvenciones reales con score LLM y `razon` en cards top 3.
- [ ] Backfill final si quedaron records sin enriquecer.
- [ ] Tag: `git tag -a v0.2.0-plan2 -m "Plan 2 complete: BDNS detail enrichment + Gemini scorer + NIF enrich + EU sync"`.
- [ ] Merge a `main` con `--no-ff` y mensaje resumen.

## Cierre

Al terminar Plan 2:
- La web devuelve **resultados reales** con scoring inteligente y razón en lenguaje natural.
- BDNS enriquecida con datos oficiales completos.
- UE como segunda fuente.
- NIF auto-completado en el formulario.

**Lo que NO trae todavía (Plan 3):** captura de email + PDF + alertas diarias por email.
**Lo que NO trae todavía (Plan 4):** panel admin + rate limiting + deploy Railway.
