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
