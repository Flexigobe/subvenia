# Plan 5 — BORME ingest + UX pivot (todo oficial + gratis)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Resolver el muro estructural "NIF → razón social bloqueado en España" pivotando la UX: el usuario teclea su razón social en el formulario (con autocomplete contra una base local construida desde BORME oficial), y nosotros le auto-rellenamos provincia + datos. El NIF queda opcional para captura de leads. Todo gratis, todo con fuentes oficiales (BOE Datos Abiertos / BORME).

**Architecture:** Nueva tabla `empresa` poblada por un ingester diario que descarga BORME-A PDFs vía el sumario XML oficial, extrae texto con `pypdf` y parsea entradas con regex. Backfill histórico (90 días → ~225k empresas). HTMX autocomplete sustituye al campo NIF como input primario. POST /search valida pero ya no requiere NIF.

**Tech Stack:** Suma a Plan 4 → `pypdf>=4.0` para extracción de texto PDF.

**Pre-requisitos:**
- ✅ Plan 4 mergeado (`v0.4.0-plan4` en main).
- ✅ Investigación BORME confirmada: PDFs públicos sin rate-limit, ~2.480 empresas/día, formato estable de regex parseable, dominio + razón social + acto extraíbles, **CIF NO publicado** (limitación estructural española).

---

## Orden de ejecución

1. Setup: modelo + migración + dep `pypdf`
2. Sumario fetcher (HTTP)
3. PDF parser (corazón del plan)
4. Ingester + cron + backfill script
5. Autocomplete endpoint + pivot del formulario
6. Admin `/admin/empresas` + review + merge

---

## Task 1: empresa model + migration 0004 + pypdf dependency

**Files:**
- Modify: `pyproject.toml` (añadir `pypdf>=4.0`)
- Modify: `app/db/models.py` (modelo `Empresa`)
- Create: `migrations/versions/0004_empresa.py`
- Create: `tests/unit/test_empresa_model.py`

### Modelo

```python
class Empresa(Base):
    __tablename__ = "empresa"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Slug derivado de razón social (lowercase, sin acentos, sin sufijos S.L./S.A.) — buscable
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    razon_social: Mapped[str] = mapped_column(Text, nullable=False)
    # Provincia INE (08 = Barcelona) — derivada del PDF de provincia
    provincia: Mapped[str | None] = mapped_column(String(2), index=True)
    domicilio: Mapped[str | None] = mapped_column(Text)
    objeto_social: Mapped[str | None] = mapped_column(Text)
    # Registro Mercantil hoja: ej "H A 197635"
    hoja_rm: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    capital_social: Mapped[float | None] = mapped_column(Numeric(16, 2))
    fecha_constitucion: Mapped[date | None] = mapped_column(Date)
    fecha_ultima_act: Mapped[date | None] = mapped_column(Date)
    actos: Mapped[list | None] = mapped_column(JSONB)  # [{fecha, tipo, detalle}]
    estado: Mapped[str] = mapped_column(
        Enum("activa", "disuelta", "concursal", name="empresa_estado_enum"),
        default="activa", nullable=False,
    )
    raw_text: Mapped[str | None] = mapped_column(Text)  # texto crudo de BORME para debug
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

Migration autogen + manual review (asegurar JSONB, enum, índices).

Tests: 1 test que persiste una Empresa y verifica que se puede recuperar por slug.

Commit: `feat(db): empresa table + migration 0004`.

---

## Task 2: BORME sumario fetcher

**Files:**
- Create: `app/sync/borme_fetcher.py`
- Create: `tests/unit/test_borme_fetcher.py`

```python
async def fetch_sumario(target: date) -> list[dict]:
    """Devuelve [{"identificador": "BORME-A-2025-91-03", "provincia": "ALICANTE",
                  "url_pdf": "https://www.boe.es/borme/dias/2025/05/16/pdfs/BORME-A-2025-91-03.pdf"},
                 ...] para esa fecha. None/[] si no hay BORME ese día (fin de semana/festivo)."""

async def fetch_pdf(url: str) -> bytes:
    """Descarga el PDF. Reintento simple en 5xx."""
```

URL base: `https://www.boe.es/datosabiertos/api/borme/sumario/{YYYYMMDD}` (formato XML). Parsear con `xml.etree.ElementTree`.

Tests con `pytest-httpx`: mock del sumario + mock de PDF. Cubrir caso fin de semana (404 → []).

Commit: `feat(sync): BORME XML sumario fetcher + PDF downloader`.

---

## Task 3: BORME PDF parser (corazón)

**Files:**
- Create: `app/sync/borme_parser.py`
- Create: `tests/fixtures/borme/sample_alicante.pdf` (capturar uno real, ~5-10 empresas)
- Create: `tests/unit/test_borme_parser.py`

### Spec del parser

Cada PDF BORME-A de provincia tiene este formato repetido:

