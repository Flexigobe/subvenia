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
