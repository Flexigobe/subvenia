# Plan 4 — Panel admin + producción

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Cerrar el MVP: panel admin protegido para que Victor vea sus leads y métricas, rate limiting, configuración para deploy en Railway con dominio, observabilidad básica, y un fix puntual del clasificador Gemini que quedó pendiente de Plan 3.

**Architecture:** Mismo patrón que Plans 1-3. Nuevo módulo `app/web/routes_admin.py` con HTTP Basic en cada ruta. Nueva tabla NO se añade (rate limiting va en memoria, sliding window). Settings añade `admin_user`, `admin_pass`, `rate_limit_per_hour`. Para deploy: `railway.toml` + `Procfile`-style config + `runtime.txt` (Python 3.12).

**Tech Stack:** Sin nuevas deps. Reusa todo lo que ya hay. Para CSV export: `csv` stdlib.

**Pre-requisitos:**
- ✅ Plan 3 mergeado.
- ✅ Postgres + 6394 BDNS + EU records.
- ⏳ `ADMIN_USER` + `ADMIN_PASS` — se generan defaults aleatorios al arrancar (Victor cambia en `.env` local + Railway dashboard en prod).
- ⏳ Cuenta Railway con servicio Postgres y servicio web — Victor ya tiene una; sólo conectaría el repo.
- ⏳ Dominio — opcional; Railway da `<servicio>.up.railway.app` gratis.

---

## Tasks

### Task 1: Admin auth + base layout

**Files:**
- Create: `app/web/routes_admin.py`
- Create: `app/web/templates/admin/_base.html` (layout admin extiende base.html)
- Modify: `app/config.py` (añadir `admin_user`, `admin_pass`, secret seguro por defecto)
- Modify: `app/main.py` (montar `admin_router`)
- Modify: `.env.example`
- Create: `tests/unit/test_routes_admin.py`

**Specs:**

- HTTP Basic auth con `secrets.compare_digest` para evitar timing attacks.
- Si `admin_pass=""` en producción → bloquear acceso al panel completo (devolver 503 con mensaje "admin disabled, set ADMIN_PASS").
- En dev: si no hay valores en `.env`, generar valores aleatorios al arrancar y loggearlos en stdout para que Victor los copie (NO los persiste — cada reinicio cambian, así que conviene fijarlos en `.env`).
- Reusable dependency `require_admin` que se aplica a cada ruta admin.

Template `admin/_base.html`: extiende base.html con nav lateral "Dashboard | Búsquedas | Suscripciones | Outbox | Salud" y un header con "Cerrar sesión" (que en HTTP Basic no es trivial — basta con redirigir a un endpoint que devuelve 401 nuevo).

Tests:
- `test_admin_redirects_to_basic_auth_when_no_creds`
- `test_admin_rejects_wrong_password`
- `test_admin_accepts_correct_credentials`
- `test_admin_returns_503_when_password_empty`

Commit: `feat(admin): HTTP Basic auth + admin layout`.

---

### Task 2: Dashboard + métricas

**Files:**
- Modify: `app/web/routes_admin.py` (GET /admin → dashboard)
- Create: `app/web/templates/admin/dashboard.html`
- Modify: `tests/unit/test_routes_admin.py`

**Métricas a calcular** (consultas SQL agregadas):
- Búsquedas: 24h / 7d / 30d (counts simples)
- Top 10 finalidades buscadas
- Top 10 CNAEs buscados
- Distribución por tamaño y provincia
- Conversión: % de búsquedas que dejaron email
- Estado del outbox: pending / sent / dead totals + media de tiempo de envío
- Estado del sync: último timestamp de sync por source (BDNS, EU), conteos
- Suscripciones activas / inactivas / total

Renderizar todo en una tarjeta-grid sencilla con grandes números y mini tablas.

Tests:
- `test_dashboard_shows_counts`
- `test_dashboard_handles_empty_db_gracefully`

Commit: `feat(admin): dashboard with key metrics`.

---

### Task 3: Admin tablas (searches + subscriptions) + CSV export

**Files:**
- Modify: `app/web/routes_admin.py` (GET /admin/searches, /admin/searches.csv, /admin/subscriptions, /admin/subscriptions/{id}/deactivate)
- Create: `app/web/templates/admin/searches.html`
- Create: `app/web/templates/admin/subscriptions.html`

