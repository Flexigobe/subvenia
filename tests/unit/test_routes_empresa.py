"""Tests for /api/empresa/search autocomplete endpoint."""

from fastapi.testclient import TestClient

from app.db.models import Empresa
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


def _seed(db_session, slug: str, razon: str, provincia: str = "08", hoja: str | None = None):
    """Insert an empresa; hoja_rm defaults to a unique value derived from slug."""
    db_session.add(Empresa(
        slug=slug,
        razon_social=razon,
        provincia=provincia,
        hoja_rm=hoja or f"H X {slug.replace(' ', '')}",
    ))


def test_empresa_search_empty_returns_empty(db_session):
    response = client.get("/api/empresa/search?q=")
    assert response.status_code == 200
    assert response.text == ""


def test_empresa_search_short_query_returns_empty(db_session):
    response = client.get("/api/empresa/search?q=a")  # 1 char
    assert response.status_code == 200
    assert response.text == ""


def test_empresa_search_returns_matches_by_slug_prefix(db_session):
    _seed(db_session, "flexigobe", "FLEXIGOBE SL")
    _seed(db_session, "flex sistemas", "FLEX SISTEMAS SA")
    _seed(db_session, "acme", "ACME SL")
    db_session.commit()

    response = client.get("/api/empresa/search?q=flex")
    assert response.status_code == 200
    assert "FLEXIGOBE SL" in response.text
    assert "FLEX SISTEMAS SA" in response.text
    assert "ACME SL" not in response.text


def test_empresa_search_normalizes_accents_and_suffix(db_session):
    _seed(db_session, "flexigobe", "FLEXIGOBE SL")
    db_session.commit()

    # Query with accent and suffix — slugify should normalize to "flexigobe"
    response = client.get("/api/empresa/search?q=Flexigob%C3%A8+SL")
    assert response.status_code == 200
    assert "FLEXIGOBE SL" in response.text


def test_empresa_search_limit_10_results(db_session):
    for i in range(15):
        _seed(db_session, f"acme{i:02d}", f"ACME{i:02d} SL", hoja=f"H X acme{i:02d}")
    db_session.commit()

    response = client.get("/api/empresa/search?q=acme")
    # Count li elements in the response
    count = response.text.count("<li>")
    assert count == 10
