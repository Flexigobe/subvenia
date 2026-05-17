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
) -> bool:
    """Envía un email vía Brevo. Devuelve True si OK (o si log-only mode).

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
        "sender": {"email": settings.alert_from_email},
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
        r.raise_for_status()
        return True
