# Diseño · Buscador de subvenciones para empresas españolas

- **Fecha:** 2026-05-17
- **Autor del diseño:** Claude (sesión de brainstorming con Victor Gómez, Flexigobe)
- **Estado:** Diseño aprobado · pendiente de plan de implementación
- **Repositorio:** `subvenciones-app` (proyecto nuevo, separado del workspace `CLIENTE-POTENCIAL`)

---

## 1. Contexto y motivación

Las empresas españolas (especialmente PYMES y autónomos) tienen dificultades para descubrir qué subvenciones públicas pueden pedir. La información está dispersa en la **BDNS** (Base de Datos Nacional de Subvenciones), portales autonómicos y en el portal europeo **Funding & Tenders Portal**. Las consultoras cobran por curar esa información.

Este proyecto construye una herramienta web que, dado un NIF español y unas pocas preguntas adicionales, devuelve una lista personalizada y rankeada de subvenciones a las que la empresa podría optar, con sus requisitos, importes, plazos y enlaces a la convocatoria oficial.

---

## 2. Objetivos y no-objetivos

### Objetivos (MVP)

- Que cualquier empresa española pueda, en menos de **2 minutos**, descubrir las subvenciones públicas (estatales, autonómicas y europeas) más relevantes para su perfil.
- Capturar el perfil de las empresas que buscan (NIF + CNAE + tamaño + provincia + finalidad de financiación) como base de leads cualificados.
- Ofrecer **alertas por email opcionales** cuando salgan nuevas convocatorias afines al perfil del usuario.
- Construir base técnica reutilizable para evolucionar a producto SaaS de pago si la herramienta gratuita valida la demanda.

### No-objetivos (fuera de MVP)

- Tramitación de subvenciones (solo descubrimiento + enlace a convocatoria oficial).
- Cobertura "mundial" más allá de España + UE.
- Cuentas de usuario / login (se hará al pasar a SaaS).
- Enriquecimiento automático de pago (eInforma/Axesor). Se posponen a fase SaaS.
- App móvil nativa (la web es responsive y suficiente).
- Multi-idioma. El MVP es solo en español.

---

## 3. Usuarios y casos de uso

### Usuario principal

Responsable de PYME / autónomo / financiero / consultor externo que evalúa qué ayudas puede solicitar una empresa específica.

### Caso de uso típico

1. Llega a la web (vía SEO, ads, o boca-oreja).
2. Introduce su NIF/CIF.
3. La app intenta auto-rellenar razón social, CNAE y provincia con fuentes públicas; si no, los rellena él (autocomplete sobre el catálogo CNAE-2009).
4. Selecciona tamaño de empresa (microempresa / pequeña / mediana / grande) y finalidad/es de la financiación (digitalización, I+D, contratación, eficiencia energética, internacionalización, formación...).
5. Pulsa "Buscar".
6. Ve las **3 subvenciones más recomendadas** destacadas como cards grandes con score, importe y plazo, más el resto en tabla compacta debajo.
7. Hace clic en una → ve detalle completo + enlace a convocatoria oficial.
8. Opcionalmente: deja su email para recibir un PDF con el informe + alertas de nuevas convocatorias que encajen con su perfil.

---

## 4. Alcance funcional

### MVP incluye

- Búsqueda con NIF + perfil (CNAE, tamaño, provincia, finalidad).
- Auto-enrichment "best-effort" con libreborme.net y OpenCorporates (gratis), con fallback a entrada manual.
- Validación de NIF/CIF/NIE con checksum oficial AEAT.
- Cobertura de **BDNS** (España: estatal + autonómicas + locales) y **EU Funding & Tenders Portal**.
- Sincronización diaria con ambas APIs.
- Matching híbrido: filtro SQL determinista + ranking con LLM (Claude Haiku 4.5).
- Página de resultados con layout **híbrido**: top 3 destacadas + tabla compacta del resto.
- Página de detalle por subvención con enlace al BOE / convocatoria oficial.
- Captura opcional de email al final con envío de PDF del informe.
- Sistema de alertas por email diarias para usuarios suscritos (cuando aparecen nuevas convocatorias afines).
- Panel admin protegido (`/admin`) con métricas, listado de búsquedas y suscripciones, exportable a CSV.
- Despliegue en Railway con dominio propio (cuando se compre).

