# tests/unit/test_routes_news.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_news_returns_200_and_has_official_sources():
    response = client.get("/noticias")
    assert response.status_code == 200
    # Spot-check known links/anchors
    assert "BDNS" in response.text
    assert "Funding" in response.text and "Tenders" in response.text
    assert "boe.es" in response.text.lower() or "BOE" in response.text
