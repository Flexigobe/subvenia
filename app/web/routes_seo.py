"""SEO routes: sitemap.xml + robots.txt.

Dynamic sitemap includes public pages plus the most recent N subvenciones.
robots.txt is static text disallowing /admin/* and referencing sitemap.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


# Cache del sitemap — 1h TTL (los buscadores no necesitan menos)
import time as _time_sitemap
_sitemap_cache: dict = {"content": "", "ts": 0.0}
_SITEMAP_TTL = 3600


@router.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Depends(get_db)) -> Response:
    origin = _origin(request)
    now = datetime.now(UTC).strftime("%Y-%m-%d")

    # Cache hit
    cur_time = _time_sitemap.time()
    if _sitemap_cache["content"] and (cur_time - _sitemap_cache["ts"]) < _SITEMAP_TTL:
        return Response(
            content=_sitemap_cache["content"],
            media_type="application/xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    static_urls = [
        ("/", "1.0", "daily"),
        ("/subvenciones", "0.9", "daily"),
        ("/licitaciones", "0.85", "daily"),
        ("/noticias", "0.7", "weekly"),
        ("/como-funciona", "0.6", "monthly"),
        ("/comprar", "0.5", "monthly"),
        ("/privacidad", "0.3", "yearly"),
        ("/terminos", "0.3", "yearly"),
    ]

    # Top 5.000 subvenciones vigentes (abiertas + próximamente) por importe
    # Política zero cerradas: solo URLs que llevan a fichas vivas.
    from app.db.queries import is_open_filter as _is_open
    stmt = (
        select(Subvencion.id, Subvencion.updated_at)
        .where(
            Subvencion.estado.in_(("abierta", "proximamente")),
            _is_open(),
        )
        .order_by(Subvencion.importe_total.desc().nullslast(), Subvencion.updated_at.desc())
        .limit(5000)
    )
    subv_rows = db.execute(stmt).all()

    # Top 1.000 licitaciones más recientes
    try:
        from app.db.models import Licitacion
        stmt_lic = (
            select(Licitacion.id, Licitacion.updated_at)
            .order_by(Licitacion.fecha_publicacion.desc().nullslast())
            .limit(1000)
        )
        lic_rows = db.execute(stmt_lic).all()
    except Exception:
        lic_rows = []

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
        lines.append("  <changefreq>weekly</changefreq>")
        lines.append("  <priority>0.7</priority>")
        lines.append("</url>")
    for row in lic_rows:
        last = row.updated_at.strftime("%Y-%m-%d") if row.updated_at else now
        lines.append("<url>")
        lines.append(f"  <loc>{origin}/licitacion/{row.id}</loc>")
        lines.append(f"  <lastmod>{last}</lastmod>")
        lines.append("  <changefreq>weekly</changefreq>")
        lines.append("  <priority>0.6</priority>")
        lines.append("</url>")
    lines.append("</urlset>")

    content = "\n".join(lines)
    _sitemap_cache["content"] = content
    _sitemap_cache["ts"] = cur_time

    return Response(
        content=content,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )
