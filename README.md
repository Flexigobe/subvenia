# subvenciones-app

[![CI](https://github.com/<your-org-or-user>/subvenciones-app/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-org-or-user>/subvenciones-app/actions/workflows/ci.yml)

**Buscador gratuito de subvenciones públicas españolas y europeas para empresas.** Cruza el perfil de la empresa con la BDNS oficial, el portal EU Funding & Tenders y los datos del BORME para encontrar las convocatorias que mejor encajan.

- **Datos oficiales**: BDNS (Ministerio de Hacienda), BORME (Registro Mercantil), EU Funding & Tenders Portal.
- **Gratis y sin registro** para el usuario final.
- **Scoring inteligente** con Google Gemini + razón en lenguaje natural.
- **Alertas opcionales** por email vía Brevo cuando salen nuevas convocatorias afines.
- **RGPD-compliant**.

> Owner: [Flexigobe](mailto:comercial@flexigobe.com). Última versión: `v0.6.0-plan6`. 200+ tests automatizados.

## Pantallazos

> _Pendiente — añadir captures de la home, /subvenciones, /admin._

## Stack

- Python 3.12 + FastAPI + APScheduler (cron in-process)
- PostgreSQL 14+ (managed en Railway)
- SQLAlchemy 2 + Alembic
- Jinja2 + HTMX + Tailwind (sin SPA)
- WeasyPrint para PDF
- Google Gemini 2.0 Flash (free tier) para scoring LLM
- Brevo para email transaccional
- BORME XML + PDF (BOE Datos Abiertos, CC-BY-NC-ND 4.0)

## Pre-requisitos

- Python 3.12
- PostgreSQL 14+
- (Producción) cuenta Railway con Postgres managed

## Setup local

```bash
# 1. Clonar e instalar deps
git clone https://github.com/<your-org-or-user>/subvenciones-app.git
cd subvenciones-app
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Crear DBs locales
psql postgres -c "CREATE USER subvenciones WITH PASSWORD 'subvenciones';" || true
psql postgres -c "CREATE DATABASE subvenciones OWNER subvenciones;" || true
psql postgres -c "CREATE DATABASE subvenciones_test OWNER subvenciones;" || true

# 3. Variables de entorno
cp .env.example .env
# Editar .env con GEMINI_API_KEY, etc.

# 4. Migraciones
alembic upgrade head

# 5. Arrancar el servidor
uvicorn app.main:app --reload --port 8000
```

Abre [http://localhost:8000](http://localhost:8000).

Si no estableces `ADMIN_PASS`, la app genera credenciales aleatorias al arrancar y las loguea en stdout. Búscalas en los logs si quieres acceder a `/admin`.

## Cargar datos oficiales

### Sincronizar BDNS (Estado español)

```bash
source .venv/bin/activate
python -c "
import asyncio
from datetime import date, timedelta
from app.db.session import SessionLocal
from app.sync.bdns_puller import sync_all
with SessionLocal() as s:
    print(asyncio.run(sync_all(s, since=date.today() - timedelta(days=30))))
"
```

### Backfill BORME (empresas)

```bash
# 3 años en paralelo, resumible con state file
PYTHONPATH=. python scripts/backfill_borme.py --days 1095 --parallel 4
```

Para extender: `--days 3650` (10 años) reusa el state file y solo procesa los días nuevos.

### Crons automáticos

Mientras la app esté corriendo (`uvicorn`), APScheduler ejecuta:

- 03:00 — sync BDNS
- 03:30 — enricher detalle BDNS
- 03:45 — sync UE Funding & Tenders
- 04:00 (día 1 de mes) — sync catálogos BDNS
- 09:00 — alerts dispatcher (envía digests email)
- 10:30 — sync BORME del día
- cada 5 min — outbox flush

Todos en zona Europe/Madrid.

## Tests

```bash
pytest -v
```

> 200+ tests · cobertura unit + integración + smoke con httpx_mock.

## Deploy en Railway

Mira la guía paso a paso completa en [`docs/LAUNCH_CHECKLIST.md`](docs/LAUNCH_CHECKLIST.md).

Resumen rápido:

1. New Project en Railway → añadir Postgres + conectar el repo.
2. Establecer las variables de entorno (lista en `docs/LAUNCH_CHECKLIST.md`).
3. Railway detecta `railway.toml` + `nixpacks.toml` y construye automáticamente.
4. Healthcheck en `/healthz`, deploy automático en cada push a `main`.

### Limitaciones conocidas del deploy

- **Single worker**: APScheduler in-process + rate limiter en memoria. Si escalas a `RAILWAY_REPLICA_COUNT > 1` los crons se duplicarían. Solución futura: separar scheduler en servicio aparte + Redis para rate limit.
- **WeasyPrint** depende de pango/cairo (instalados via `nixpacks.toml`). Si los logs muestran `ImportError: cannot load library libpango`, revisa que el build use Nixpacks (no Dockerfile custom).

## Deploy con Docker (alternativa)

```bash
docker build -t subvenciones-app .
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+psycopg://USER:PASS@host.docker.internal:5432/subvenciones" \
  -e GEMINI_API_KEY="..." \
  -e ADMIN_PASS="..." \
  -e BASE_URL="http://localhost:8000" \
  subvenciones-app
```

Imagen multi-stage (~250 MB final), usuario no-root, incluye libs pango/cairo para WeasyPrint.

## Estructura del proyecto

```
app/
├── main.py                    # FastAPI entrypoint + lifespan + scheduler
├── config.py                  # Pydantic Settings
├── db/                        # ORM models + sessión + migraciones (Alembic)
├── lib/                       # NIF validator, CNAE catalog, PDF generator, email Brevo
├── enrich/                    # VIES (NIF enrichment)
├── matching/                  # Filtro SQL determinista + Gemini scorer + classifier finalidad
├── sync/                      # Pullers (BDNS, EU, BORME) + ingesters + scheduler runner
├── alerts/                    # Dispatcher email + outbox flusher
└── web/
    ├── routes_*.py            # Search, browse, admin, alerts, empresa, legal, seo, news, enrich
    └── templates/             # Jinja2 + HTMX + Tailwind
data/
└── cnae_2009.json             # Catálogo CNAE-2009
docs/
├── superpowers/specs/         # Diseño funcional
├── superpowers/plans/         # Planes 1-7
├── LAUNCH_CHECKLIST.md        # Guía paso a paso para producción
└── audits/                    # Auditorías técnicas
migrations/versions/           # Alembic migrations 0001-0004
scripts/                       # Backfill BORME, reclassify finalidad
tests/                         # Unit + integration (200+ tests)
```

## Documentación

- Diseño funcional: [`docs/superpowers/specs/2026-05-17-subvenciones-app-design.md`](docs/superpowers/specs/2026-05-17-subvenciones-app-design.md)
- Planes de desarrollo: [`docs/superpowers/plans/`](docs/superpowers/plans/)
- Launch checklist: [`docs/LAUNCH_CHECKLIST.md`](docs/LAUNCH_CHECKLIST.md)

## Licencia

Código propietario de Flexigobe. Los datos públicos consumidos (BDNS, BORME, EU Funding & Tenders) se utilizan bajo las condiciones de reutilización de cada organismo.

## Contacto

- Email: [comercial@flexigobe.com](mailto:comercial@flexigobe.com)
- Issues: [GitHub Issues](https://github.com/<your-org-or-user>/subvenciones-app/issues)
