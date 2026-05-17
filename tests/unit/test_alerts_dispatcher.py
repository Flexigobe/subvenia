"""Tests for outbox flush + alerts dispatcher."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import AlertSent, AlertSubscription, EmailOutbox, Subvencion


@pytest.mark.asyncio
async def test_flush_outbox_marks_sent_in_log_only_mode(db_session, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "")

    db_session.add(EmailOutbox(to_email="x@y.com", subject="S", body_html="B"))
    db_session.commit()

    from app.alerts.dispatcher import flush_outbox
    stats = await flush_outbox(db_session)
    assert stats["sent"] == 1
    assert stats["processed"] == 1

    msg = db_session.execute(select(EmailOutbox)).scalar_one()
    assert msg.status == "sent"
    assert msg.sent_at is not None


@pytest.mark.asyncio
async def test_flush_outbox_retries_and_dies_after_5_failures(db_session, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "fake-key")

    db_session.add(EmailOutbox(to_email="x@y.com", subject="S", body_html="B", attempts=4))
    db_session.commit()

    # Patch send_email to always raise
    import app.alerts.dispatcher as dispatcher
    async def boom(*a, **k):
        raise RuntimeError("simulated send failure")
    monkeypatch.setattr(dispatcher, "send_email", boom)

    from app.alerts.dispatcher import flush_outbox
    stats = await flush_outbox(db_session)
    assert stats["failed"] == 1
    assert stats["dead"] == 1

    msg = db_session.execute(select(EmailOutbox)).scalar_one()
    assert msg.status == "dead"
    assert msg.attempts == 5
    assert "simulated send failure" in (msg.last_error or "")


@pytest.mark.asyncio
async def test_dispatch_alerts_enqueues_new_subvenciones(db_session, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "")

    # Create a subscription
    sub = AlertSubscription(
        email="a@b.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": ["digitalizacion"]},
        unsubscribe_token="tk1",
    )
    db_session.add(sub)
    db_session.commit()

    # Seed a matching subvencion created AFTER the sub
    subv = Subvencion(
        source="bdns", external_id="NEW-1", titulo="Kit Digital",
        ambito="estatal", cnae_elegible=["6201"], finalidad=["digitalizacion"],
        estado="abierta", fecha_fin=date.today() + timedelta(days=30),
        beneficiarios={"tamanos": ["pequena"]},
    )
    db_session.add(subv)
    db_session.commit()

    from app.alerts.dispatcher import dispatch_alerts
    stats = await dispatch_alerts(db_session)
    assert stats["subscriptions_alerted"] == 1
    assert stats["total_new_subvenciones"] >= 1

    # Outbox row enqueued, AlertSent recorded
    outbox = db_session.execute(select(EmailOutbox)).scalars().all()
    assert len(outbox) == 1
    sent_rows = db_session.execute(select(AlertSent)).scalars().all()
    assert len(sent_rows) >= 1


@pytest.mark.asyncio
async def test_dispatch_alerts_is_idempotent_per_subvencion(db_session, monkeypatch):
    """Running dispatch twice does NOT send the same subvencion twice."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "")

    # Insert sub first, then subv in a separate commit so subv.created_at > sub.created_at
    sub = AlertSubscription(
        email="idem@b.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": ["digitalizacion"]},
        unsubscribe_token="tk-idem",
    )
    db_session.add(sub)
    db_session.commit()

    subv = Subvencion(
        source="bdns", external_id="IDEM-1", titulo="T",
        ambito="estatal", cnae_elegible=["6201"], finalidad=["digitalizacion"],
        estado="abierta", fecha_fin=date.today() + timedelta(days=30),
        beneficiarios={"tamanos": ["pequena"]},
    )
    db_session.add(subv)
    db_session.commit()

    from app.alerts.dispatcher import dispatch_alerts
    await dispatch_alerts(db_session)
    await dispatch_alerts(db_session)  # second run

    # Only one outbox row produced overall
    outbox_count = len(db_session.execute(select(EmailOutbox)).scalars().all())
    assert outbox_count == 1


@pytest.mark.asyncio
async def test_dispatch_alerts_skips_inactive_subs(db_session, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "")

    sub = AlertSubscription(
        email="inactive@b.com",
        perfil={"cnae": "6201", "tamano": "pequena", "provincia": "08", "finalidad": []},
        unsubscribe_token="tk-inact",
        active=False,
    )
    db_session.add(sub)
    db_session.add(Subvencion(
        source="bdns", external_id="X", titulo="T", ambito="estatal",
        cnae_elegible=[], finalidad=[], estado="abierta",
        fecha_fin=date.today() + timedelta(days=30),
        beneficiarios={"tamanos": ["pequena"]},
    ))
    db_session.commit()

    from app.alerts.dispatcher import dispatch_alerts
    stats = await dispatch_alerts(db_session)
    assert stats["subscriptions_alerted"] == 0