**Searches**:
- Paginada (20/page), ordenada por `created_at desc`.
- Columnas: created_at, NIF, razon_social, CNAE, tamano, provincia, finalidad, email (si dejó).
- Filtros opcionales (query params): `?since=YYYY-MM-DD`, `?has_email=true`.
- Botón "Exportar CSV" → `/admin/searches.csv` con TODOS los registros (respetando filtros), formato UTF-8 con BOM.

**Subscriptions**:
- Tabla simple con created_at, email, perfil (json minificado), active, last_sent_at.
- Botón por fila "Desactivar" → `POST /admin/subscriptions/{id}/deactivate` (form, no HTMX para mantener simple).

Tests:
- `test_admin_searches_lists_paginated`
- `test_admin_searches_csv_returns_correct_format`
- `test_admin_searches_csv_respects_filters`
- `test_admin_subscriptions_lists`
- `test_admin_subscriptions_deactivate_works`

Commit: `feat(admin): searches + subscriptions tables with CSV export`.

---

### Task 4: Admin sync-now button + outbox viewer

**Files:**
- Modify: `app/web/routes_admin.py` (GET /admin/sync, POST /admin/sync/bdns, POST /admin/sync/eu, POST /admin/sync/enricher, POST /admin/outbox/flush, GET /admin/outbox)
- Create: `app/web/templates/admin/sync.html`
- Create: `app/web/templates/admin/outbox.html`

**Sync page**: muestra el estado de los últimos runs (último timestamp por job, último resultado en JSON) y 4 botones de "Forzar ahora":
- Forzar BDNS sync
- Forzar BDNS enricher
- Forzar EU sync
- Forzar flush outbox

Estos POSTs encolan la tarea (no la ejecutan inline) usando `asyncio.create_task` o un simple `BackgroundTasks` de FastAPI. Devuelve "Tarea encolada — refresca en 30s para ver el resultado en logs".

**Outbox page**: lista paginada de emails con su status (pending/sent/dead), attempts, last_error. Botón "Reintentar todos los dead" que los resetea a `attempts=0, status=pending`.

Tests:
- `test_admin_sync_bdns_button_runs_task`
- `test_admin_outbox_lists_messages`
- `test_admin_outbox_retry_dead_resets_attempts`

Commit: `feat(admin): force-sync buttons + outbox viewer with retry-dead`.

---

### Task 5: Rate limiting middleware

**Files:**
- Create: `app/web/rate_limit.py` (in-memory sliding window)
- Modify: `app/main.py` (registrar middleware)
- Modify: `app/config.py` (`rate_limit_per_hour: int = 60`)
- Create: `tests/unit/test_rate_limit.py`

**Spec:**
- Aplica solo a `POST /search` (las consultas costosas).
- Llave: SHA-256(ip + user-agent[:50]) para evitar PII pero conservar discriminación.
- Sliding window de 1h con 60 requests por defecto.
- Almacenamiento en memoria (dict con timestamps).
- Cuando se excede: devolver 429 con HTML amistoso ("Vaya, has hecho muchas búsquedas. Vuelve en N minutos."). Header `Retry-After: <seconds>`.
- Bypass para rutas admin (asume IPs de confianza); bypass también para rutas estáticas y health.

Tests:
- `test_rate_limit_allows_under_threshold`
- `test_rate_limit_blocks_over_threshold`
- `test_rate_limit_returns_429_with_retry_after`
- `test_rate_limit_does_not_apply_to_get_endpoints`
- `test_rate_limit_window_slides_correctly`

Commit: `feat(web): rate limiting middleware for POST /search`.

---

### Task 6: Railway deployment config

**Files:**
- Create: `railway.toml`
- Create: `Procfile`
- Create: `nixpacks.toml` (si Railway lo requiere para WeasyPrint deps; pango/cairo)
- Modify: `README.md` (sección "Deploy en Railway")

**railway.toml** mínimo:
```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/healthz"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 5
```

**nixpacks.toml** (para incluir pango/cairo para WeasyPrint):
```toml
[phases.setup]
aptPkgs = ["python3-cffi", "python3-brotli", "libpango-1.0-0", "libpangoft2-1.0-0", "libharfbuzz0b"]
```

**README** sección "Deploy en Railway":
1. Crear proyecto Railway, añadir servicio Postgres (genera `DATABASE_URL`).
2. Conectar este repo como servicio web.
3. Variables a establecer en Railway dashboard:
   - `DATABASE_URL` (auto-set por el plugin Postgres)
   - `GEMINI_API_KEY` (de aistudio.google.com)
   - `BREVO_API_KEY` (cuando esté lista)
   - `ALERT_FROM_EMAIL`
   - `ADMIN_USER`, `ADMIN_PASS`
   - `BASE_URL` (= URL pública)
   - `RATE_LIMIT_PER_HOUR=60`
