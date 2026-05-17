# subvenciones-app

Buscador de subvenciones públicas para empresas españolas (Plan 1: BDNS + matching determinista).

## Pre-requisitos

- Python 3.12
- Postgres 14+

## Setup local

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Postgres local — crear usuario y DBs
psql postgres -c "CREATE USER subvenciones WITH PASSWORD 'subvenciones';" || true
psql postgres -c "CREATE DATABASE subvenciones OWNER subvenciones;" || true
psql postgres -c "CREATE DATABASE subvenciones_test OWNER subvenciones;" || true

# Variables de entorno
cp .env.example .env

# Migraciones
alembic upgrade head

# Arrancar el servidor
uvicorn app.main:app --reload --port 8000
```

Abrir http://localhost:8000.

## Sincronizar BDNS manualmente

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

El scheduler in-process lo ejecuta automáticamente cada día a las 03:00 (Europe/Madrid) mientras la app está corriendo.

**Nota:** la URL y los parámetros del endpoint BDNS en `app/sync/bdns_puller.py:fetch_page` están basados en una asunción del plan; en la primera ejecución contra BDNS real puede ser necesario ajustar el endpoint y los nombres de campos. Si la llamada falla, abrir https://www.infosubvenciones.es/bdnstrans/ y verificar el endpoint correcto.

## Tests

```bash
source .venv/bin/activate
pytest -v
```

## Estructura

```
app/
├── main.py              # FastAPI entrypoint + scheduler lifespan
├── config.py            # Pydantic Settings
├── db/                  # ORM models + sesión
├── lib/                 # NIF validator + CNAE catalog
├── sync/                # BDNS puller + APScheduler runner
├── matching/            # SQL filter + pre-rank determinista
└── web/                 # Rutas + templates Jinja2 + HTMX
```

## Documentación

- Diseño: [docs/superpowers/specs/2026-05-17-subvenciones-app-design.md](docs/superpowers/specs/2026-05-17-subvenciones-app-design.md)
- Plan 1 (este plan): [docs/superpowers/plans/2026-05-17-subvenciones-app-plan-1-cimientos.md](docs/superpowers/plans/2026-05-17-subvenciones-app-plan-1-cimientos.md)

## Próximos planes (post-Plan 1)

- Plan 2: enriquecimiento NIF (libreborme/OpenCorporates) + scoring Gemini + UE Funding & Tenders.
- Plan 3: captura email + PDF + alertas diarias por email vía Brevo.
- Plan 4: panel admin + rate limiting + deploy Railway + dominio.
