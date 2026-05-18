# Launch checklist — subvenciones-app

Guía paso a paso para Victor para desplegar la app a producción en Railway con dominio propio. Pensada como **checklist secuencial**: marca cada caja al completar.

---

## Pre-flight (antes del deploy)

### Credenciales y servicios

- [ ] **API key de Google Gemini** — generada en https://aistudio.google.com/app/apikey. **Hoy está a free tier limit=0**; pedir cuota o añadir billing antes del lanzamiento.
- [ ] **API key de Brevo** — desde https://app.brevo.com/settings/keys/api. Crear key con permiso "Transactional emails".
- [ ] **Email "from" verificado en Brevo** — añadir `alertas@flexigobe.com` (o el alias elegido) en Brevo → Senders & IP → "Add a sender" y validar el email de confirmación.
- [ ] **Cuenta Railway** con plan que permita servicio web + Postgres managed (~5€/mes).
- [ ] **Dominio elegido y comprado** (opcional al inicio — Railway da `<servicio>.up.railway.app`). Sugerencias: `subvenciones.flexigobe.com` (subdominio del existente) o `tusubvencion.es` / `subvenciones.app` (nuevo).

### Repo en GitHub

- [ ] Push del repo a GitHub como `subvenciones-app` (privado o público).
- [ ] Reemplazar `<your-org-or-user>` en el badge de CI del `README.md` por el username/org real.
- [ ] Primer push dispara CI automáticamente — verificar que pasa en verde.

### Local: estado de los datos

- [ ] Backfill BORME completado (`python3 -c "import json; print(len(json.load(open('/tmp/borme_backfill_state.json'))['completed']), 'fechas')"`).
- [ ] Cobertura de la base `empresa`: `SELECT COUNT(*) FROM empresa` debe estar al menos en 200k.
- [ ] BDNS y EU sincronizadas: `SELECT source, COUNT(*) FROM subvencion GROUP BY source`.

---

## Deploy en Railway

### Paso 1 — Crear proyecto

- [ ] Login en https://railway.com.
- [ ] **New Project** → **Empty Project**.
- [ ] Renombrar el proyecto a `subvenciones-app` (Settings → Project name).

### Paso 2 — Añadir Postgres

- [ ] **+ New** → **Database** → **Add PostgreSQL**.
- [ ] Verificar que el servicio Postgres genera la variable `DATABASE_URL` (Settings → Variables del servicio Postgres → debe haber `DATABASE_PUBLIC_URL` y `DATABASE_URL`).
- [ ] (Opcional) habilitar backups diarios en Settings → Backups.

### Paso 3 — Conectar el repo web

- [ ] **+ New** → **GitHub Repo** → seleccionar `subvenciones-app`.
- [ ] Railway detecta el `nixpacks.toml` y el `railway.toml` automáticamente.
- [ ] En **Settings → Build**, verificar:
  - Builder: `NIXPACKS`
  - Start command: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers`
  - Health check path: `/healthz`

### Paso 4 — Variables de entorno

En **Settings → Variables del servicio web**, añadir:

| Variable | Valor | Notas |
|----------|-------|-------|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Reference al servicio Postgres (Railway autocompleta) |
| `BASE_URL` | `https://subvenciones-app-production.up.railway.app` | Cambiar al dominio real cuando se conecte |
| `SEO_CANONICAL_ORIGIN` | (igual que BASE_URL) | Para sitemap y canonical |
| `GEMINI_API_KEY` | `AIza...` | de aistudio.google.com |
| `GEMINI_MODEL` | `gemini-2.0-flash` | (default, opcional) |
| `BREVO_API_KEY` | `xkeysib-...` | de app.brevo.com |
| `ALERT_FROM_EMAIL` | `alertas@flexigobe.com` | verificado en Brevo |
| `ALERT_ADMIN_EMAIL` | `comercial@flexigobe.com` | (opcional) recibe avisos cuando emails mueren |
| `ADMIN_USER` | `admin` | o el que prefieras |
| `ADMIN_PASS` | `<random 24 chars>` | `openssl rand -base64 24` |
| `RATE_LIMIT_PER_HOUR` | `60` | default |
| `PLAUSIBLE_DOMAIN` | `subvenciones.flexigobe.com` | (opcional) — déjalo vacío si no quieres analítica |
| `LOG_LEVEL` | `INFO` | default |

### Paso 5 — Deploy inicial

- [ ] Settings → **Deploy** → click en "Redeploy" (o esperar al push automático).
- [ ] Ver logs en **Observability**: debe aparecer `alembic upgrade head` (migrations 1-4) y luego `Uvicorn running on http://0.0.0.0:PORT`.
- [ ] Verificar que el healthcheck pasa: la URL pública responde 200 en `/healthz` con `{"status": "ok", "db": "ok", "scheduler": "running"}`.

### Paso 6 — Cargar datos iniciales

Las cron jobs se ejecutan a sus horas programadas. Para no esperar, lanza un sync manual desde el panel admin:

- [ ] Visita `https://<tu-url>/admin/sync` y haz login con las credenciales admin.
- [ ] Click en **BDNS** — encola sync inmediato.
- [ ] Click en **CATALOGS** — carga los catálogos taxonómicos.
- [ ] Espera 2-3 minutos, refresca el dashboard `/admin` y verifica que aparecen subvenciones BDNS.
- [ ] **NO ejecutar BORME desde admin en producción** — son ~90 min de descarga. En lugar de eso: subir los datos locales a Postgres remoto (ver paso 8).

