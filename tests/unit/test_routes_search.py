# tests/unit/test_routes_search.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_returns_form():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "NIF" in html or "nif" in html
    assert "tamaño" in html.lower() or "tamano" in html.lower()
    assert 'name="finalidad"' in html or "finalidad" in html.lower()
