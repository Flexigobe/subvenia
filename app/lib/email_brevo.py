"""Cliente para Brevo (Sendinblue) transactional email API.

Cuando `brevo_api_key` está vacío, los emails se loguean en vez de enviarse — útil
para dev/test y como degradación cuando aún no hay key en producción.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


async def send_email(
    to: str,
    subject: str,
    body_html: str,
    attachments: list[dict[str, Any]] | None = None,
    unsubscribe_url: str | None = None,
) -> bool:
    """Envía un email vía Brevo. Devuelve True si OK (o si log-only mode).

    Args:
        to: destinatario
        subject: asunto
        body_html: cuerpo HTML del mensaje
        attachments: lista [{filename, base64}] de adjuntos
        unsubscribe_url: si se pasa, añade headers List-Unsubscribe (Gmail Feb 2024
                         lo exige para mejor deliverability).

    Raises:
        httpx.HTTPStatusError on >=400 responses (caller decides retry).
    """
    settings = get_settings()
    if not settings.brevo_api_key:
        logger.info(
            "[BREVO LOG-ONLY] to=%s subject=%r body=%d chars attachments=%d",
            to,
            subject,
            len(body_html),
            len(attachments or []),
        )
        return True

    payload: dict[str, Any] = {
        "sender": {"email": settings.alert_from_email, "name": "Radar Ayudas"},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": body_html,
    }
    if attachments:
        # Brevo expects [{"name": "...", "content": base64-string}]
        payload["attachment"] = [
            {"name": a["filename"], "content": a["base64"]}
            for a in attachments
        ]

    # List-Unsubscribe headers (Gmail/Yahoo requirements Feb 2024 para no marcar como spam)
    if unsubscribe_url:
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>, <mailto:{settings.alert_from_email}?subject=unsubscribe>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            BREVO_API_URL,
            headers={
                "api-key": settings.brevo_api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if r.status_code >= 400:
            # Capturar mensaje detallado de Brevo para debug en logs
            try:
                error_body = r.json()
                logger.error(
                    "Brevo API %d to=%s: code=%s message=%s",
                    r.status_code,
                    to,
                    error_body.get("code"),
                    error_body.get("message"),
                )
            except Exception:
                logger.error("Brevo API %d to=%s: body=%s", r.status_code, to, r.text[:500])
            r.raise_for_status()
        return True
