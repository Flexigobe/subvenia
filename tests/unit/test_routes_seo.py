"""Tests for sitemap + robots + SEO meta tags."""

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


def test_robots_txt_disallows_admin_and_references_sitemap():
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "User-agent: *" in body
    assert "Disallow: /admin/" in body
    assert "Sitemap:" in body
    assert "/sitemap.xml" in body


def test_sitemap_xml_returns_xml_with_static_routes(db_session):
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]
    body = response.text
    assert body.startswith('<?xml version="1.0"')
    assert "<urlset" in body
    # All 5 static routes must appear
    for path in ["/", "/subvenciones", "/noticias", "/privacidad", "/terminos"]:
        assert f"<loc>http" in body  # has some URL
    # Each <url> has lastmod + changefreq + priority
    assert "<lastmod>" in body
    assert "<changefreq>" in body
    assert "<priority>" in body


def test_sitemap_xml_includes_open_subvenciones(db_session):
    from app.db.models import Subvencion

    db_session.add(Subvencion(
        source="bdns", external_id="SEO-1", titulo="Test SEO", ambito="estatal",
        cnae_elegible=[], finalidad=[], estado="abierta",
    ))
    db_session.commit()

    response = client.get("/sitemap.xml")
    body = response.text
    # The subsidy should appear with /subsidy/{id} path
    assert "/subsidy/" in body


def test_home_has_open_graph_tags():
    response = client.get("/")
    body = response.text
    assert 'property="og:type"' in body
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'name="twitter:card"' in body
    assert 'rel="canonical"' in body


def test_home_has_meta_description():
    response = client.get("/")
    assert response.status_code == 200
    assert 'name="description"' in response.text


def test_plausible_not_loaded_when_domain_empty(monkeypatch):
    """When PLAUSIBLE_DOMAIN is empty (default), the script is NOT injected."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "plausible_domain", "")
    # Re-register globals to pick up the change
    import app.main as main_mod
    main_mod._register_seo_globals()

    response = client.get("/")
    assert "plausible.io" not in response.text


def test_plausible_loaded_when_domain_set(monkeypatch):
    """When PLAUSIBLE_DOMAIN is set, the script appears with data-domain."""
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "plausible_domain", "example.com")
    import app.main as main_mod
    main_mod._register_seo_globals()

    response = client.get("/")
    assert "plausible.io" in response.text
    assert 'data-domain="example.com"' in response.text

    # Restore
    monkeypatch.setattr(settings, "plausible_domain", "")
    main_mod._register_seo_globals()
