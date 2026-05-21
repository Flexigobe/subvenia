"""Helper para inyectar globals comunes en todas las plantillas Jinja2.

Uso: en cada router, después de crear `templates = Jinja2Templates(...)`,
llamar `inject_globals(templates)`.

Globals expuestos en el namespace Jinja:
  - today_iso: fecha actual en formato "YYYY.MM.DD" (string)
  - current_year: año actual (int)

NOTA: Los globals se evalúan al cargar el módulo; el server se reinicia
diariamente con el scheduler así que `date.today()` está al día. Si la
app corre días sin reinicio, podríamos usar un context processor más
sofisticado, pero para el caso actual basta.
"""
from datetime import date


def inject_globals(templates) -> None:
    """Añade variables globales a un objeto Jinja2Templates de FastAPI.

    Usamos un objeto wrapper con `__str__` para que `{{ today_iso }}`
    interpole correctamente al string del día actual SIN tener que llamar
    a una función con paréntesis. Esto resuelve el bug donde Jinja imprimía
    `<function inject_globals.<locals>._today_iso>` al verse una función.
    """

    class _DynamicDateStr:
        def __init__(self, fmt: str):
            self._fmt = fmt

        def __str__(self) -> str:
            return date.today().strftime(self._fmt)

        def __html__(self) -> str:
            return self.__str__()

    class _DynamicYear:
        def __str__(self) -> str:
            return str(date.today().year)

        def __int__(self) -> int:
            return date.today().year

        def __html__(self) -> str:
            return str(date.today().year)

    templates.env.globals["today_iso"] = _DynamicDateStr("%Y.%m.%d")
    templates.env.globals["current_year"] = _DynamicYear()

    # Filtro Jinja para renderizar descripciones que pueden venir como HTML
    # (algunas convocatorias EU traen <p>, <strong>, <a> en la descripción).
    # Sanitizamos con bleach para evitar XSS y permitir solo tags seguros.
    import re as _re
    try:
        import bleach as _bleach

        _ALLOWED_TAGS = [
            "p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li",
            "a", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "code",
        ]
        _ALLOWED_ATTRS = {
            "a": ["href", "title", "rel", "target"],
        }

        def _safe_html(text: str | None) -> str:
            if not text:
                return ""
            # Detectar si tiene HTML real (no solo < > sueltos en texto)
            if not _re.search(r"<(p|br|strong|em|h[1-6]|ul|ol|li|a|div|span)[\s>/]", text, _re.IGNORECASE):
                # Texto plano: dejar al template el escape automático
                return text
            # HTML: sanitizar y devolver Markup
            cleaned = _bleach.clean(
                text,
                tags=_ALLOWED_TAGS,
                attributes=_ALLOWED_ATTRS,
                strip=True,
                strip_comments=True,
            )
            # Linkify URLs sueltas
            cleaned = _bleach.linkify(cleaned)
            from markupsafe import Markup
            return Markup(cleaned)
    except ImportError:
        # bleach no instalado (dev local sin deps completas) → strip HTML
        def _safe_html(text: str | None) -> str:
            if not text:
                return ""
            return _re.sub(r"<[^>]+>", "", text)

    templates.env.filters["safe_html"] = _safe_html