### Paso 7 — Conectar dominio personalizado (opcional)

- [ ] Settings → **Domains** → **Custom Domain** → introducir `subvenciones.flexigobe.com` (o el dominio comprado).
- [ ] Copiar el CNAME que Railway proporciona y añadirlo en tu DNS provider:
  - Si subdominio: CNAME `subvenciones` → `subvenciones-app-production.up.railway.app`.
  - Si dominio raíz: ALIAS / ANAME apuntando a la URL de Railway (depende del provider).
- [ ] Esperar propagación DNS (5-60 min).
- [ ] HTTPS automático con Let's Encrypt (Railway lo gestiona).
- [ ] **Actualizar variables** `BASE_URL` y `SEO_CANONICAL_ORIGIN` con el dominio nuevo. Redeploy.

### Paso 8 — Migrar los datos locales (BORME backfilled)

Tu Mac tiene ~200-300k empresas en local. Migrar a Postgres de Railway en lugar de re-sincronizar desde cero:

- [ ] En tu Mac:
  ```bash
  # Conseguir DATABASE_URL pública de Railway
  RAILWAY_DB_URL=$(railway variables --service Postgres --plain | grep DATABASE_PUBLIC_URL | cut -d= -f2-)

  # Dump local de la tabla empresa
  pg_dump postgresql://subvenciones:subvenciones@localhost:5432/subvenciones \
    --table=empresa --data-only --column-inserts \
    > /tmp/empresa.sql

  # Restore en Railway
  psql "$RAILWAY_DB_URL" < /tmp/empresa.sql
  ```
- [ ] Verificar: `SELECT COUNT(*) FROM empresa` debe coincidir.

(Si no tienes Railway CLI, puedes copiar la URL pública desde el dashboard.)

---

## Post-flight (después del deploy)

### Smoke tests manuales

- [ ] Visitar `https://<tu-url>/` — formulario carga, hero visible.
- [ ] Teclear una razón social — autocomplete devuelve sugerencias.
- [ ] Seleccionar una y hacer búsqueda — devuelve subvenciones con score.
- [ ] Visitar `/subvenciones` — listado completo se carga, búsqueda y filtros funcionan.
- [ ] Visitar `/noticias`, `/como-funciona`, `/privacidad`, `/terminos` — todos responden 200.
- [ ] Visitar `/sitemap.xml` — XML válido.
- [ ] Visitar `/admin` — pide credenciales, dashboard funciona con tus credenciales.
- [ ] Probar el flujo de suscripción email (deja un email tuyo al final de una búsqueda). Debe llegar el email de bienvenida (asumiendo BREVO_API_KEY válido).
- [ ] Probar el link de unsubscribe en el email recibido.

### SEO / discovery

- [ ] Enviar `https://<tu-url>/sitemap.xml` a Google Search Console.
- [ ] Verificar metadata: pegar la URL en https://www.opengraph.xyz/ — debe mostrar título y descripción correctos.
- [ ] Configurar Plausible (si elegiste habilitarlo): crear sitio en plausible.io o instalar self-hosted, copiar el dominio en `PLAUSIBLE_DOMAIN`.

### Monitoring

- [ ] Configurar un uptime check externo apuntando a `/healthz` (uptimerobot.com gratis, o BetterStack).
- [ ] Revisar Railway → Observability → Logs cada día las primeras 2 semanas.
- [ ] Programar revisión semanal del dashboard `/admin` para ver:
  - Búsquedas (¿tráfico real?)
  - Conversión email (¿gente se suscribe?)
  - Outbox (¿hay emails dead?)
  - Estado del sync (¿algún job ha fallado?)

### Plan B — rollback

Si algo va muy mal:

- [ ] Railway → Deployments → seleccionar deploy anterior verde → **Redeploy**.
- [ ] Si la migración nueva rompe algo: `alembic downgrade -1` desde un shell en Railway.

---

## Comunicación / Lanzamiento

### Día del lanzamiento

- [ ] Publicar en LinkedIn (cuenta personal + Flexigobe). Mensaje sugerido en `docs/LAUNCH_POST.md`.
- [ ] Compartir en Twitter/X.
- [ ] Email a contactos de Flexigobe ofreciendo el servicio.
- [ ] Anotar en `docs/POST_LAUNCH_LOG.md` cualquier feedback recibido las primeras 48h.

### Mantenimiento mensual

- [ ] Revisar logs y errores acumulados.
- [ ] Backup manual de Postgres antes de cambios mayores.
- [ ] Actualizar `Plan 5` BORME backfill con días nuevos (`PYTHONPATH=. python scripts/backfill_borme.py --days 30 --parallel 4` — solo los nuevos por el state file).
- [ ] Revisar la cuota Gemini — si la subes a paid, cambiar billing.
- [ ] Renovar dominio cuando toque.

---

## Cosas para iterar después

- Investigar BORME detail PDFs para extraer más datos (capital cambios, ampliaciones).
- Probar Algolia/Meilisearch para autocomplete más rápido si el SQL ILIKE se queda corto.
- Migrar el rate-limiter en memoria a Redis si escalas a multi-worker.
- Migrar APScheduler a un worker dedicado.
- Añadir cuentas de usuario y modelo SaaS si validas demanda.