### Fuera de MVP (futuro)

- Cuentas de usuario y plan SaaS de pago.
- Enriquecimiento automático de pago (eInforma/Axesor).
- Cobertura adicional fuera UE (mundo).
- Tramitación asistida.
- Multi-idioma.

---

## 5. Arquitectura

### Stack

| Capa | Tecnología | Motivo |
|------|------------|--------|
| Backend | Python 3.12 + FastAPI | Consistencia con el resto de proyectos de Flexigobe |
| Templating | Jinja2 + HTMX + Alpine.js | Server-rendered, sin necesidad de SPA, simple y rápido |
| CSS | Tailwind CSS | Estándar, productividad alta |
| Base de datos | PostgreSQL (managed Railway) | Compatible con plan de 5€ existente |
| ORM | SQLAlchemy + Alembic | Migraciones controladas |
| Scheduler | APScheduler (in-process) | Cron diario sin servicio extra |
| LLM | Anthropic Claude Haiku 4.5 | Rápido y barato (~0.001€/búsqueda) |
| Email | Brevo | Plan gratuito (300 emails/día), reutilizable |
| Hosting | Railway (~5€/mes existente) | Ya contratado |
| Dominio | Pendiente de compra (~10€/año) | Se conecta a Railway con HTTPS automático |

### Servicios externos

| Servicio | Uso | Coste | Auth |
|----------|-----|-------|------|
| BDNS (infosubvenciones.es) | Sync diario de subvenciones España | Gratis | Sin auth |
| EU Funding & Tenders Portal API | Sync diario de programas UE | Gratis | Sin auth |
| libreborme.net | Enrichment NIF (razón social, CNAE) | Gratis (rate-limit) | Sin auth |
| OpenCorporates | Enrichment NIF backup | Gratis (500/mes) | API key gratis |
| Anthropic API | Scoring de subvenciones con LLM | ~5-10€/mes | API key |
| Brevo | Emails transaccionales + alertas | Free tier | API key |

### Coste mensual estimado

| Concepto | Mes 1-2 (~20 búsq/día) | Mes 3-6 (~100 búsq/día) |
|----------|------------------------|--------------------------|
| Railway (web + Postgres) | 5€ (ya contratado) | 5€ |
| Anthropic Claude Haiku (3 llamadas batch/búsqueda con cache 7d) | ~3-5€ | ~20-30€ |
| Brevo (free tier 300 emails/día) | 0€ | 0€ |
| Dominio (~10€/año amortizado) | ~0.85€ | ~0.85€ |
| **Total** | **~9-11€/mes** | **~25-35€/mes** |

El coste LLM escala lineal con tráfico. La caché de scoring por `(empresa_perfil_hash, subvencion_id)` con TTL 7d mitiga repeticiones. Si el tráfico se dispara antes de monetizar, primera palanca = reducir candidatos a 20 (2 batches) o ampliar TTL de cache.

### Diagrama lógico

```
                 ┌─────────────────────┐
                 │   Usuario navegador │
                 └──────────┬──────────┘
                            │ HTTPS
                            ▼
┌────────────────────────────────────────────────┐
│        FastAPI app (Railway, single dyno)      │
│  ┌──────────────┐   ┌──────────────────────┐   │
│  │ Web routes   │   │ Admin routes (auth) │   │
│  │ /  /search   │   │ /admin/*            │   │
│  │ /subsidy/{id}│   └──────────────────────┘   │
│  │ /api/enrich  │   ┌──────────────────────┐   │
│  │ /api/subscribe   │ APScheduler           │   │
│  └──────────────┘   │  - sync_bdns 03:00    │   │
│                     │  - sync_eu   03:30    │   │
│                     │  - alerts    09:00    │   │
│                     └──────────────────────┘   │
└────────┬──────────────┬──────────────┬─────────┘
         │              │              │
         ▼              ▼              ▼
   ┌──────────┐  ┌──────────────┐  ┌─────────────────┐
   │ Postgres │  │ Claude Haiku │  │ APIs externas   │
   │ (Railway)│  │ (scoring)    │  │ BDNS · EU · enrich │
   └──────────┘  └──────────────┘  └─────────────────┘
                                    │
                                    ▼
                              ┌──────────┐
                              │  Brevo   │
                              │ (emails) │
                              └──────────┘
```

