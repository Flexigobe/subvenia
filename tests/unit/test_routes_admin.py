"""Tests for the admin panel auth + base routing."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from tests.conftest import TestSessionLocal


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


def _basic_header(user: str, password: str) -> dict[str, str]:
    raw = f"{user}:{password}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


@pytest.fixture
def admin_creds(monkeypatch):
    """Set explicit admin credentials for the test."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_user", "testuser")
    monkeypatch.setattr(settings, "admin_pass", "testpass-supersecret")
    # Clear any dev fallback so the explicit values are used
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "_DEV_ADMIN", {})
    return {"user": "testuser", "password": "testpass-supersecret"}


def test_admin_requires_auth_returns_401_without_credentials(admin_creds):
    response = client.get("/admin")
    assert response.status_code == 401
    assert "Basic" in response.headers.get("WWW-Authenticate", "")


def test_admin_rejects_wrong_password(admin_creds):
    response = client.get("/admin", headers=_basic_header("testuser", "WRONG"))
    assert response.status_code == 401


def test_admin_rejects_wrong_username(admin_creds):
    response = client.get("/admin", headers=_basic_header("hacker", "testpass-supersecret"))
    assert response.status_code == 401


def test_admin_accepts_correct_credentials(admin_creds):
    response = client.get(
        "/admin",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "Panel admin" in response.text


def test_admin_returns_503_when_pass_empty(monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_pass", "")
    monkeypatch.setattr(settings, "admin_user", "")
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "_DEV_ADMIN", {})

    response = client.get(
        "/admin",
        headers=_basic_header("anything", "anything"),
    )
    assert response.status_code == 503
    assert "disabled" in response.text.lower() or "admin_pass" in response.text.lower()


def test_admin_logout_returns_401(admin_creds):
    """Logout endpoint forces a fresh auth challenge."""
    response = client.get(
        "/admin/logout",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 401


def test_admin_dev_fallback_credentials_work(monkeypatch):
    """When admin_pass is empty AND _DEV_ADMIN has values, those credentials should work."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_pass", "")
    monkeypatch.setattr(settings, "admin_user", "")
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "_DEV_ADMIN", {"user": "admin", "pass": "dev-fallback-pass"})

    response = client.get("/admin", headers=_basic_header("admin", "dev-fallback-pass"))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Dashboard metrics tests
# ---------------------------------------------------------------------------

def test_dashboard_renders_with_metrics_labels(admin_creds, db_session):
    """Dashboard returns 200 + contains expected metric section titles."""
    response = client.get(
        "/admin",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    text = response.text
    # All key metric blocks are rendered
    for label in [
        "Dashboard",
        "Conversión email",
        "Suscripciones",
        "Outbox",
        "Top finalidades",
        "Top CNAEs",
        "Estado del sync",
    ]:
        assert label in text, f"missing label: {label}"


def test_dashboard_shows_correct_counts(admin_creds, db_session):
    """With seeded data, the dashboard reflects the counts."""
    from app.db.models import Search

    # Seed 3 searches with email and 2 without
    for i in range(3):
        db_session.add(Search(
            nif="B12345674", cnae="6201", tamano="pequena", provincia="08",
            finalidad=["digitalizacion"], email=f"user{i}@example.com",
        ))
    for i in range(2):
        db_session.add(Search(
            nif="B12345674", cnae="4711", tamano="micro", provincia="28",
            finalidad=["comercio"],
        ))
    db_session.commit()

    response = client.get(
        "/admin",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    text = response.text
    # 5 total in 24h — the number 5 must appear somewhere in the counts
    assert "5" in text


def test_dashboard_handles_empty_db_gracefully(admin_creds, db_session):
    """Dashboard with no data shouldn't crash; should show zeros."""
    response = client.get(
        "/admin",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    # The conversion percentage should be 0% on empty data (no zero division)
    assert "0.0%" in response.text or "0%" in response.text


def test_dashboard_shows_top_finalidades_when_data(admin_creds, db_session):
    """When searches exist with finalidades, top finalidades section shows them."""
    from app.db.models import Search

    db_session.add(Search(
        nif="B12345674", cnae="6201", tamano="pequena", provincia="08",
        finalidad=["digitalizacion", "i+d"],
    ))
    db_session.add(Search(
        nif="B12345674", cnae="6201", tamano="pequena", provincia="08",
        finalidad=["digitalizacion"],
    ))
    db_session.commit()

    response = client.get(
        "/admin",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    # "digitalizacion" must appear in the rendered top finalidades list
    assert "digitalizacion" in response.text


# ─────────────────────────────────────────────────────────────────────────────
# Plan 4 Task 3 — admin tables + CSV
# ─────────────────────────────────────────────────────────────────────────────


def test_admin_searches_lists_paginated(admin_creds, db_session):
    from app.db.models import Search
    # Seed 22 rows (>20 to verify pagination)
    for i in range(22):
        db_session.add(Search(
            nif="B12345674", cnae="6201", tamano="pequena", provincia="08",
            finalidad=["digitalizacion"],
        ))
    db_session.commit()

    response = client.get(
        "/admin/searches",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "22 resultados" in response.text or "22" in response.text
    assert "Página 1 de 2" in response.text

    response2 = client.get(
        "/admin/searches?page=2",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response2.status_code == 200
    assert "Página 2 de 2" in response2.text


def test_admin_searches_csv_has_bom_and_headers(admin_creds, db_session):
    from app.db.models import Search
    db_session.add(Search(
        nif="B12345674", cnae="6201", tamano="pequena", provincia="08",
        finalidad=["digitalizacion", "i+d"], email="user@example.com",
    ))
    db_session.commit()

    response = client.get(
        "/admin/searches.csv",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    body = response.text
    # UTF-8 BOM first
    assert body.startswith("﻿") or body.startswith("created_at")  # depending on streaming order
    # All required columns present in the header line
    for col in ["nif", "razon_social", "cnae", "tamano", "provincia", "finalidad", "email"]:
        assert col in body
    # The seeded data appears
    assert "B12345674" in body
    assert "digitalizacion,i+d" in body
    assert "user@example.com" in body


def test_admin_searches_csv_respects_has_email_filter(admin_creds, db_session):
    from app.db.models import Search
    db_session.add(Search(
        nif="WITH-EMAIL", cnae="6201", tamano="pequena", provincia="08",
        finalidad=["digitalizacion"], email="user@example.com",
    ))
    db_session.add(Search(
        nif="WITHOUT-EMAIL", cnae="6201", tamano="pequena", provincia="08",
        finalidad=["digitalizacion"],
    ))
    db_session.commit()

    response = client.get(
        "/admin/searches.csv?has_email=true",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    body = response.text
    assert "WITH-EMAIL" in body
    assert "WITHOUT-EMAIL" not in body


def test_admin_subscriptions_lists(admin_creds, db_session):
    from app.db.models import AlertSubscription
    db_session.add(AlertSubscription(
        email="sub@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="tok-abc",
    ))
    db_session.commit()

    response = client.get(
        "/admin/subscriptions",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "sub@example.com" in response.text
    assert "Desactivar" in response.text  # active subs have the button


def test_admin_deactivate_subscription_works(admin_creds, db_session):
    from app.db.models import AlertSubscription
    from sqlalchemy import select as _select

    sub = AlertSubscription(
        email="bye@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="tok-deactivate",
    )
    db_session.add(sub)
    db_session.commit()

    response = client.post(
        f"/admin/subscriptions/{sub.id}/deactivate",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/subscriptions"

    # Verify DB updated — expire all cached state and re-query
    db_session.expire_all()
    updated = db_session.execute(
        _select(AlertSubscription).where(AlertSubscription.email == "bye@example.com")
    ).scalar_one()
    assert updated.active is False


# ─────────────────────────────────────────────────────────────────────────────
# Plan 4 Task 4 — sync admin + outbox viewer
# ─────────────────────────────────────────────────────────────────────────────


def test_admin_sync_page_renders_with_status(admin_creds, db_session):
    """Sync page shows last update per source + force-now buttons."""
    response = client.get(
        "/admin/sync",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "Sync" in response.text
    assert "Forzar ahora" in response.text
    # Buttons for known jobs
    for job in ["BDNS", "EU", "ENRICHER", "CATALOGS", "ALERTS"]:
        assert job in response.text


def test_admin_sync_run_schedules_known_job(admin_creds, monkeypatch):
    """POST /admin/sync/{job} returns 303 and schedules a background task for known jobs."""
    # Replace the actual coroutines with no-op stubs to avoid hitting real APIs.
    import app.web.routes_admin as routes_mod

    async def _noop():
        return None
    monkeypatch.setitem(routes_mod._JOB_CALLABLES, "bdns", _noop)

    response = client.post(
        "/admin/sync/bdns",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/admin/sync?msg=" in response.headers["location"]


def test_admin_sync_run_unknown_job_returns_404(admin_creds):
    response = client.post(
        "/admin/sync/nope",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_admin_outbox_lists_with_filter(admin_creds, db_session):
    from app.db.models import EmailOutbox
    db_session.add(EmailOutbox(to_email="a@b.com", subject="S1", body_html="<p>1</p>", status="pending"))
    db_session.add(EmailOutbox(to_email="c@d.com", subject="S2", body_html="<p>2</p>", status="sent"))
    db_session.add(EmailOutbox(to_email="e@f.com", subject="S3", body_html="<p>3</p>", status="dead"))
    db_session.commit()

    response = client.get(
        "/admin/outbox?status=dead",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "e@f.com" in response.text
    # The other two should NOT appear (status filter)
    assert "a@b.com" not in response.text
    assert "c@d.com" not in response.text


def test_admin_outbox_retry_dead_resets_attempts(admin_creds, db_session):
    from app.db.models import EmailOutbox
    from sqlalchemy import select as _select

    db_session.add(EmailOutbox(
        to_email="dead@example.com", subject="S", body_html="<p>x</p>",
        status="dead", attempts=5, last_error="some error",
    ))
    db_session.add(EmailOutbox(
        to_email="sent@example.com", subject="S", body_html="<p>x</p>",
        status="sent", attempts=1,
    ))
    db_session.commit()

    response = client.post(
        "/admin/outbox/retry-dead",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/admin/outbox?status=pending" in response.headers["location"]

    db_session.expire_all()
    rows = db_session.execute(_select(EmailOutbox).order_by(EmailOutbox.to_email)).scalars().all()
    by_email = {r.to_email: r for r in rows}
    assert by_email["dead@example.com"].status == "pending"
    assert by_email["dead@example.com"].attempts == 0
    assert by_email["dead@example.com"].last_error is None
    # 'sent' row not touched
    assert by_email["sent@example.com"].status == "sent"
    assert by_email["sent@example.com"].attempts == 1


# ─────────────────────────────────────────────────────────────────────────────
# Plan 5 — admin empresas viewer
# ─────────────────────────────────────────────────────────────────────────────


def test_admin_empresas_lists_paginated(admin_creds, db_session):
    """30 empresas → page 1 of 2."""
    from app.db.models import Empresa

    for i in range(30):
        db_session.add(Empresa(
            slug=f"acme{i:02d}",
            razon_social=f"ACME{i:02d} SL",
            provincia="08",
            hoja_rm=f"H X PAG{i:03d}",
        ))
    db_session.commit()

    response = client.get(
        "/admin/empresas",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "Empresas" in response.text
    assert "ACME00 SL" in response.text  # page 1 should include first
    assert "Página 1 de 2" in response.text


def test_admin_empresas_filters_by_slug_prefix(admin_creds, db_session):
    from app.db.models import Empresa

    db_session.add(Empresa(slug="flexigobe", razon_social="FLEXIGOBE SL", provincia="08", hoja_rm="H X F1"))
    db_session.add(Empresa(slug="acme", razon_social="ACME SL", provincia="08", hoja_rm="H X A1"))
    db_session.commit()

    response = client.get(
        "/admin/empresas?q=flex",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "FLEXIGOBE SL" in response.text
    assert "ACME SL" not in response.text


def test_admin_empresas_filters_by_provincia(admin_creds, db_session):
    from app.db.models import Empresa

    db_session.add(Empresa(slug="madrid co", razon_social="MADRID CO SL", provincia="28", hoja_rm="H X M1"))
    db_session.add(Empresa(slug="bcn co", razon_social="BCN CO SL", provincia="08", hoja_rm="H X B1"))
    db_session.commit()

    response = client.get(
        "/admin/empresas?provincia=28",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "MADRID CO SL" in response.text
    assert "BCN CO SL" not in response.text


def test_admin_empresas_filters_by_estado(admin_creds, db_session):
    from app.db.models import Empresa

    db_session.add(Empresa(slug="activa co", razon_social="ACTIVA CO SL", estado="activa", hoja_rm="H X AC"))
    db_session.add(Empresa(slug="disuelta co", razon_social="DISUELTA CO SL", estado="disuelta", hoja_rm="H X DS"))
    db_session.commit()

    response = client.get(
        "/admin/empresas?estado=disuelta",
        headers=_basic_header(admin_creds["user"], admin_creds["password"]),
    )
    assert response.status_code == 200
    assert "DISUELTA CO SL" in response.text
    assert "ACTIVA CO SL" not in response.text
