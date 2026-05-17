"""Tests that the alert models persist and constrain correctly."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import AlertSent, AlertSubscription, EmailOutbox, Subvencion


def test_alert_subscription_persists_with_required_fields(db_session):
    sub = AlertSubscription(
        email="user@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": ["digitalizacion"]},
        unsubscribe_token="tok-abc-123",
    )
    db_session.add(sub)
    db_session.commit()

    row = db_session.execute(select(AlertSubscription).where(AlertSubscription.email == "user@example.com")).scalar_one()
    assert row.active is True
    assert row.last_sent_at is None
    assert row.perfil["cnae"] == "6201"
    assert row.unsubscribe_token == "tok-abc-123"


def test_alert_subscription_email_must_be_unique(db_session):
    db_session.add(AlertSubscription(
        email="dup@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="tok-1",
    ))
    db_session.commit()
    db_session.add(AlertSubscription(
        email="dup@example.com",  # duplicate
        perfil={"cnae": "6202", "tamano": "mediana", "provincia": "28", "finalidad": []},
        unsubscribe_token="tok-2",
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_alert_sent_prevents_duplicate_pair(db_session):
    sub = AlertSubscription(
        email="alerts@example.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="tok-alerts",
    )
    subv = Subvencion(
        source="bdns",
        external_id="ALERT-TEST-1",
        titulo="Test",
        ambito="estatal",
        cnae_elegible=[],
        finalidad=[],
        estado="abierta",
    )
    db_session.add_all([sub, subv])
    db_session.commit()

    db_session.add(AlertSent(subscription_id=sub.id, subvencion_id=subv.id))
    db_session.commit()
    db_session.add(AlertSent(subscription_id=sub.id, subvencion_id=subv.id))  # duplicate
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_email_outbox_defaults(db_session):
    msg = EmailOutbox(to_email="x@y.com", subject="Hi", body_html="<p>Hi</p>")
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    assert msg.status == "pending"
    assert msg.attempts == 0
    assert msg.sent_at is None
    assert msg.created_at is not None


def test_email_outbox_status_enum_rejects_invalid_value(db_session):
    msg = EmailOutbox(to_email="x@y.com", subject="Hi", body_html="<p>Hi</p>", status="INVALID")
    db_session.add(msg)
    with pytest.raises(Exception):  # DataError or similar
        db_session.commit()


def test_email_outbox_attachments_jsonb_roundtrip(db_session):
    attachments = [{"filename": "a.pdf", "base64": "abc==", "content_type": "application/pdf"}]
    msg = EmailOutbox(to_email="x@y.com", subject="S", body_html="b", attachments=attachments)
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    assert msg.attachments == attachments