---

## 6. Estructura del proyecto

```
subvenciones-app/
├── app/
│   ├── main.py                # FastAPI entrypoint + lifespan + scheduler init
│   ├── config.py              # pydantic Settings (env vars)
│   │
│   ├── web/                   # Capa HTTP
│   │   ├── routes_search.py
│   │   ├── routes_enrich.py
│   │   ├── routes_alerts.py
│   │   ├── routes_admin.py
│   │   └── templates/         # Jinja2 + HTMX
│   │       ├── base.html
│   │       ├── home.html
│   │       ├── results.html
│   │       ├── subsidy_detail.html
│   │       └── admin/*
│   │
│   ├── enrich/                # NIF → CNAE/tamaño/provincia
│   │   ├── libreborme.py
│   │   ├── opencorporates.py
│   │   └── service.py
│   │
│   ├── sync/                  # Workers
│   │   ├── bdns_puller.py
│   │   ├── eu_puller.py
│   │   └── runner.py          # APScheduler
│   │
│   ├── matching/
│   │   ├── filter.py          # filtro SQL determinista
│   │   ├── scorer_llm.py      # ranking con Claude Haiku
│   │   └── service.py
│   │
│   ├── alerts/
│   │   └── dispatcher.py
│   │
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── migrations/        # Alembic
│   │
│   └── lib/
│       ├── nif_validator.py
│       ├── cnae_catalog.py
│       ├── pdf_generator.py   # WeasyPrint
│       └── email_brevo.py
│
├── data/
│   └── cnae_2009.json         # Catálogo oficial estático (~800 códigos)
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── pyproject.toml
├── railway.toml
├── .env.example
├── .gitignore
└── README.md
```

---

## 7. Modelo de datos

### `subvencion`

Convocatoria de subvención, sincronizada desde BDNS o EU.

| Columna | Tipo | Notas |
|---------|------|-------|
| id | uuid PK | Generado por la app |
| source | enum('bdns', 'eu') | Origen |
| external_id | text | ID en la fuente origen |
| titulo | text | |
| organismo | text | Quien convoca |
| ambito | enum('estatal', 'autonomico', 'local', 'ue') | |
| ccaa | text nullable | Si aplica |
| fecha_inicio | date | |
| fecha_fin | date nullable | |
| importe_total | numeric nullable | Importe global de la convocatoria |
| importe_max_beneficiario | numeric nullable | Tope por empresa |
| porcentaje | numeric nullable | % de subvención sobre el proyecto |
| beneficiarios | jsonb | Estructura: tamaños, formas jurídicas |
| cnae_elegible | text[] | CNAE compatibles; vacío = todos |
| finalidad | text[] | Etiquetas normalizadas (digitalización, i+d, ...) |
| descripcion | text | |
| enlace_oficial | text | URL al BOE / convocatoria |
| raw_payload | jsonb | Respuesta cruda de la API, por si hay que reparsear |
| estado | enum('abierta', 'cerrada', 'proximamente') | |
| created_at, updated_at | timestamptz | |

Índices: `(estado)`, `(fecha_fin)`, GIN sobre `cnae_elegible`, GIN sobre `finalidad`, `(source, external_id)` UNIQUE.

### `search`

Una búsqueda realizada por un usuario. Es la **tabla de leads**.

| Columna | Tipo | Notas |
|---------|------|-------|
| id | uuid PK | |
| nif | text | NIF/CIF/NIE introducido (validado) |
| razon_social | text nullable | Si se obtuvo |
| cnae | text | |
| tamano | enum('micro', 'pequena', 'mediana', 'grande') | |
| provincia | text | Código INE provincia |
| finalidad | text[] | |
| email | text nullable | Si se proporcionó al final |
| ip_hash | text | SHA-256 de la IP (anti-abuso, no PII directa) |
| user_agent | text nullable | |
| created_at | timestamptz | |

### `search_result`

Histórico de qué subvenciones se enseñaron en cada búsqueda y con qué score.

