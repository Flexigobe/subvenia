"""SEO routes: sitemap.xml + robots.txt.

Dynamic sitemap includes public pages plus the most recent N subvenciones.
robots.txt is static text disallowing /admin/* and referencing sitemap.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Subvencion
from app.db.session import get_db

router = APIRouter()


def _origin(request: Request) -> str:
    """Prefer explicit SEO_CANONICAL_ORIGIN over request origin (Railway proxy may give wrong host)."""
    settings = get_settings()
    if settings.seo_canonical_origin:
        return settings.seo_canonical_origin.rstrip("/")
    # Fall back to constructed origin
    scheme = request.url.scheme
    netloc = request.url.netloc
    return f"{scheme}://{netloc}"


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request) -> PlainTextResponse:
    origin = _origin(request)
    body = (
        "User-agent: *\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        "Allow: /\n"
        f"Sitemap: {origin}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@router.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Depends(get_db)) -> Response:
    origin = _origin(request)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    static_urls = [
        ("/", "1.0", "daily"),
        ("/subvenciones", "0.9", "daily"),
        ("/noticias", "0.7", "weekly"),
        ("/como-funciona", "0.6", "monthly"),  # NEW
        ("/privacidad", "0.3", "yearly"),
        ("/terminos", "0.3", "yearly"),
    ]

    # Latest 1000 open subvenciones — keeps sitemap reasonable in size
    stmt = (
        select(Subvencion.id, Subvencion.updated_at)
        .where(Subvencion.estado == "abierta")
        .order_by(Subvencion.updated_at.desc())
        .limit(1000)
    )
    subv_rows = db.execute(stmt).all()

    lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>',
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, priority, freq in static_urls:
        lines.append("<url>")
        lines.append(f"  <loc>{origin}{path}</loc>")
        lines.append(f"  <lastmod>{now}</lastmod>")
        lines.append(f"  <changefreq>{freq}</changefreq>")
        lines.append(f"  <priority>{priority}</priority>")
        lines.append("</url>")
    for row in subv_rows:
        last = row.updated_at.strftime("%Y-%m-%d") if row.updated_at else now
        lines.append("<url>")
        lines.append(f"  <loc>{origin}/subsidy/{row.id}</loc>")
        lines.append(f"  <lastmod>{last}</lastmod>")
        lines.append(f"  <changefreq>monthly</changefreq>")
        lines.append(f"  <priority>0.6</priority>")
        lines.append("</url>")
    lines.append("</urlset>")

    return Response(content="\n".join(lines), media_type="application/xml")
