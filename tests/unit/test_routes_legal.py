"""Tests for legal pages (privacy + terms)."""

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


def test_privacidad_returns_200_with_rgpd_keywords():
    response = client.get("/privacidad")
    assert response.status_code == 200
    text_lower = response.text.lower()
    # Key RGPD content markers
    assert "responsable" in text_lower
    assert "rgpd" in text_lower or "datos personales" in text_lower
    assert "comercial@flexigobe.com" in response.text
    # Should mention the official sources
    assert "BDNS" in response.text
    assert "BORME" in response.text


def test_terminos_returns_200_with_disclaimer():
    response = client.get("/terminos")
    assert response.status_code == 200
    text_lower = response.text.lower()
    assert "información orientativa" in text_lower or "tal cual" in text_lower
    assert "ley española" in text_lower or "tribunales" in text_lower


def test_footer_has_legal_links():
    """Footer in base.html (rendered by home) must link to /privacidad and /terminos."""
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/privacidad"' in response.text
    assert 'href="/terminos"' in response.text
    assert "Política de privacidad" in response.text


def test_como_funciona_returns_200_with_content():
    response = client.get("/como-funciona")
    assert response.status_code == 200
    text = response.text
    # Key sections that must be present
    assert "Cómo funciona" in text
    assert "BDNS" in text
    assert "BORME" in text
    assert "Gemini" in text or "IA" in text
    # CTA back to home
    assert 'href="/"' in text and "Probar el buscador" in text
    # Mentions privacy link
    assert 'href="/privacidad"' in text


def test_como_funciona_appears_in_nav():
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/como-funciona"' in response.text


def test_home_hero_has_strong_value_proposition():
    response = client.get("/")
    text = response.text
    # Should mention BDNS prominently AND BORME (Plan 7 pivot is hero pulido)
    assert "BDNS" in text and "BORME" in text
    # The three trust chips
    assert "Gratis" in text and "oficiales" in text.lower()