| Columna | Tipo | Notas |
|---------|------|-------|
| id | uuid PK | |
| search_id | uuid FK → search | |
| subvencion_id | uuid FK → subvencion | |
| score | integer 0–100 | |
| razon_llm | text nullable | Frase generada por el LLM |
| rank | integer | Posición en los resultados |

### `alert_subscription`

| Columna | Tipo | Notas |
|---------|------|-------|
| id | uuid PK | |
| email | text UNIQUE | |
| perfil | jsonb | cnae, tamano, provincia, finalidad[] |
| unsubscribe_token | text UNIQUE | Para link de baja en cada email |
| last_sent_at | timestamptz nullable | |
| active | boolean | |
| created_at | timestamptz | |

### `alert_sent`

| Columna | Tipo | Notas |
|---------|------|-------|
| id | uuid PK | |
| subscription_id | uuid FK | |
| subvencion_id | uuid FK | |
| sent_at | timestamptz | |

Índice UNIQUE `(subscription_id, subvencion_id)` para evitar duplicados.

### `email_outbox`

Cola de emails con reintentos (para alerta y bienvenida).

| Columna | Tipo | Notas |
|---------|------|-------|
| id | uuid PK | |
| to_email | text | |
| subject | text | |
| body_html | text | |
| status | enum('pending', 'sent', 'dead') | |
| attempts | integer | |
| last_error | text nullable | |
| created_at, sent_at | timestamptz | |

---

## 8. Flujos principales

### 8.1 Búsqueda (camino principal)

1. `GET /` → renderiza `home.html` (formulario vacío).
2. Usuario teclea NIF y pierde foco (`blur`).
   - HTMX dispara `GET /api/enrich/{nif}`.
   - Backend valida NIF (regex + checksum). Si inválido → 400 con mensaje.
   - `enrich.service.lookup(nif)` consulta en paralelo libreborme + OpenCorporates, hace merge priorizando libreborme.
   - Respuesta HTMX rellena `razon_social`, `cnae`, `provincia` (todos editables).
3. Usuario elige `tamano`, `finalidad[]`, pulsa "Buscar".
4. `POST /search` con form completo:
   - Pydantic valida inputs.
   - Persiste registro en `search`.
   - `matching.filter.candidates()` ejecuta SELECT en `subvencion` con: `estado='abierta'`, `cnae` compatible (`cnae_elegible @> ARRAY[user.cnae] OR cnae_elegible = '{}'`), `ambito` compatible con provincia o `'estatal'`/`'ue'`, `finalidad && user.finalidad`, `fecha_fin > now()`. Pre-rank determinista (peso por % match CNAE + finalidad + cercanía a fecha_fin) y se queda con los **30 mejores candidatos**.
   - `matching.scorer_llm.score()` envía esos 30 candidatos a Claude Haiku **en 3 llamadas batch (10 subvenciones por llamada)** vía `asyncio.gather`. El modelo devuelve para cada subvención `{score: int 0-100, razon: str}`.
   - Si el LLM falla por timeout o error, fallback a usar directamente el score determinista del pre-rank (sin razón en lenguaje natural).
   - Persiste los 30 resultados (con score y razón) en `search_result`, ordenados por score descendente.
   - Renderiza `results.html` con los **top 3 como cards destacadas + los 27 restantes** en tabla compacta.
5. `GET /subsidy/{id}` → detalle completo de la subvención + enlace oficial.
6. (Opcional) Bottom de `results.html` → form "deja tu email para recibir el PDF + alertas":
   - `POST /api/subscribe` crea `alert_subscription` con el perfil de la última búsqueda y encola email de bienvenida con PDF adjunto.

### 8.2 Sincronización (cron diario)

- **03:00** — `sync.runner.sync_bdns()`:
  - Lee BDNS `?fecha_desde={last_sync}` paginado hasta agotar.
  - Hace `upsert` por `(source='bdns', external_id)`.
  - Marca subvenciones como `cerrada` si `fecha_fin < hoy`.
  - Loguea contadores (nuevas / actualizadas / cerradas).
- **03:30** — `sync.runner.sync_eu()`:
  - Idem para EU Funding & Tenders.
