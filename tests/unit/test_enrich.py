"""Tests for NIF enrichment service and HTMX endpoint."""

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


# --- libreborme adapter tests ---

@pytest.mark.asyncio
async def test_libreborme_fetch_company_returns_normalized_dict(httpx_mock):
    httpx_mock.add_response(
        url="https://libreborme.net/api/company/B12345674/",
        json={"cif": "B12345674", "name": "FLEXIGOBE SL", "province": "Madrid"},
    )
    from app.enrich.libreborme import fetch_company

    result = await fetch_company("B12345674")
    assert result is not None
    assert result["razon_social"] == "FLEXIGOBE SL"
    assert result["provincia_text"] == "Madrid"


@pytest.mark.asyncio
async def test_libreborme_returns_none_on_404(httpx_mock):
    httpx_mock.add_response(
        url="https://libreborme.net/api/company/NOEXISTE/",
        status_code=404,
    )
    from app.enrich.libreborme import fetch_company

    result = await fetch_company("NOEXISTE")
    assert result is None


@pytest.mark.asyncio
async def test_libreborme_returns_none_on_server_error(httpx_mock):
    httpx_mock.add_response(
        url="https://libreborme.net/api/company/B12345674/",
        status_code=500,
    )
    from app.enrich.libreborme import fetch_company

    result = await fetch_company("B12345674")
    assert result is None  # silencioso, no levanta excepción


# --- enrich service tests ---

@pytest.mark.asyncio
async def test_enrich_service_returns_libreborme_data(httpx_mock):
    httpx_mock.add_response(
        url="https://libreborme.net/api/company/B12345674/",
        json={"cif": "B12345674", "name": "Acme SL", "province": "Valencia"},
    )
    from app.enrich.service import enrich_nif

    result = await enrich_nif("B12345674")
    assert result is not None
    assert result["razon_social"] == "Acme SL"


@pytest.mark.asyncio
async def test_enrich_service_returns_none_when_no_sources_match(httpx_mock):
    httpx_mock.add_response(
        url="https://libreborme.net/api/company/NOEXISTE/",
        status_code=404,
    )
    from app.enrich.service import enrich_nif

    assert await enrich_nif("NOEXISTE") is None


# --- HTMX endpoint tests ---

def test_enrich_endpoint_returns_400_for_invalid_nif():
    response = client.get("/api/enrich?nif=NOTVALID")
    assert response.status_code == 400


def test_enrich_endpoint_returns_partial_with_data(httpx_mock):
    httpx_mock.add_response(
        url="https://libreborme.net/api/company/B12345674/",
        json={"cif": "B12345674", "name": "Test SL", "province": "Madrid"},
    )
    response = client.get("/api/enrich?nif=B12345674")
    assert response.status_code == 200
    # Returns an HTML partial that includes the company name + an OOB swap for the razon_social input
    assert "Test SL" in response.text
    # The OOB swap targets the razon_social input
    assert 'hx-swap-oob' in response.text.lower() or 'id="razon_social"' in response.text


def test_enrich_endpoint_returns_partial_with_not_found_hint(httpx_mock):
    httpx_mock.add_response(
        url="https://libreborme.net/api/company/B12345674/",
        status_code=404,
    )
    response = client.get("/api/enrich?nif=B12345674")
    assert response.status_code == 200  # not an error — just no data, ask user to fill manually
    # User-friendly hint:
    text_lower = response.text.lower()
    assert "no" in text_lower or "manual" in text_lower or "encontrad" in text_lower
