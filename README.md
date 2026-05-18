# subvenciones-app

Buscador de subvenciones públicas para empresas españolas (Plan 1: BDNS + matching determinista).

[![CI](https://github.com/<your-org-or-user>/subvenciones-app/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-org-or-user>/subvenciones-app/actions/workflows/ci.yml)

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

## Deploy en Railway

La app está lista para Railway sin tocar código. El flujo:

1. **Crear proyecto en Railway** (https://railway.com → "New Project").
2. **Añadir Postgres**: "+ New" → "Database" → "Add PostgreSQL". Railway inyecta automáticamente `DATABASE_URL` como variable.
3. **Conectar este repo**: "+ New" → "GitHub Repo" → seleccionar `subvenciones-app`.
4. **Variables de entorno** (en Settings → Variables del servicio web):

   | Variable | Valor | Notas |
   |----------|-------|-------|
   | `DATABASE_URL` | auto | Inyectado por el plugin Postgres |
   | `BASE_URL` | `https://<servicio>.up.railway.app` | URL pública; ajustar tras 1er deploy |
   | `GEMINI_API_KEY` | tu key | https://aistudio.google.com/app/apikey |
   | `BREVO_API_KEY` | (opcional) | Si vacío, emails se loguean — no se envían |
   | `ALERT_FROM_EMAIL` | `alertas@flexigobe.com` | Verificado en Brevo |
   | `ADMIN_USER` | `admin` | Cambia si quieres |
   | `ADMIN_PASS` | `<cadena segura>` | Genérala con `openssl rand -base64 24` |
   | `RATE_LIMIT_PER_HOUR` | `60` | Default; sube si quieres más permisivo |

5. **Deploy automático**: cada push a `main` despliega. Health-check en `/healthz`.
6. **Logs**: Settings → Deploys → View Logs (incluye logs estructurados de los syncs).
7. **(Opcional) Dominio personalizado**: Settings → Domains → Custom Domain → seguir instrucciones DNS.

### Limitaciones del deploy actual

- **Single worker**: APScheduler corre in-process y el rate limiter es per-worker. Si escalas a `RAILWAY_REPLICA_COUNT > 1` los crons se duplicarían y el rate limiter sería por instancia. Para escalar horizontalmente se necesita extraer el scheduler a un servicio aparte y mover el rate limiter a Redis.
- **WeasyPrint** depende de pango/cairo, instalados via `nixpacks.toml`. Si los logs muestran `ImportError: cannot load library libpango`, revisa que el build use Nixpacks (no Dockerfile custom).

### Despliegue local de prueba (sin Railway)

```bash
docker run -d --name pg -p 5432:5432 -e POSTGRES_USER=subvenciones -e POSTGRES_PASSWORD=subvenciones postgres:15
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Deploy con Docker (alternativa a Railway/Nixpacks)

Si prefieres Docker (Fly.io, Render, AWS, K8s...):

```bash
docker build -t subvenciones-app .
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql+psycopg://USER:PASS@host.docker.internal:5432/subvenciones" \
  -e GEMINI_API_KEY="..." \
  -e ADMIN_PASS="..." \
  -e BASE_URL="http://localhost:8000" \
  subvenciones-app
```

El Dockerfile es multi-stage (~250MB final). Incluye las libs de pango/cairo para WeasyPrint. Corre como usuario no-root.

## Documentación

- Diseño: [docs/superpowers/specs/2026-05-17-subvenciones-app-design.md](docs/superpowers/specs/2026-05-17-subvenciones-app-design.md)
- Plan 1 (este plan): [docs/superpowers/plans/2026-05-17-subvenciones-app-plan-1-cimientos.md](docs/superpowers/plans/2026-05-17-subvenciones-app-plan-1-cimientos.md)

## Próximos planes (post-Plan 1)

- Plan 2: enriquecimiento NIF (libreborme/OpenCorporates) + scoring Gemini + UE Funding & Tenders.
- Plan 3: captura email + PDF + alertas diarias por email vía Brevo.
- Plan 4: panel admin + rate limiting + deploy Railway + dominio.
