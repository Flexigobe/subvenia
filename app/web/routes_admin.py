"""Panel admin protegido con HTTP Basic. Reusable dependency `require_admin` se aplica
en cada ruta. Si ADMIN_PASS está vacío en producción, devuelve 503 — protección extra
para evitar dejar el panel abierto por accidente."""

from __future__ import annotations

import secrets as _secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app.config import get_settings

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


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_index(
    request: Request,
    _user: Annotated[str, Depends(require_admin)],
) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/index.html", {})


@router.get("/logout", response_class=HTMLResponse)
def admin_logout() -> HTMLResponse:
    """Force re-auth by returning 401. Browsers prompt for credentials again."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión cerrada",
        headers={"WWW-Authenticate": 'Basic realm="admin"'},
    )
