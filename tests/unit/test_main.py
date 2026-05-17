from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_returns_200():
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    # Enriched /healthz: status is "ok" or "degraded" (scheduler not running in test scope)
    assert data["status"] in ("ok", "degraded")
    assert "db" in data
    assert "scheduler" in data
    assert "checks" in data
