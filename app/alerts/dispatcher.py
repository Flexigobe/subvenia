"""Email alerts: outbox processor + new-subvencion digest dispatcher."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlertSent, AlertSubscription, EmailOutbox, Subvencion
from app.lib.email_brevo import send_email
from app.matching.filter import EmpresaProfile, find_candidates

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5


async def flush_outbox(session: Session, max_per_run: int = 50) -> dict[str, int]:
    """Procesa pendientes del outbox. Retries con attempts++, marca 'dead' tras 5 fallos."""
    pending = session.execute(
        select(EmailOutbox)
        .where(EmailOutbox.status == "pending")
        .order_by(EmailOutbox.created_at.asc())
        .limit(max_per_run)
    ).scalars().all()

    sent = failed = dead = 0
    for msg in pending:
        try:
            ok = await send_email(msg.to_email, msg.subject, msg.body_html, msg.attachments)
            if ok:
                msg.status = "sent"
                msg.sent_at = datetime.now(timezone.utc)
                sent += 1
            else:
                msg.attempts += 1
                if msg.attempts >= _MAX_ATTEMPTS:
                    msg.status = "dead"
                    dead += 1
                failed += 1
        except Exception as exc:
            msg.last_error = str(exc)[:500]
            msg.attempts += 1
            if msg.attempts >= _MAX_ATTEMPTS:
                msg.status = "dead"
                dead += 1
            failed += 1
            logger.warning("Outbox send failed for %s: %s", msg.to_email, exc)
        session.commit()

    # If any emails became dead during THIS run AND an admin alert address is configured,
    # enqueue one summary alert.  We only enqueue if no [ADMIN ALERT] is already pending
    # to prevent duplicates.  The alert email itself is subject to the same retry/dead logic,
    # but we do NOT spawn further alerts for it (the subject prefix guard handles that).
    if dead > 0:
        from app.config import get_settings as _get_settings
        _settings = _get_settings()
        if _settings.alert_admin_email:
            already_pending = session.execute(
                select(EmailOutbox).where(
                    EmailOutbox.status == "pending",
                    EmailOutbox.subject.like("[ADMIN ALERT]%"),
                ).limit(1)
            ).scalar_one_or_none()
            if not already_pending:
                recent_dead_rows = session.execute(
                    select(EmailOutbox.to_email, EmailOutbox.subject, EmailOutbox.last_error)
                    .where(EmailOutbox.status == "dead")
                    .order_by(EmailOutbox.created_at.desc())
                    .limit(10)
                ).all()
                details = "\n".join(
                    f"- to={r.to_email} subject={(r.subject or '')[:60]}<br>error={r.last_error or '—'}"
                    for r in recent_dead_rows
                )
                session.add(EmailOutbox(
                    to_email=_settings.alert_admin_email,
                    subject=f"[ADMIN ALERT] {dead} email(s) marked dead in outbox",
                    body_html=(
                        f"<p>El cron de flush_outbox detectó <strong>{dead}</strong> email(s) "
                        f"que no se pudieron enviar tras 5 intentos.</p>"
                        f"<p>Últimos hasta 10 dead:</p><pre>{details}</pre>"
                        f"<p>Revisa <a href=\"{_settings.base_url}/admin/outbox?status=dead\">"
                        f"el panel admin</a> para más detalle y posible retry-dead.</p>"
                    ),
                ))
                session.commit()

    return {"sent": sent, "failed": failed, "dead": dead, "processed": len(pending)}


async def dispatch_alerts(session: Session) -> dict[str, int]:
    """Para cada AlertSubscription activa, busca convocatorias creadas después de
    `last_sent_at` que matcheen el perfil, encola un digest email, registra
    AlertSent (idempotencia) y actualiza last_sent_at.
    """
    from app.config import get_settings
    from fastapi.templating import Jinja2Templates

    settings = get_settings()
    base_url = settings.base_url
    templates = Jinja2Templates(directory=str(
        Path(__file__).resolve().parents[1] / "web" / "templates"
    ))

    subs = session.execute(
        select(AlertSubscription).where(AlertSubscription.active.is_(True))
    ).scalars().all()

    subscriptions_alerted = 0
    total_subvenciones_emailed = 0

    for sub in subs:
        since = sub.last_sent_at or sub.created_at
        try:
            perfil = EmpresaProfile(
                cnae=sub.perfil["cnae"],
                tamano=sub.perfil["tamano"],
                provincia=sub.perfil["provincia"],
                finalidad=sub.perfil.get("finalidad", []),
            )
        except KeyError as exc:
            logger.warning("Subscription %s has malformed perfil (%s); skipping", sub.id, exc)
            continue

        candidates = find_candidates(session, perfil, limit=30)
        new_ones = [c.subvencion for c in candidates if c.subvencion.created_at > since]
        # Filter out ones already alerted to this subscription
        already_sent_ids = set(session.execute(
            select(AlertSent.subvencion_id).where(AlertSent.subscription_id == sub.id)
        ).scalars().all())
        new_ones = [s for s in new_ones if s.id not in already_sent_ids]

        if not new_ones:
            continue

        # Render the digest email
        body_html = templates.get_template("emails/alert_digest.html").render(
            new_subvenciones=new_ones[:10],
            total_new=len(new_ones),
            perfil=sub.perfil,
            unsubscribe_token=sub.unsubscribe_token,
            base_url=base_url,
        )

        session.add(EmailOutbox(
            to_email=sub.email,
            subject=f"{len(new_ones)} nuevas subvenciones para tu empresa",
            body_html=body_html,
        ))
        for s in new_ones:
            session.add(AlertSent(subscription_id=sub.id, subvencion_id=s.id))
        sub.last_sent_at = datetime.now(timezone.utc)
        subscriptions_alerted += 1
        total_subvenciones_emailed += len(new_ones)

    session.commit()
    return {
        "subscriptions_alerted": subscriptions_alerted,
        "total_new_subvenciones": total_subvenciones_emailed,
    }