```
218391 - NOPESABOX VILLENA SL.
Constitución. Comienzo de operaciones: 11.04.25. Objeto social: La explotación
de negocios dedicados a la actividad de gimnasios. Domicilio: C/ SANTO CRISTO 5 1º 1
(BANYERES DE MARIOLA). Capital: 3.000,00 Euros. Nombramientos. Adm. Solid.:
MAESTRE SANTIAGO ERNESTO. Datos registrales. S 8, H A 197635, I/A 1 (8.05.25).
```

Función pública:

```python
def parse_pdf_text(text: str, provincia_code: str) -> list[dict]:
    """Parsea el texto extraído de pypdf y devuelve una lista de diccionarios
    listos para upsert en `empresa`. Cada dict tiene:
      - razon_social, slug, provincia, domicilio, objeto_social, hoja_rm,
        capital_social, fecha_constitucion, fecha_ultima_act, actos (list), estado, raw_text"""

def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Wrapper sobre pypdf que devuelve el texto plano completo, concatenando páginas."""

def slugify(razon_social: str) -> str:
    """Lowercase, strip accents, strip sufijos S.L./S.A./SL/SA/SLU/SLNE,
    collapse whitespace. Devuelve identificador buscable estable."""
```

### Regex / heurística

- Entrada empresa: empieza con `\d{6}\s*-\s*(.+?)\.\s*\n` — el número de inscripción + razón social.
- Acto = Constitución | Modificación | Nombramientos | Cese | Disolución | Cambio de domicilio | Reelecciones | etc.
- Domicilio: `Domicilio:\s*(.+?)(?=Capital:|Objeto social:|Datos registrales:|Nombramientos:|$)` (lookahead)
- Capital: `Capital:\s*([\d.,]+)\s*Euros`
- Hoja RM: `H\s*[A-Z]\s*\d+`
- Fecha constitución: `Comienzo de operaciones:\s*(\d{1,2}\.\d{1,2}\.\d{2})`
- Estado: si aparece "Disolución" → `disuelta`; si "Declaración de concurso" → `concursal`; default `activa`.

El parser DEBE ser robusto a múltiples actos en la misma empresa, líneas multi-línea en direcciones, y casos donde algunos campos faltan.

Tests:
- `test_parse_constitucion_full` — empresa con todos los campos
- `test_parse_nombramientos_only` — empresa sin Constitución, solo Nombramientos (no extrae capital ni objeto)
- `test_parse_disolucion_marks_disuelta`
- `test_parse_multiple_empresas_in_one_pdf` — el sample fixture
- `test_slugify_strips_sufijos_and_accents`

Commit: `feat(sync): BORME PDF text extraction + entry parser`.

---

## Task 4: Ingester + daily cron + backfill script

**Files:**
- Create: `app/sync/borme_ingester.py`
- Modify: `app/sync/runner.py` (job cron diario)
- Create: `scripts/backfill_borme.py`
- Create: `tests/unit/test_borme_ingester.py`

### Ingester

```python
async def sync_day(session: Session, target: date) -> dict[str, int]:
    """Para `target`: fetch sumario → fetch cada PDF de sección A → parse →
    upsert en `empresa` por `hoja_rm` (UNIQUE)."""
    # Returns {"new": N, "updated": M, "skipped_no_hoja": K, "errors": E}
```

Upsert por `hoja_rm` (único). Si la empresa ya existe, actualizar `actos` (append) y `fecha_ultima_act`. Para constitución de empresa nueva, insertar todo. Si una empresa aparece en BORME con estado "Disolución", marcar `estado='disuelta'`.

### Cron

Job nuevo en `app/sync/runner.py` a las **10:30 Europe/Madrid** (BORME se publica sobre las 8-9, le damos margen):

```python
async def run_borme_sync() -> None:
    with SessionLocal() as s:
        stats = await sync_day(s, target=date.today())
    logger.info("sync_complete", extra={"sync_name": "borme", "stats": stats})
```

### Backfill script

```python
# scripts/backfill_borme.py
"""Backfill últimos N días de BORME. Saltable y reanudable."""
import asyncio, argparse
from datetime import date, timedelta

async def main(days: int) -> None:
    end = date.today()
    start = end - timedelta(days=days)
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # Lun-Vie
            with SessionLocal() as s:
                stats = await sync_day(s, cur)
            print(f"{cur}: {stats}")
        cur += timedelta(days=1)
```

Tests: `test_sync_day_inserts_new`, `test_sync_day_updates_existing_by_hoja`, `test_sync_day_skips_weekend`.

Commit: `feat(sync): BORME daily ingester + backfill script + cron`.

**Smoke real:** ejecutar `python scripts/backfill_borme.py --days 5` y verificar que aparecen ~10-12k empresas en DB.

---

## Task 5: Autocomplete endpoint + pivot del formulario home

**Files:**
- Create: `app/web/routes_empresa.py` (HTMX endpoint)
- Modify: `app/web/templates/home.html` (pivot UX)
- Modify: `app/web/routes_search.py` (NIF opcional, razón social como key)
- Modify: `app/main.py` (mount nuevo router)
- Modify: `tests/unit/test_routes_search.py` (adaptar a la nueva forma)
- Create: `tests/unit/test_routes_empresa.py`

