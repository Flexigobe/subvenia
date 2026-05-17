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


@pytest.mark.asyncio
async def test_flush_outbox_enqueues_admin_alert_when_emails_die(db_session, monkeypatch):
    """When emails get newly marked 'dead' AND alert_admin_email is set,
    flush_outbox enqueues a [ADMIN ALERT] summary."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "brevo_api_key", "fake-key")
    monkeypatch.setattr(get_settings(), "alert_admin_email", "admin@example.com")

    # Pre-seed an email at attempts=4 → will fail and become dead this run
    db_session.add(EmailOutbox(
        to_email="user@example.com",
        subject="S",
        body_html="<p>x</p>",
        attempts=4,
        status="pending",
    ))
    db_session.commit()

    import app.alerts.dispatcher as dispatcher

    async def boom(*a, **k):
        raise RuntimeError("simulated send failure")

    monkeypatch.setattr(dispatcher, "send_email", boom)

    from app.alerts.dispatcher import flush_outbox
    await flush_outbox(db_session)

    # An admin alert should be enqueued
    alerts = db_session.execute(
        select(EmailOutbox).where(EmailOutbox.subject.like("[ADMIN ALERT]%"))
    ).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].to_email == "admin@example.com"
    assert "dead" in alerts[0].body_html.lower()


@pytest.mark.asyncio
async def test_flush_outbox_does_not_duplicate_pending_admin_alert(db_session, monkeypatch):
    """If an admin alert is already pending, flush_outbox should NOT enqueue another."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "brevo_api_key", "fake-key")
    monkeypatch.setattr(get_settings(), "alert_admin_email", "admin@example.com")

    # Pre-seed an existing pending admin alert
    db_session.add(EmailOutbox(
        to_email="admin@example.com",
        subject="[ADMIN ALERT] 3 email(s) marked dead in outbox",
        body_html="<p>existing</p>",
        status="pending",
    ))
    # And a new dying message
    db_session.add(EmailOutbox(
        to_email="user@example.com",
        subject="S",
        body_html="<p>x</p>",
        attempts=4,
        status="pending",
    ))
    db_session.commit()

    import app.alerts.dispatcher as dispatcher

    async def boom(*a, **k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(dispatcher, "send_email", boom)

    from app.alerts.dispatcher import flush_outbox
    await flush_outbox(db_session)

    # Should still be only 1 admin alert (no duplicate)
    alerts = db_session.execute(
        select(EmailOutbox).where(EmailOutbox.subject.like("[ADMIN ALERT]%"))
    ).scalars().all()
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_flush_outbox_skips_admin_alert_when_no_admin_email(db_session, monkeypatch):
    """If alert_admin_email is empty, no admin alert is enqueued even on dead emails."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "brevo_api_key", "fake-key")
    monkeypatch.setattr(get_settings(), "alert_admin_email", "")

    db_session.add(EmailOutbox(
        to_email="user@example.com",
        subject="S",
        body_html="<p>x</p>",
        attempts=4,
        status="pending",
    ))
    db_session.commit()

    import app.alerts.dispatcher as dispatcher

    async def boom(*a, **k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(dispatcher, "send_email", boom)

    from app.alerts.dispatcher import flush_outbox
    await flush_outbox(db_session)

    alerts = db_session.execute(
        select(EmailOutbox).where(EmailOutbox.subject.like("[ADMIN ALERT]%"))
    ).scalars().all()
    assert len(alerts) == 0