4. Deploy → URL auto-generada en `<servicio>.up.railway.app`.
5. (Opcional) conectar dominio personalizado en Railway → Settings → Domains.

Commit: `chore(deploy): Railway config + deploy docs`.

---

### Task 7: Observabilidad + healthz enriquecido

**Files:**
- Modify: `app/main.py` (`/healthz` devuelve DB connectivity check + scheduler status)
- Create: `app/web/routes_admin.py` route `GET /admin/health` (HTML view) o reusar la página de sync.
- Modify: `app/sync/runner.py` (cada job loggea `[SYNC] {name}: {result}` estructurado con extra dict para parsing)
- Modify: `app/alerts/dispatcher.py` (`flush_outbox` envía email a `ALERT_ADMIN_EMAIL` si hay >0 records en estado `dead`)

`/healthz`:
```python
{
  "status": "ok" | "degraded",
  "db": "ok" | "error",
  "scheduler": "running" | "stopped",
  "checks": {"db_latency_ms": 5}
}
```

Logging estructurado: usar `logger.info("BDNS sync done", extra={"sync_name": "bdns", "stats": stats})` para que Railway pueda parsear.

Test:
- `test_healthz_reports_db_status`
- `test_healthz_reports_scheduler_status`

Commit: `feat(observability): enriched /healthz + structured sync logging + admin alert on dead emails`.

---

### Task 8: Fix Gemini reclassify malformed JSON

**Files:**
- Modify: `app/matching/finalidad_classifier.py` (debug + parser resiliente)
- Modify: `scripts/reclassify_finalidad.py` (loggea respuestas crudas en `/tmp/reclassify_debug.log`)

**Bug observado en Plan 3 Task 2:**
El script `reclassify_finalidad.py` devolvió `improved: 0` aunque corrió contra 527 records. La pista del log: `}\n]); falling back` — sugiere que Gemini respondió con algo como `<código JS> ... ]); ...` (parece envoltorio de print/console.log).

**Steps:**
1. Añadir debug logging temporal en `_strip_markdown_fences` + después del parse: loggear el `text` raw a un archivo de debug si JSON.parse falla.
2. Probar con 5 records manualmente. Capturar el texto exacto que Gemini devuelve.
3. Robustecer el parser: regex para extraer el primer `[...]` array del texto (`re.search(r'\[.*?\]', text, re.DOTALL)`), evitando wrappers tipo "Here's the result: [...]" o `print([...])`.
4. Re-ejecutar reclassify y verificar `improved > 0`.

Tests:
- `test_classify_extracts_array_from_wrapped_response` (mock devuelve `"Result: [\"i+d\"]"`)
- `test_classify_extracts_array_from_print_wrapper` (mock devuelve `"print([\"i+d\"])"`)

Commit: `fix(matching): robust JSON extraction in finalidad classifier`.

---

## Pasada final + merge

- [ ] `pytest tests/ -v 2>&1 | tail -5` muestra **140+ tests passing** (126 previos + ~14 nuevos).
- [ ] Smoke completo manual:
  - `/admin` con credenciales correctas → dashboard
  - `/admin/searches.csv` descarga CSV válido
  - 60 búsquedas rápidas → 61ª devuelve 429
  - `/healthz` devuelve JSON con `db: ok`, `scheduler: running`
- [ ] Tag: `git tag -a v0.4.0-plan4 -m "Plan 4 complete: admin panel + rate limiting + Railway deploy config"`.
- [ ] Merge a `main` con `--no-ff`.
- [ ] Tras merge: Victor abre Railway, conecta repo, mete env vars, deploy.

## Cierre

Al terminar Plan 4 la app es **deployable y operable**:
- Panel admin con métricas, leads exportables a CSV, sync forzado, outbox visible.
- Rate limiting protege contra abuso.
- Healthcheck completo para Railway / monitoring.
- Configuración de despliegue lista — `git push` y Railway construye.

**Lo que NO trae (futuro)**:
- Cuentas de usuario / login para empresas (no es un SaaS aún).
- App móvil nativa.
- Tramitación asistida.
- Cobertura de subvenciones fuera España + UE.

Ese alcance ya entraría en **Plan 5** o un pivot al modelo de pago.
