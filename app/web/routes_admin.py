"""Panel admin protegido con HTTP Basic. Reusable dependency `require_admin` se aplica
en cada ruta. Si ADMIN_PASS está vacío en producción, devuelve 503 — protección extra
para evitar dejar el panel abierto por accidente."""

from __future__ import annotations

import secrets as _secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    AlertSubscription,
    EmailOutbox,
    Search,
    Subvencion,
)
from app.db.session import get_db

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/admin")

_basic = HTTPBasic()


def require_admin(credentials: Annotated[HTTPBasicCredentials, Depends(_basic)]) -> str:
    """Validate credentials with constant-time comparison.

    Returns the authenticated username (useful for audit logging).
    Raises 503 if admin is disabled (empty pass) or 401 on bad credentials.
    """
    # Import here so tests can monkeypatch _get_admin_credentials cleanly
    from app.main import _get_admin_credentials

    user, password = _get_admin_credentials()
    if not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin disabled. Set ADMIN_PASS in environment.",
        )

    correct_user = _secrets.compare_digest(credentials.username.encode(), user.encode())
    correct_pass = _secrets.compare_digest(credentials.password.encode(), password.encode())
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": 'Basic realm="admin"'},
        )
    return credentials.username


# ---------------------------------------------------------------------------
# Dashboard metrics helpers
# ---------------------------------------------------------------------------

def _count_since(session: Session, since: datetime) -> int:
    return session.execute(
        select(func.count()).select_from(Search).where(Search.created_at >= since)
    ).scalar_one()


def _top_values(
    session: Session, since: datetime, *, array_field: bool, column
) -> list[tuple[str, int]]:
    """Return top 10 values of a column ordered by frequency in the last `since` window.

    If `array_field=True`, the column is a Postgres ARRAY — unnest it first.
    """
    if array_field:
        stmt = (
            select(func.unnest(column).label("v"), func.count().label("c"))
            .where(Search.created_at >= since)
            .group_by("v")
            .order_by(func.count().desc())
            .limit(10)
        )
    else:
        stmt = (
            select(column.label("v"), func.count().label("c"))
            .where(Search.created_at >= since)
            .group_by("v")
            .order_by(func.count().desc())
            .limit(10)
        )
    return [(row.v, row.c) for row in session.execute(stmt)]


def _compute_dashboard_metrics(session: Session) -> dict:
    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    last_30d = now - timedelta(days=30)

    # Búsquedas counts
    total_searches = session.execute(select(func.count()).select_from(Search)).scalar_one()
    searches_24h = _count_since(session, last_24h)
    searches_7d = _count_since(session, last_7d)
    searches_30d = _count_since(session, last_30d)

    # Conversión email
    emails_30d = session.execute(
        select(func.count()).select_from(Search).where(
            Search.created_at >= last_30d,
            Search.email.is_not(None),
        )
    ).scalar_one()
    conversion = (emails_30d / searches_30d * 100.0) if searches_30d else 0.0

    # Top finalidades + CNAEs + tamaño distribution (last 30d)
    top_finalidades = _top_values(session, last_30d, array_field=True, column=Search.finalidad)
    top_cnaes = _top_values(session, last_30d, array_field=False, column=Search.cnae)
    distrib_tamano = _top_values(session, last_30d, array_field=False, column=Search.tamano)

    # Suscripciones
    subs_total = session.execute(select(func.count()).select_from(AlertSubscription)).scalar_one()
    subs_active = session.execute(
        select(func.count()).select_from(AlertSubscription).where(AlertSubscription.active.is_(True))
    ).scalar_one()
    subs_inactive = subs_total - subs_active

    # Outbox status counts
    outbox_counts_rows = session.execute(
        select(EmailOutbox.status, func.count())
        .group_by(EmailOutbox.status)
    ).all()
    outbox_counts = {r[0]: r[1] for r in outbox_counts_rows}

    # Outbox avg send time (sent in last 7d)
    avg_send_seconds_row = session.execute(
        select(
            func.avg(
                func.extract("epoch", EmailOutbox.sent_at - EmailOutbox.created_at)
            )
        ).where(
            EmailOutbox.status == "sent",
            EmailOutbox.sent_at.is_not(None),
            EmailOutbox.sent_at >= last_7d,
        )
    ).scalar_one()
    avg_send_seconds = float(avg_send_seconds_row) if avg_send_seconds_row else None

    # Sync state per source
    sync_state_rows = session.execute(
        select(
            Subvencion.source,
            func.count().label("c"),
            func.max(Subvencion.updated_at).label("last_update"),
        )
        .group_by(Subvencion.source)
    ).all()
    sync_state = [
        {"source": r.source, "count": r.c, "last_update": r.last_update}
        for r in sync_state_rows
    ]

    return {
        "total_searches": total_searches,
        "searches_24h": searches_24h,
        "searches_7d": searches_7d,
        "searches_30d": searches_30d,
        "conversion_pct": round(conversion, 1),
        "emails_30d": emails_30d,
        "top_finalidades": top_finalidades,
        "top_cnaes": top_cnaes,
        "distrib_tamano": distrib_tamano,
        "subs_total": subs_total,
        "subs_active": subs_active,
        "subs_inactive": subs_inactive,
        "outbox_counts": outbox_counts,
        "avg_send_seconds": avg_send_seconds,
        "sync_state": sync_state,
        "now": now,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_index(
    request: Request,
    _user: Annotated[str, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> HTMLResponse:
    metrics = _compute_dashboard_metrics(db)
    return templates.TemplateResponse(request, "admin/dashboard.html", metrics)


@router.get("/logout", response_class=HTMLResponse)
def admin_logout() -> HTMLResponse:
    """Force re-auth by returning 401. Browsers prompt for credentials again."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión cerrada",
        headers={"WWW-Authenticate": 'Basic realm="admin"'},
    )