- **09:00** — `alerts.dispatcher.run()`:
  - Para cada `alert_subscription` activa:
    - Busca subvenciones nuevas (creadas en `subvencion` después de `last_sent_at`) que matcheen su `perfil` (mismo filtro SQL que `/search`).
    - Si hay ≥1: compone email con la lista, lo encola en `email_outbox`, registra `alert_sent`, actualiza `last_sent_at`.
- **Cada 5 min** — `email_outbox.flush()`:
  - Procesa pendientes vía Brevo, marca `sent` o incrementa `attempts`. Después de 5 fallos → `dead`.

### 8.3 Admin

- `/admin` protegido con HTTP Basic (`ADMIN_USER` / `ADMIN_PASS` en env vars).
- Vistas:
  - **Dashboard**: contadores 24h / 7d / 30d (búsquedas, suscripciones, alertas enviadas).
  - **Búsquedas**: tabla paginada y filtrable de `search`, exportable a CSV.
  - **Suscripciones**: tabla de `alert_subscription`, desactivable manualmente.
  - **Salud**: estado del último sync BDNS / EU / alertas, errores recientes.
  - Botón "Forzar sync ahora" para emergencias.

---

## 9. Manejo de errores

| Escenario | Comportamiento |
|-----------|----------------|
| NIF con formato/checksum inválido | 400 inmediato con mensaje específico, no se hacen llamadas externas |
| libreborme y OpenCorporates fallan | Silencio, fallback a entrada manual; métrica `enrichment_miss++` |
| BDNS API cae durante sync | Reintento exponencial (30s, 5m, 30m); si los 3 fallan, mantiene estado previo, envía email de alerta admin |
| EU API cae | Idem |
| Claude Haiku falla o > 5s timeout | Fallback a scoring determinista por reglas (sin razón en lenguaje natural) |
| Brevo cae al enviar | Email queda en `email_outbox` con estado pending; cron reintenta. 5 fallos → `dead` + alerta admin |
| Postgres caído | Health check `/healthz` falla → Railway reinicia el servicio automáticamente |
| Input inválido en `/search` | Pydantic rechaza con 422 + mensaje claro al usuario |
| Abuso / scraping | Middleware rate-limit: 60 búsquedas/hora por IP_hash |

---

## 10. Estrategia de testing

### Unitarios (pytest)

- `nif_validator`: 50+ casos cubriendo NIF persona física, CIF empresa, NIE extranjero, checksums correctos e incorrectos.
- `bdns_puller.parse()`: fixtures con payloads reales en `tests/fixtures/bdns/*.json`.
- `eu_puller.parse()`: idem en `tests/fixtures/eu/*.json`.
- `matching.filter`: DB sembrada con subvenciones de juguete, verifica que el SELECT devuelve lo esperado para varios perfiles.
- `scorer_llm`: cliente Anthropic mockeado, verifica construcción del prompt y manejo de respuestas malformadas.

### Integración (pytest + httpx + Postgres en Docker)

- `/search` end-to-end con DB sembrada.
- `/api/enrich/{nif}` con clientes externos mockeados.
- `/api/subscribe` y comprobación de que el email queda encolado.
- Cron `sync_bdns` con respuesta mockeada → verifica upserts correctos.
- Cron `alerts.dispatcher` con escenarios de subscription afines y no afines.

### E2E (opcional)

- Playwright recorre: home → meter NIF → ver resultados → suscribirse. Se evalúa al cerrar MVP si compensa la inversión.

### Smoke en producción

- Post-deploy: pega contra `/healthz` y `/search` con NIF canario, verifica respuesta 200 + tiempos.

---

## 11. Despliegue

- **Plataforma:** Railway (existente, 5€/mes).
- **Servicios:**
  - 1 web (FastAPI + Uvicorn).
  - 1 Postgres managed.
  - APScheduler vive dentro del proceso web (no requiere servicio extra).
- **Variables de entorno** (en Railway):
  - `DATABASE_URL`
  - `ANTHROPIC_API_KEY`
  - `BREVO_API_KEY`
  - `OPENCORPORATES_API_KEY`
  - `ADMIN_USER`
  - `ADMIN_PASS`
  - `BASE_URL`
  - `ALERT_FROM_EMAIL`
  - `ALERT_TO_ADMIN_EMAIL` (para alertas de salud)