### Endpoint

```python
@router.get("/api/empresa/search", response_class=HTMLResponse)
def empresa_search(request: Request, q: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    """HTMX autocomplete. Devuelve HTML con lista de empresas que matchean `q` (slug ILIKE)."""
    if len(q.strip()) < 2:
        return HTMLResponse("")
    slug_q = slugify(q)
    rows = db.execute(
        select(Empresa)
        .where(Empresa.slug.like(f"{slug_q}%"))
        .order_by(Empresa.razon_social)
        .limit(10)
    ).scalars().all()
    return templates.TemplateResponse(request, "partials/empresa_options.html", {"empresas": rows, "q": q})
```

### Partial `partials/empresa_options.html`

```html
{% if empresas %}
<ul class="absolute bg-white border border-gray-200 rounded-lg w-full max-h-64 overflow-y-auto z-10 shadow-md">
  {% for e in empresas %}
  <li>
    <button type="button"
            class="w-full text-left px-3 py-2 hover:bg-green-50 text-sm border-b border-gray-100 last:border-b-0"
            onclick="selectEmpresa({{ e.id|tojson }}, {{ e.razon_social|tojson }}, {{ (e.provincia or '')|tojson }})">
      <div class="font-medium">{{ e.razon_social }}</div>
      <div class="text-xs text-gray-500">{{ e.provincia or '—' }}{% if e.domicilio %} · {{ e.domicilio[:60] }}{% endif %}</div>
    </button>
  </li>
  {% endfor %}
</ul>
{% else %}
<p class="text-xs text-gray-500 px-2">Sin coincidencias. Continúa tecleando o rellena los campos a mano.</p>
{% endif %}
```

### Pivot del form home.html

Cambia el orden y la prominencia. El **input principal** ahora es razón social con HTMX autocomplete. NIF queda como opcional al final.

Key changes en `home.html`:
- Mover el campo `razon_social` arriba, con `hx-get="/api/empresa/search"`, `hx-trigger="keyup changed delay:300ms"`, `hx-target="#empresa-suggestions"`.
- Añadir `<div id="empresa-suggestions" class="relative"></div>` justo debajo.
- Función JS `selectEmpresa(id, razon, provincia)` que: rellena el input razón social, oculta dropdown, y autoselecciona provincia en el `<select>`.
- NIF pasa a `optional` (sin `required`), placeholder `(opcional, para personalizar el informe)`.

### Backend ajustes

`POST /search`: `nif: Annotated[str | None, Form()] = None`. Si viene, validar; si está vacío, persistir Search con `nif=""` o NULL. La razón social ya era opcional; mantener.

### Tests

- `test_empresa_search_returns_matches`
- `test_empresa_search_empty_query_returns_empty`
- `test_empresa_search_slug_match` (Flexigobe matchea con o sin tildes/sufijo)
- `test_search_works_without_nif`
- `test_search_still_validates_nif_when_provided`

Commit: `feat(web): razón social autocomplete from BORME + NIF opcional`.

---

## Task 6: Admin /admin/empresas + review + merge

**Files:**
- Modify: `app/web/routes_admin.py` (GET /admin/empresas paginado)
- Create: `app/web/templates/admin/empresas.html`
- Modify: `tests/unit/test_routes_admin.py`

Página simple paginada con filtro por slug, provincia, estado. Columnas: razón social, provincia, estado, fecha constitución, último acto.

### Pasada final

- `pytest tests/ -v 2>&1 | tail -5` → 175+ tests passing.
- Smoke completo manual:
  - `/admin/empresas` lista empresas reales
  - Buscar "Flexigobe" en la home → autocomplete muestra `FLEXIGOBE SL · Barcelona`
  - Click selecciona la empresa, rellena provincia
  - Submit con o sin NIF → resultados
- Tag `v0.5.0-plan5`.
- Merge a `main` con `--no-ff`.

Commit: `feat(admin): /admin/empresas viewer with filters`.

---

## Cierre Plan 5

App con:
- **~225.000 empresas españolas** activas en local desde fuente oficial gratuita (BORME via BOE Datos Abiertos).
- **Autocompletado en el form** que rellena razón social + provincia al teclear.
- **NIF opcional** — sigue siendo útil para captura de leads, no obligatorio.
- **Cron diario** que mantiene la base BORME al día.

Es la respuesta más honesta posible al límite estructural español: no podemos resolver `NIF → razón social` porque AEAT no lo expone, pero **podemos resolver el problema desde otro ángulo** que es igualmente útil para el usuario.

**Lo que sigue pendiente** (queda para iteración / pivot a SaaS):
- Resolver el problema fiscal (NIF) → requiere pagar eInforma o digital cert
- Cuentas de usuario / login
- App móvil
- Pago / suscripciones
