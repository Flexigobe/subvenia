"""Tests for Empresite sitemap parser."""

from app.sync.empresite_sitemap import _url_to_razon_social


def test_url_to_razon_social_basic():
    url = "https://empresite.eleconomista.es/FLEXIBLE-INTEGRATED-CIRCUITS.html"
    assert _url_to_razon_social(url) == "FLEXIBLE INTEGRATED CIRCUITS"


def test_url_to_razon_social_with_dashes():
    url = "https://empresite.eleconomista.es/ABAD-AUDITORES-ABOGADOS-INTERNACIONALES-SLP.html"
    assert _url_to_razon_social(url) == "ABAD AUDITORES ABOGADOS INTERNACIONALES SLP"


def test_url_to_razon_social_skips_meta_pages():
    """Las páginas FAQs, terms, etc. no son empresas."""
    assert _url_to_razon_social("https://empresite.eleconomista.es/FAQS.html") is None
    assert _url_to_razon_social("https://empresite.eleconomista.es/TERMS_OF_USE.html") is None
    assert _url_to_razon_social("https://empresite.eleconomista.es/PRIVACY_POLICY.html") is None


def test_url_to_razon_social_invalid_url_returns_none():
    assert _url_to_razon_social("https://example.com/foo.html") is None
    assert _url_to_razon_social("not a url") is None


def test_url_to_razon_social_strips_whitespace():
    url = "  https://empresite.eleconomista.es/EJEMPLO-SL.html  "
    assert _url_to_razon_social(url) == "EJEMPLO SL"
