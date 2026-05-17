"""PDF generator vía WeasyPrint. Degrada a None si WeasyPrint no está disponible
o falla al renderizar (deps pango/cairo en macOS)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from weasyprint import HTML  # type: ignore[import-untyped]
    _WEASYPRINT_AVAILABLE = True
except ImportError as exc:
    logger.warning("WeasyPrint not available: %s. PDF generation will be skipped.", exc)
    HTML = None  # type: ignore[assignment,misc]
    _WEASYPRINT_AVAILABLE = False


def generate_pdf(html: str) -> bytes | None:
    """Renderiza HTML a PDF bytes. Devuelve None si WeasyPrint no está disponible o falla."""
    if not _WEASYPRINT_AVAILABLE:
        return None
    try:
        return HTML(string=html).write_pdf()
    except Exception as exc:
        logger.warning("WeasyPrint failed to render PDF: %s", exc)
        return None
