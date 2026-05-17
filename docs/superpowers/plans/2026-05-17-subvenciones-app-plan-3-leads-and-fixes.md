# Plan 3 — Captación de leads + fixes de calidad de datos

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Convertir la app de Plan 2 (datos completos + Gemini scoring) en una máquina de captación de leads (email opcional + PDF + alertas diarias) y resolver las tres limitaciones de datos heredadas de Plan 2.

**Architecture:** Reutiliza patrones de Plans 1/2. Nuevos módulos: `app/enrich/vies.py`, `app/matching/finalidad_classifier.py`, `app/lib/pdf_generator.py`, `app/lib/email_brevo.py`, `app/alerts/dispatcher.py`. Tres nuevas tablas: `alert_subscription`, `alert_sent`, `email_outbox`. APScheduler suma 2 jobs (alerts 09:00, outbox cada 5 min). Patrón "logger only" para email cuando `BREVO_API_KEY=""` — no rompe nada en dev sin la key.

**Tech Stack:** Suma a Plan 1/2 → `weasyprint>=63.0` para PDF.

**Pre-requisitos:**
- ✅ Plan 2 mergeado (`v0.2.0-plan2` en main).
- ✅ Gemini API key existente.
- ⏳ `BREVO_API_KEY` — se le pide a Victor; mientras no esté, el código funciona en "log only" mode.
- ⏳ Email "from" — alias `@flexigobe.com` verificado en Brevo (Victor confirma).

---

## Orden de ejecución y rationale

| # | Tarea | ¿Necesita Brevo? | Impacto en UX |
|---|-------|-------------------|---------------|
| 1 | VIES NIF enrichment | No | Restaura el auto-fill del formulario |
| 2 | Gemini-clasifica finalidad | No | Pasa de 7% a >70% de records BDNS con finalidad útil |
| 3 | EU open-status fix | No | Convierte 100 records EU cerrados en cientos abiertos |
| 4 | Tablas alert_*/outbox + migración | No | Foundation para 5-7 |
| 5 | POST /api/subscribe + PDF | No (log only) | El usuario puede dejar email opcional |
| 6 | Brevo client + outbox processor + alerts dispatcher | Sí (o log only) | Envío real de emails + alerta diaria |
| 7 | GET /unsubscribe/{token} | No | Cumple RGPD básico |

Las 1-3 son los fixes técnicos que pidió el reviewer de Plan 2.

---

## Task 1: VIES NIF enrichment (reemplaza libreborme)

**Files:**
- Create: `app/enrich/vies.py`
- Modify: `app/enrich/service.py` (cambiar `fetch_company` por `fetch_vies`)
- Create: `tests/unit/test_vies.py`

VIES (VAT Information Exchange System) es el servicio público y gratuito de la UE para validar VAT numbers. En España, el NIF/CIF es el VAT con prefijo `ES`. Devuelve `name` (razón social) y `address`. Sin auth, sin rate limit duro, sin coste.

Endpoint REST (más moderno que el SOAP histórico):
```
GET https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/{nif}
```

Respuesta JSON: `{"countryCode": "ES", "vatNumber": "B12345674", "valid": true, "name": "FLEXIGOBE SL", "address": "..."}` o `{"valid": false}`.

### Steps

1. Investiga la API real con un par de NIFs públicos. Si la URL REST no funciona, fallback al SOAP (puedes usar `zeep` — ya no, evita nuevas deps; usa `httpx` con un body XML hardcoded).
2. Implementa `fetch_vies(nif) -> dict | None` con la misma forma de retorno que tenía `libreborme.fetch_company`: `{razon_social, provincia_text, raw}`.
3. Adapta `service.enrich_nif` para llamar a VIES primero. Si VIES devuelve `valid: false`, devuelve None.
4. Reescribe `tests/unit/test_enrich.py` para mockear VIES en vez de libreborme. Eliminar tests que asumían libreborme.
5. Smoke real contra VIES con un NIF público (CIF de Inditex `A15075062` por ejemplo) — debe rellenar la razón social en el formulario.
6. Commit: `feat(enrich): replace libreborme with VIES (free public EU VAT validator)`.

---

## Task 2: Gemini clasifica finalidad desde descripción

