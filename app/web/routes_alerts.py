"""HTMX endpoints for the email alerts subsystem."""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AlertSubscription, EmailOutbox, Search, SearchResult
from app.db.session import get_db
from app.lib.pdf_generator import generate_pdf

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


@router.post("/api/subscribe", response_class=HTMLResponse)
async def subscribe(
    request: Request,
    email: Annotated[str, Form()],
    perfil_json: Annotated[str, Form()],
    nif: Annotated[str | None, Form()] = None,
    razon_social: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if not _EMAIL_RE.match(email):
        return templates.TemplateResponse(
            request,
            "partials/subscribe_form.html",
            {"error": "Email no válido. Revisa el formato."},
        )

    try:
        perfil = json.loads(perfil_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Perfil inválido")

    # Find or create the subscription (idempotent by email)
    existing = db.execute(
        select(AlertSubscription).where(AlertSubscription.email == email)
    ).scalar_one_or_none()
    if existing:
        existing.perfil = perfil
        existing.active = True
        sub = existing
    else:
        sub = AlertSubscription(
            email=email,
            perfil=perfil,
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        db.add(sub)
    db.commit()

    # Build the welcome email and PDF report
    settings = get_settings()
    base_url = settings.base_url

    # Reuse the most recent Search row for this NIF if available, otherwise empty report
    top3, rest = [], []
    if nif:
        latest_search = db.execute(
            select(Search).where(Search.nif == nif).order_by(Search.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_search:
            results = db.execute(
                select(SearchResult)
                .where(SearchResult.search_id == latest_search.id)
                .order_by(SearchResult.rank.asc())
            ).scalars().all()
            top3 = list(results[:3])
            rest = list(results[3:])

    # Email body (HTML)
    body_html = templates.get_template("emails/welcome_email.html").render(
        razon_social=razon_social,
        top3=top3,
        perfil=perfil,
        unsubscribe_token=sub.unsubscribe_token,
        base_url=base_url,
        attachments=None,  # will update below if PDF succeeds
    )

    # Try to attach PDF
    attachments = None
    pdf_html = templates.get_template("emails/welcome_pdf.html").render(
        nif=nif or "",
        razon_social=razon_social,
        perfil=perfil,
        top3=top3,
        rest=rest,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    pdf_bytes = generate_pdf(pdf_html)
    if pdf_bytes:
        attachments = [
            {
                "filename": "informe-subvenciones.pdf",
                "base64": base64.b64encode(pdf_bytes).decode("ascii"),
                "content_type": "application/pdf",
            }
        ]
        # Re-render body_html with attachments so the "(adjunto en PDF)" note appears
        body_html = templates.get_template("emails/welcome_email.html").render(
            razon_social=razon_social,
            top3=top3,
            perfil=perfil,
            unsubscribe_token=sub.unsubscribe_token,
            base_url=base_url,
            attachments=attachments,
        )

    db.add(EmailOutbox(
        to_email=email,
        subject="Tus subvenciones — informe + alertas",
        body_html=body_html,
        attachments=attachments,
    ))
    db.commit()

    return templates.TemplateResponse(
        request,
        "partials/subscribe_form.html",
        {"success": True, "email": email},
    )


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(request: Request, token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    sub = db.execute(
        select(AlertSubscription).where(AlertSubscription.unsubscribe_token == token)
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=404, detail="Token de baja no encontrado")

    sub.active = False
    db.commit()
    return templates.TemplateResponse(
        request,
        "unsubscribed.html",
        {"email": sub.email},
    )
