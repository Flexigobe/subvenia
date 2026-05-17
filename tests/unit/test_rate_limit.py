"""Tests for the rate limiting middleware."""

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.web.rate_limit import RateLimitMiddleware


@pytest.fixture
def small_limit_client():
    """A minimal app with rate limit set to 3 requests per window."""
    app = FastAPI()

    @app.post("/search", response_class=HTMLResponse)
    def search() -> HTMLResponse:
        return HTMLResponse("ok")

    @app.get("/")
    def home() -> HTMLResponse:
        return HTMLResponse("home")

    @app.post("/api/subscribe")
    def subscribe() -> HTMLResponse:
        return HTMLResponse("subscribed")

    @app.get("/admin/searches")
    def admin_searches() -> HTMLResponse:
        return HTMLResponse("admin")

    app.add_middleware(RateLimitMiddleware, requests_per_window=3)
    return TestClient(app)


def test_rate_limit_allows_requests_under_threshold(small_limit_client):
    for i in range(3):
        r = small_limit_client.post("/search", data={})
        assert r.status_code == 200, f"Request {i+1} should succeed"


def test_rate_limit_blocks_4th_request(small_limit_client):
    for _ in range(3):
        small_limit_client.post("/search", data={})
    r = small_limit_client.post("/search", data={})
    assert r.status_code == 429


def test_rate_limit_includes_retry_after_header(small_limit_client):
    for _ in range(3):
        small_limit_client.post("/search", data={})
    r = small_limit_client.post("/search", data={})
    assert "retry-after" in {h.lower() for h in r.headers.keys()}
    retry_after_val = r.headers.get("Retry-After") or r.headers.get("retry-after")
    assert retry_after_val is not None
    assert int(retry_after_val) > 0


def test_rate_limit_message_in_spanish(small_limit_client):
    for _ in range(3):
        small_limit_client.post("/search", data={})
    r = small_limit_client.post("/search", data={})
    assert r.status_code == 429
    assert "búsquedas" in r.text.lower() or "busquedas" in r.text.lower()


def test_rate_limit_does_not_affect_get_endpoints(small_limit_client):
    """Even after hitting the limit on /search, GET / should still work."""
    for _ in range(3):
        small_limit_client.post("/search", data={})
    r = small_limit_client.get("/")
    assert r.status_code == 200


def test_rate_limit_does_not_affect_other_post_endpoints(small_limit_client):
    """POST /api/subscribe is NOT rate-limited (only /search is)."""
    for _ in range(10):
        r = small_limit_client.post("/api/subscribe", data={})
        assert r.status_code == 200


def test_rate_limit_does_not_affect_admin(small_limit_client):
    """GET /admin/searches passes through even when limit reached."""
    for _ in range(3):
        small_limit_client.post("/search", data={})
    r = small_limit_client.get("/admin/searches")
    assert r.status_code == 200


def test_rate_limit_distinguishes_clients_by_user_agent(small_limit_client):
    """Same IP but different UA hashes should be tracked separately."""
    # Saturate client A (default UA)
    for _ in range(3):
        small_limit_client.post("/search", data={})
    # Client B with a totally different UA should NOT be blocked
    r = small_limit_client.post(
        "/search",
        data={},
        headers={"User-Agent": "OtherUserAgent/9.9"},
    )
    assert r.status_code == 200