**Files:**
- Create: `app/matching/finalidad_classifier.py`
- Modify: `app/sync/bdns_mappers.py` (`map_detail` opcionalmente usa el clasificador en vez del keyword heuristic)
- Modify: `app/sync/bdns_enricher.py` (en `enrich_one` y `enrich_existing`, después del map_detail tradicional, si finalidad=`['otros']`, intenta reclasificar con Gemini usando `descripcionBasesReguladoras`)
- Create: `tests/unit/test_finalidad_classifier.py`
- Crear script one-off: `scripts/reclassify_finalidad.py` (re-aplica el clasificador a los 6394 registros existentes)

### Implementation

`finalidad_classifier.py` expone `async classify(text: str, fallback: list[str]) -> list[str]`:

1. Si `gemini_api_key` vacía → devuelve `fallback`.
2. Llama Gemini 2.5 Flash con prompt: "Dada esta descripción de subvención española, clasifícala en una o varias de estas finalidades: [digitalizacion, i+d, contratacion, eficiencia_energetica, internacionalizacion, formacion, innovacion, comercio, turismo, cultura, deportes, social, agricultura, medio_ambiente, otros]. Devuelve un JSON array con los tokens aplicables (1-3 max)."
3. Cache en memoria por `hash(text[:500])` con TTL 30 días (los textos no cambian).
4. Fallback al keyword heuristic si Gemini falla.

### Re-clasificación de los 6394 records existentes

Hacer un one-off script:

```python
# scripts/reclassify_finalidad.py
import asyncio
from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import Subvencion
from app.matching.finalidad_classifier import classify
from app.sync.bdns_mappers import infer_finalidad

async def main():
    with SessionLocal() as s:
        rows = s.execute(select(Subvencion).where(Subvencion.source == 'bdns')).scalars().all()
        improved = 0
        for sub in rows:
            if 'otros' in (sub.finalidad or []) or not sub.finalidad:
                text = (sub.raw_payload or {}).get('descripcionBasesReguladoras') or sub.titulo or ''
                if not text.strip():
                    continue
                fallback = infer_finalidad((sub.raw_payload or {}).get('descripcionFinalidad'))
                new = await classify(text[:1500], fallback=fallback)
                if new and new != sub.finalidad:
                    sub.finalidad = new
                    improved += 1
                    if improved % 100 == 0:
                        s.commit()
                        print(f"Improved {improved}")
        s.commit()
        print(f"Total improved: {improved}")

asyncio.run(main())
```

Coste estimado: 6000 records × 1 LLM call = 6000 calls. Free tier de Gemini es 1500/día → tardará 4 días Y de tirón. Para evitarlo, **dividir en chunks de 1400/día** o ejecutarlo de noche. O alternativa: solo los 572 records `estado=abierta` (mucho menos).

Decisión pragmática: el script reclasifica **solo records abiertos** (~572). Eso son 572 calls, dentro del free tier de un día. Para records cerrados no merece la pena reclasificar.

### Tests

```python
@pytest.mark.asyncio
async def test_classify_returns_tokens_from_gemini(mock_genai):
    result = await classify("Ayudas para digitalización de PYMEs", fallback=["otros"])
    assert "digitalizacion" in result


@pytest.mark.asyncio
async def test_classify_falls_back_when_gemini_fails(mock_failing_genai):
    result = await classify("texto", fallback=["otros"])
    assert result == ["otros"]


@pytest.mark.asyncio
async def test_classify_returns_fallback_when_no_api_key(monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "gemini_api_key", "")
    result = await classify("texto", fallback=["i+d"])
    assert result == ["i+d"]
```

Commit: `feat(matching): Gemini-based finalidad classifier for richer matching coverage`.

---

## Task 3: EU sync open-status fix

**Files:**
- Modify: `app/sync/eu_puller.py` (parametrizar sort + filtrar Closed antes de upsert)
- Modify: `tests/unit/test_eu_puller.py`

### Issue

La API EU devuelve por orden default los más recientemente cerrados primero. Llamando `max_pages=10` traemos 500 records, todos `Closed`. Queremos los `Open`.

### Two-step fix

1. **Intentar sort por deadline**:
   - Investigar si la API acepta `sortField=deadlineDate` y `sortOrder=DESC` en query string.
   - Si funciona: usar ese sort para que las primeras páginas sean las más futuras (abiertas).
