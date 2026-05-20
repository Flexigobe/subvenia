"""Tests for /api/cnae/search typeahead endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_cnae_search_empty_returns_empty():
    response = client.get("/api/cnae/search?q=")
    assert response.status_code == 200
    assert response.text == ""


def test_cnae_search_by_keyword_ferreteria():
    """Buscar 'ferreteria' debe devolver CNAE 4674 (al por mayor) y/o 4752 (al por menor)."""
    response = client.get("/api/cnae/search?q=ferreteria")
    assert response.status_code == 200
    assert "4674" in response.text
    assert "Comercio al por mayor de ferretería" in response.text


def test_cnae_search_by_keyword_software():
    response = client.get("/api/cnae/search?q=software")
    assert response.status_code == 200
    assert "6201" in response.text


def test_cnae_search_by_keyword_restaurante():
    response = client.get("/api/cnae/search?q=restaurante")
    assert response.status_code == 200
    assert "5610" in response.text


def test_cnae_search_by_keyword_asesoria_fiscal():
    response = client.get("/api/cnae/search?q=asesoria+fiscal")
    assert response.status_code == 200
    assert "6920" in response.text


def test_cnae_search_by_numeric_code_prefix():
    """Cuando la query es numérica, busca por prefijo de código."""
    response = client.get("/api/cnae/search?q=62")
    assert response.status_code == 200
    # 62xx codes
    assert "6201" in response.text or "6202" in response.text


def test_cnae_search_returns_data_attributes():
    """El partial debe incluir data-code y data-description para que JS los lea."""
    response = client.get("/api/cnae/search?q=peluqueria")
    assert response.status_code == 200
    assert "data-code=" in response.text
    assert "data-description=" in response.text
    assert "cnae-option" in response.text


def test_cnae_search_accepts_cnae_param_alias():
    """El input HTMX envía el parámetro como `cnae=` (nombre del input). El endpoint
    debe aceptar tanto `q=` como `cnae=`."""
    response = client.get("/api/cnae/search?cnae=jardineria")
    assert response.status_code == 200
    assert "8130" in response.text


def test_cnae_search_no_results_shows_helpful_message():
    """Cuando ninguna entrada matchea, mostramos guía amistosa con sugerencias."""
    response = client.get("/api/cnae/search?q=xxxxxxxxxx")
    assert response.status_code == 200
    # Debe sugerir continuar con otras palabras o código numérico
    assert "xxxxxxxxxx" in response.text or "Prueba" in response.text or "código" in response.text


def test_cnae_search_15_sectors_all_findable():
    """Verificación exhaustiva: 15 actividades comunes, todas encuentran su CNAE."""
    cases = [
        ("ferreteria", "4674"),
        ("software", "6201"),
        ("consultoria informatica", "6202"),
        ("restaurante", "5610"),
        ("bar", "5630"),
        ("transporte mercancias", "4941"),
        ("taxi", "4932"),
        ("asesoria fiscal", "6920"),
        ("abogado", "6910"),
        ("dental", "8623"),
        ("peluqueria", "9602"),
        ("gimnasio", "9313"),
        ("hotel", "5510"),
        ("autoescuela", "8553"),
        ("farmacia", "4773"),
    ]
    for query, expected_code in cases:
        response = client.get(f"/api/cnae/search?q={query.replace(' ', '+')}")
        assert response.status_code == 200, f"Failed for {query}"
        assert expected_code in response.text, (
            f"Query '{query}' should return CNAE {expected_code} but didn't"
        )