- **Dominio:** cuando se compre, se conecta en Railway y HTTPS se gestiona automático (Let's Encrypt).
- **CI/CD:** push a `main` → deploy automático en Railway. Rama `dev` para desarrollo.
- **Logging:** logs estructurados (JSON) a stdout; Railway los recoge.
- **Backups:** snapshots diarios de Postgres (incluido en Railway).
- **Observabilidad básica:**
  - `/healthz` → 200 si DB responde.
  - `/admin/metrics` → JSON con contadores clave.

---

## 12. Pre-requisitos antes de empezar a desarrollar

Cuando se arranque la implementación, **lo primero será confirmar con Victor los siguientes elementos** (todos vinculados al objetivo de no escribir código sin las llaves listas):

1. **API key de Anthropic** (Claude Haiku) — para el scoring.
2. **API key de Brevo** — Victor ya la tiene de otros proyectos; confirmar reutilización.
3. **API key gratuita de OpenCorporates** — alta rápida en https://api.opencorporates.com.
4. **Acceso a la cuenta de Railway** + nombre del nuevo servicio.
5. **Decidir nombre del dominio** (no es bloqueante para empezar, pero conviene tenerlo elegido pronto).
6. Confirmar **email remitente** para alertas (un alias de `@flexigobe.com` o uno propio del proyecto).

---

## 13. Trabajo futuro (post-MVP)

- Cuentas de usuario y plan SaaS de pago.
- Enriquecimiento automático con eInforma/Axesor como feature premium.
- Asistente conversacional ("¿tengo que hacer este trámite?") sobre los detalles de cada subvención.
- Cobertura de más fuentes (Iberoamérica, OECD, etc.).
- Panel para gestores/asesores con multi-cliente.
- Notificaciones push / WhatsApp además de email.
- Tramitación asistida (preparación de documentación, formularios) — modelo de negocio.

---

## 14. Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| BDNS cambia su API o se cae | Pull diario tolerante a fallos; mantenemos snapshot, no nos quedamos sin datos |
| libreborme cierra o cambia ToS | Tenemos fallback a OpenCorporates; y siempre el formulario manual |
| OpenCorporates pasa a pago / sube tier | Idem: fallback a manual; impacto solo en UX, no en funcionalidad |
| Coste de LLM se dispara con tráfico | Pre-rank determinista deja solo top 30 candidatos; 3 llamadas batch a Haiku por búsqueda (no 30); rate limit 60 búsquedas/hora por IP_hash; cache de scoring por `(empresa_perfil_hash, subvencion_id)` con TTL 7d |
| Privacidad: capturamos NIF y emails | Política de privacidad clara en footer; SHA-256 sobre IP; opción de borrado bajo petición; cumplimiento RGPD básico |
| Recibir queja por mostrar info incorrecta | Aclarar en cada resultado: "Información orientativa generada automáticamente. Consulta siempre la convocatoria oficial." con link directo |

---

## 15. Métricas de éxito

- **Búsquedas/semana** (objetivo mes 1: 50; mes 3: 500).
- **Tasa de finalización del flujo** (NIF → resultados): objetivo > 70%.
- **Tasa de email captado**: objetivo > 15% de búsquedas terminadas.
- **Tasa de apertura de email de alerta**: objetivo > 30%.
- **Tasa de error LLM** (fallback usado): objetivo < 5%.
- **Latencia media `/search`**: objetivo < 3s.

---

## Decisiones tomadas durante el brainstorming

1. **Propósito:** Gratis ahora, SaaS si funciona.
2. **Alcance geográfico:** España (BDNS) + UE (Funding & Tenders).
3. **Matching:** NIF + 2-3 preguntas rápidas (CNAE + tamaño + provincia + finalidad).
4. **Captación de leads:** Email opcional al final + alertas.
5. **Layout resultados:** Híbrido — top 3 destacadas + tabla compacta del resto.
6. **Flujo:** Single-page progresivo (no wizard).
7. **Enrichment NIF:** Best-effort gratis (libreborme + OpenCorporates) con fallback manual. Sin eInforma/Axesor por coste.
8. **Stack:** Python + FastAPI + HTMX + Tailwind + Postgres + APScheduler.
9. **LLM:** Claude Haiku 4.5 para scoring.
10. **Hosting:** Railway existente.