2. **Filtrar en client side al hacer upsert**:
   - En `sync_all`, omitir records donde `estado='cerrada'`. Si solo nos importan los abiertos, no los persistimos.
   - Iterar páginas hasta encontrar X open o agotar `max_pages`.

```python
# en sync_all:
results = payload.get("results") or []
if not results:
    break
new_in_page = 0
for raw in results:
    parsed = parse_item(raw)
    if parsed["estado"] in ("cerrada",):
        continue  # skip closed entirely
    # ... upsert ...
    new_in_page += 1
# Si en esta página no entró ninguno (toda cerrada), seguir buscando.
```

3. **Cleanup**: opcionalmente borrar los 100 records EU cerrados que cargó Plan 2 (`DELETE FROM subvencion WHERE source='eu' AND estado='cerrada'`). Pero como hay records eu en la DB que pueden no encontrarse de nuevo, mejor dejarlos: estado `cerrada` ya los excluye de búsquedas.

Tests:
- `test_eu_parse_item_maps_status_31094502_to_abierta` (ya existe)
- `test_eu_sync_all_skips_closed` (nuevo)
- `test_eu_sync_all_iterates_until_open_found` (nuevo)

Smoke: ejecutar `sync_all(max_pages=20)` y verificar que `eu` records con `estado='abierta'` > 0 en DB.

Commit: `fix(sync): EU puller filters closed grants and iterates for open ones`.

---

## Task 4: Tablas alert_subscription + alert_sent + email_outbox

**Files:**
- Modify: `app/db/models.py` (añadir 3 modelos)
- Create: `migrations/versions/0003_alerts_and_outbox.py`

### Modelos

```python
class AlertSubscription(Base):
    __tablename__ = "alert_subscription"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    perfil: Mapped[dict] = mapped_column(JSONB, nullable=False)  # cnae, tamano, provincia, finalidad
    unsubscribe_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AlertSent(Base):
    __tablename__ = "alert_sent"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_subscription.id", ondelete="CASCADE"), nullable=False
    )
    subvencion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subvencion.id", ondelete="CASCADE"), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("subscription_id", "subvencion_id", name="uq_alert_sub_subv"),)


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list | None] = mapped_column(JSONB)  # [{filename, base64, content_type}]
    status: Mapped[str] = mapped_column(
        Enum("pending", "sent", "dead", name="outbox_status_enum"),
        default="pending",
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

### Migración

Autogenerar y renombrar a `0003_alerts_and_outbox.py`. Verificar JSONB, ARRAY, enums.

Commit: `feat(db): add alert_subscription, alert_sent, email_outbox tables`.

---

## Task 5: POST /api/subscribe + PDF generator

**Files:**
- Create: `app/lib/pdf_generator.py`
- Create: `app/web/routes_alerts.py`
- Create: `app/web/templates/partials/subscribe_form.html`
- Modify: `app/web/templates/results.html` (añadir subscribe form al final)
- Modify: `app/main.py` (incluir router alerts)
- Create: `tests/unit/test_routes_alerts.py`
- Modify: `pyproject.toml` (añadir `weasyprint>=63.0`)

### PDF generator

`app/lib/pdf_generator.py`:

```python
"""PDF generator vía WeasyPrint para el informe de resultados."""
from io import BytesIO
from weasyprint import HTML

def generate_pdf(html: str) -> bytes:
    """Renderiza HTML a PDF bytes."""
    return HTML(string=html).write_pdf()
