"""Tests for the enriched /healthz endpoint."""

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


def test_healthz_returns_rich_json():
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert "db" in data
    assert "scheduler" in data
    assert "checks" in data


def test_healthz_db_check_includes_latency():
    response = client.get("/healthz")
    data = response.json()
    if data["db"] == "ok":
        assert "db_latency_ms" in data["checks"]
        assert isinstance(data["checks"]["db_latency_ms"], (int, float))


def test_healthz_reports_degraded_when_scheduler_not_running():
    """TestClient doesn't actually start the lifespan in this test scope, so
    _scheduler_ref['instance'] is None — that should report scheduler=stopped + degraded."""
    import app.main as main_mod

    original = main_mod._scheduler_ref.get("instance")
    try:
        main_mod._scheduler_ref["instance"] = None
        response = client.get("/healthz")
        data = response.json()
        assert data["scheduler"] == "stopped"
        assert data["status"] == "degraded"
    finally:
        main_mod._scheduler_ref["instance"] = original
