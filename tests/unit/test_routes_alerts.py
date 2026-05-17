"""Tests for the /api/subscribe HTMX endpoint."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AlertSubscription, EmailOutbox
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


def _perfil_payload() -> str:
    return json.dumps({"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": ["digitalizacion"]})


def test_subscribe_creates_subscription_and_enqueues_email(db_session):
    response = client.post("/api/subscribe", data={
        "email": "user@example.com",
        "perfil_json": _perfil_payload(),
    })
    assert response.status_code == 200
    assert "user@example.com" in response.text

    subs = db_session.execute(select(AlertSubscription).where(AlertSubscription.email == "user@example.com")).scalars().all()
    assert len(subs) == 1
    assert subs[0].active is True
    assert subs[0].perfil["cnae"] == "6201"
    assert len(subs[0].unsubscribe_token) >= 32

    outbox = db_session.execute(select(EmailOutbox).where(EmailOutbox.to_email == "user@example.com")).scalars().all()
    assert len(outbox) == 1
    assert outbox[0].status == "pending"
    assert outbox[0].subject.lower().startswith("tus subvenciones")


def test_subscribe_is_idempotent_by_email(db_session):
    payload = {"email": "dup@example.com", "perfil_json": _perfil_payload()}
    r1 = client.post("/api/subscribe", data=payload)
    assert r1.status_code == 200
    # Second call with updated perfil
    payload2 = {
        "email": "dup@example.com",
        "perfil_json": json.dumps({"cnae": "6202", "tamano": "mediana", "provincia": "28", "finalidad": ["i+d"]}),
    }
    r2 = client.post("/api/subscribe", data=payload2)
    assert r2.status_code == 200

    subs = db_session.execute(select(AlertSubscription).where(AlertSubscription.email == "dup@example.com")).scalars().all()
    assert len(subs) == 1
    assert subs[0].perfil["cnae"] == "6202"


def test_subscribe_rejects_invalid_email(db_session):
    response = client.post("/api/subscribe", data={
        "email": "notvalid",
        "perfil_json": _perfil_payload(),
    })
    # 200 with error partial (HTMX-friendly)
    assert response.status_code == 200
    assert "no v" in response.text.lower() or "inválid" in response.text.lower()
    subs = db_session.execute(select(AlertSubscription)).scalars().all()
    assert len(subs) == 0


def test_subscribe_rejects_malformed_perfil(db_session):
    response = client.post("/api/subscribe", data={
        "email": "x@y.com",
        "perfil_json": "{not valid json",
    })
    assert response.status_code == 400


def test_subscribe_reactivates_previously_inactive(db_session):
    # Pre-seed an inactive subscription
    db_session.add(AlertSubscription(
        email="reactivate@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="old-token",
        active=False,
    ))
    db_session.commit()

    response = client.post("/api/subscribe", data={
        "email": "reactivate@example.com",
        "perfil_json": _perfil_payload(),
    })
    assert response.status_code == 200

    sub = db_session.execute(select(AlertSubscription).where(AlertSubscription.email == "reactivate@example.com")).scalar_one()
    assert sub.active is True


def test_unsubscribe_deactivates_subscription(db_session):
    db_session.add(AlertSubscription(
        email="bye@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="known-token-123",
    ))
    db_session.commit()

    response = client.get("/unsubscribe/known-token-123")
    assert response.status_code == 200
    assert "bye@example.com" in response.text
    assert "Baja confirmada" in response.text or "baja" in response.text.lower()

    sub = db_session.execute(select(AlertSubscription).where(AlertSubscription.email == "bye@example.com")).scalar_one()
    assert sub.active is False


def test_unsubscribe_returns_404_for_unknown_token():
    response = client.get("/unsubscribe/this-token-does-not-exist")
    assert response.status_code == 404


def test_unsubscribe_is_idempotent(db_session):
    """Visiting the link twice keeps the sub inactive (no error)."""
    db_session.add(AlertSubscription(
        email="twice@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="idempotent-token",
    ))
    db_session.commit()

    r1 = client.get("/unsubscribe/idempotent-token")
    r2 = client.get("/unsubscribe/idempotent-token")
    assert r1.status_code == 200
    assert r2.status_code == 200

    sub = db_session.execute(select(AlertSubscription).where(AlertSubscription.email == "twice@example.com")).scalar_one()
    assert sub.active is False