```

WeasyPrint en macOS necesita brew install pango cairo. **Si el ambiente no tiene esas libs, dejar dependency optional y degradar a "no PDF attached"** — no romper.

### Subscribe endpoint

```python
@router.post("/api/subscribe", response_class=HTMLResponse)
async def subscribe(
    request: Request,
    email: Annotated[str, Form()],
    perfil_json: Annotated[str, Form()],  # JSON serialized perfil from results page
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Validate email
    if not _email_re.match(email):
        raise HTTPException(status_code=400, detail="Email inválido")
    
    perfil = json.loads(perfil_json)
    # Idempotent: si ya existe la sub con ese email, just update perfil
    existing = db.execute(select(AlertSubscription).where(AlertSubscription.email == email)).scalar_one_or_none()
    if existing:
        existing.perfil = perfil
        existing.active = True
    else:
        token = secrets.token_urlsafe(32)
        db.add(AlertSubscription(email=email, perfil=perfil, unsubscribe_token=token))
    db.commit()
    
    # Encolar email de bienvenida con PDF adjunto (que se procesará en el outbox)
    pdf_html = render_template("emails/welcome.html", perfil=perfil, search_results=...)
    try:
        pdf_bytes = generate_pdf(pdf_html)
        attachments = [{
            "filename": "informe-subvenciones.pdf",
            "base64": base64.b64encode(pdf_bytes).decode(),
            "content_type": "application/pdf",
        }]
    except Exception as e:
        attachments = None
    
    db.add(EmailOutbox(
        to_email=email,
        subject="Tus subvenciones — informe + alertas",
        body_html=render_template("emails/welcome_email.html", ...),
        attachments=attachments,
    ))
    db.commit()
    
    return templates.TemplateResponse(request, "partials/subscribe_form.html", {"success": True, "email": email})
```

### Modificar `results.html`

Al final, antes del cierre del bloque `content`, añadir:

```html
<section class="mt-12 bg-white border border-gray-200 rounded-2xl p-6">
  <h2 class="text-xl font-bold mb-2">¿Quieres recibirlo por email?</h2>
  <p class="text-sm text-gray-600 mb-4">
    Te enviamos este informe en PDF y te avisamos en cuanto salga una nueva
    convocatoria que encaje con tu perfil. Sin spam, baja con un clic.
  </p>
  <form
    hx-post="/api/subscribe"
    hx-target="#subscribe-result"
    hx-swap="innerHTML"
    class="flex flex-col md:flex-row gap-2"
  >
    <input
      type="email"
      name="email"
      required
      placeholder="tu@empresa.com"
      class="flex-1 border border-gray-300 rounded px-3 py-2"
    >
    <input type="hidden" name="perfil_json" value='{{ perfil_json }}'>
    <button type="submit" class="bg-green-700 hover:bg-green-800 text-white font-semibold px-6 py-2 rounded">
      Quiero recibir alertas →
    </button>
  </form>
  <div id="subscribe-result" class="mt-2"></div>
</section>
```

Y en el contexto de `POST /search` añadir `perfil_json = json.dumps({...})` para pasarlo al template.

### Tests

- `test_subscribe_creates_subscription_idempotently` (mismo email 2x → 1 record en DB)
- `test_subscribe_invalid_email_returns_400`
- `test_subscribe_encola_email_en_outbox`
- `test_subscribe_returns_success_partial` (HTML partial con "Te enviaremos un email")

Commit: `feat(alerts): POST /api/subscribe + PDF generator + outbox enqueueing`.

---

## Task 6: Brevo client + outbox processor + alerts dispatcher cron

**Files:**
- Create: `app/lib/email_brevo.py`
- Create: `app/alerts/dispatcher.py`
- Modify: `app/sync/runner.py` (2 jobs: outbox flush 5min, alerts dispatcher 09:00)
- Create: `tests/unit/test_email_brevo.py`
- Create: `tests/unit/test_alerts_dispatcher.py`

### Brevo client

```python
# app/lib/email_brevo.py
async def send_email(to: str, subject: str, body_html: str, attachments: list | None = None) -> bool:
    settings = get_settings()
    if not settings.brevo_api_key:
        logger.info("[BREVO LOG-ONLY] would send to %s: %s", to, subject)
        return True  # pretend success for dev/test
    payload = {
        "sender": {"email": settings.alert_from_email or "no-reply@flexigobe.com"},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": body_html,
    }
    if attachments:
        payload["attachment"] = [
            {"name": a["filename"], "content": a["base64"]} for a in attachments
        ]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": settings.brevo_api_key, "Accept": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        return True
```

### Outbox processor

```python
# app/alerts/dispatcher.py
async def flush_outbox(session: Session, max_per_run: int = 50) -> dict:
    pending = session.execute(
        select(EmailOutbox).where(EmailOutbox.status == "pending").limit(max_per_run)
    ).scalars().all()
    sent = failed = 0
    for msg in pending:
        try:
            ok = await send_email(msg.to_email, msg.subject, msg.body_html, msg.attachments)
            if ok:
                msg.status = "sent"
                msg.sent_at = datetime.now(timezone.utc)
                sent += 1
            else:
                msg.attempts += 1
                if msg.attempts >= 5:
                    msg.status = "dead"
        except Exception as e:
            msg.last_error = str(e)[:500]
            msg.attempts += 1
            if msg.attempts >= 5:
                msg.status = "dead"
            failed += 1
        session.commit()
    return {"sent": sent, "failed": failed, "processed": len(pending)}
```

### Alerts dispatcher

```python
async def dispatch_alerts(session: Session) -> dict:
    """Para cada suscripción activa, busca convocatorias nuevas (creadas
    después de last_sent_at) que coincidan con el perfil. Si hay ≥1, encola
    email + marca alert_sent + actualiza last_sent_at."""
    subs = session.execute(select(AlertSubscription).where(AlertSubscription.active.is_(True))).scalars().all()
    sent = 0
    for sub in subs:
        since = sub.last_sent_at or sub.created_at
        perfil = EmpresaProfile(**sub.perfil)
        candidates = find_candidates(session, perfil, limit=30)
        # Filtrar solo las creadas después de `since`
        new_ones = [c.subvencion for c in candidates if c.subvencion.created_at > since]
        # Excluir las ya enviadas a esta suscripción
        already_sent_ids = set(session.execute(
            select(AlertSent.subvencion_id).where(AlertSent.subscription_id == sub.id)
        ).scalars().all())
        new_ones = [s for s in new_ones if s.id not in already_sent_ids]
        if not new_ones:
            continue
        body_html = render_template("emails/alert.html", new=new_ones[:10], perfil=sub.perfil, unsubscribe_token=sub.unsubscribe_token)
        session.add(EmailOutbox(
            to_email=sub.email,
            subject=f"{len(new_ones)} nuevas subvenciones que te encajan",
            body_html=body_html,
        ))
        for s in new_ones:
            session.add(AlertSent(subscription_id=sub.id, subvencion_id=s.id))
        sub.last_sent_at = datetime.now(timezone.utc)
        sent += 1
    session.commit()
    return {"subscriptions_with_alerts": sent}
```

### Jobs en runner.py

- Outbox flush: cada 5 min
- Alerts dispatcher: diario 09:00 Europe/Madrid

### Tests

Mock Brevo HTTP. Verify outbox state transitions, retry count, dead state after 5 fails. Verify dispatcher idempotency (no duplicate alerts for same subvencion).

Commit: `feat(alerts): Brevo client + outbox processor + daily alerts dispatcher`.

---

## Task 7: GET /unsubscribe/{token}

Pequeña ruta para desactivar la suscripción con un click del link en cada email.

**Files:**
- Modify: `app/web/routes_alerts.py`
- Create: `app/web/templates/unsubscribed.html`
- Modify: tests existentes para añadir test del unsubscribe

```python
@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(request: Request, token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    sub = db.execute(select(AlertSubscription).where(AlertSubscription.unsubscribe_token == token)).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404)
    sub.active = False
    db.commit()
    return templates.TemplateResponse(request, "unsubscribed.html", {"email": sub.email})
```

Template `unsubscribed.html`: mensaje de confirmación + link "deshacer" opcional (re-active).

Commit: `feat(alerts): GET /unsubscribe/{token} with RGPD compliance`.

---

## Pasada final + merge

- [ ] `pytest tests/ -v 2>&1 | tail -5` muestra **100+ tests passing** (86 previos + ~15 nuevos).
- [ ] Smoke completo:
  - VIES rellena un NIF público.
  - Búsqueda real devuelve más resultados afines tras Gemini-classify.
  - EU sync trae records abiertos.
  - Subscribe encola email en outbox.
  - Outbox processor lo "envía" (log-only si no hay key).
  - Unsubscribe desactiva.
- [ ] Tag: `git tag -a v0.3.0-plan3 -m "Plan 3 complete: VIES + Gemini-finalidad + EU open fix + email alerts + PDF"`.
- [ ] Merge a `main` con `--no-ff`.

## Cierre

Al terminar Plan 3:
- App captura emails opcionalmente al final de cada búsqueda.
- Envía PDF del informe en el email de bienvenida.
- Cron diario manda alertas con nuevas convocatorias afines.
- VIES auto-rellena el NIF.
- Gemini clasifica finalidad → matches reales >> Plan 2.
- EU contribuye con records abiertos.

**Lo que NO trae todavía (Plan 4):** panel admin + rate limiting + deploy Railway + dominio.
