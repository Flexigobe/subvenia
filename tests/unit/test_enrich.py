"""Tests for NIF enrichment via VIES (replaced libreborme in Plan 3).

Nota: España devuelve "---" para name/address en VIES por política nacional.
Los mocks que incluyen name real simulan un estado miembro que sí publica datos
(o futura publicación de España), para verificar que el adaptador normaliza bien.
"""

import httpx
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


# --- VIES adapter tests ---


@pytest.mark.asyncio
async def test_vies_fetch_company_returns_normalized_dict(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/B12345674",
        json={
            "isValid": True,
            "name": "FLEXIGOBE SOCIEDAD LIMITADA",
            "address": "C. EJEMPLO 1\n28001 MADRID",
            "countryCode": "ES",
            "vatNumber": "B12345674",
        },
    )
    from app.enrich.vies import fetch_company

    result = await fetch_company("B12345674")
    assert result is not None
    assert result["razon_social"] == "FLEXIGOBE SOCIEDAD LIMITADA"
    assert result["provincia_text"] == "Madrid"


@pytest.mark.asyncio
async def test_vies_returns_none_when_invalid(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/NOEXISTE",
        json={"isValid": False, "userError": "INVALID"},
    )
    from app.enrich.vies import fetch_company

    assert await fetch_company("NOEXISTE") is None


@pytest.mark.asyncio
async def test_vies_returns_none_on_server_error(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/B12345674",
        status_code=500,
    )
    from app.enrich.vies import fetch_company

    assert await fetch_company("B12345674") is None


@pytest.mark.asyncio
async def test_vies_returns_none_on_network_error(httpx_mock):
    httpx_mock.add_exception(
        httpx.ConnectError("Network down"),
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/B12345674",
    )
    from app.enrich.vies import fetch_company

    assert await fetch_company("B12345674") is None


@pytest.mark.asyncio
async def test_vies_handles_address_without_postal_code(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/B12345674",
        json={
            "isValid": True,
            "name": "Test SL",
            "address": "Algo sin CP",
        },
    )
    from app.enrich.vies import fetch_company

    result = await fetch_company("B12345674")
    assert result is not None
    assert result["razon_social"] == "Test SL"
    # provincia_text fallback: capitaliza la última línea tal cual
    assert result["provincia_text"] == "Algo Sin Cp"


@pytest.mark.asyncio
async def test_vies_handles_spain_no_data_sentinel(httpx_mock):
    """España devuelve '---' en name/address — resultado válido pero sin razon_social."""
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/A15075062",
        json={
            "isValid": True,
            "userError": "VALID",
            "name": "---",
            "address": "---",
            "vatNumber": "A15075062",
        },
    )
    from app.enrich.vies import fetch_company

    result = await fetch_company("A15075062")
    # NIF válido → resultado no None, pero razon_social y provincia_text son None
    assert result is not None
    assert result["razon_social"] is None
    assert result["provincia_text"] is None


# --- service orchestrator tests ---


@pytest.mark.asyncio
async def test_enrich_service_returns_vies_data(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/B12345674",
        json={"isValid": True, "name": "Acme SL", "address": "Calle X\n46001 VALENCIA"},
    )
    from app.enrich.service import enrich_nif

    result = await enrich_nif("B12345674")
    assert result is not None
    assert result["razon_social"] == "Acme SL"


@pytest.mark.asyncio
async def test_enrich_service_returns_none_when_invalid(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/NOEXISTE",
        json={"isValid": False},
    )
    from app.enrich.service import enrich_nif

    assert await enrich_nif("NOEXISTE") is None


@pytest.mark.asyncio
async def test_enrich_service_returns_dict_for_valid_nif_without_name(httpx_mock):
    """Para NIFs válidos sin nombre (caso España), devuelve el dict igualmente."""
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/A15075062",
        json={"isValid": True, "name": "---", "address": "---"},
    )
    from app.enrich.service import enrich_nif

    result = await enrich_nif("A15075062")
    assert result is not None
    assert result["razon_social"] is None


# --- HTMX endpoint tests (kept from Plan 2) ---


def test_enrich_endpoint_returns_400_for_invalid_nif():
    response = client.get("/api/enrich?nif=NOTVALID")
    assert response.status_code == 400


def test_enrich_endpoint_returns_partial_with_data(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/B12345674",
        json={"isValid": True, "name": "Test SL", "address": "Calle X\n28001 MADRID"},
    )
    response = client.get("/api/enrich?nif=B12345674")
    assert response.status_code == 200
    assert "Test SL" in response.text


def test_enrich_endpoint_returns_partial_with_not_found_hint(httpx_mock):
    httpx_mock.add_response(
        url="https://ec.europa.eu/taxation_customs/vies/rest-api/ms/ES/vat/B12345674",
        json={"isValid": False},
    )
    response = client.get("/api/enrich?nif=B12345674")
    assert response.status_code == 200
    text_lower = response.text.lower()
    assert "no" in text_lower or "manual" in text_lower or "encontrad" in text_lower
