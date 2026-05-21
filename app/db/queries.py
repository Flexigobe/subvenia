"""Helpers centralizados de consulta sobre Subvencion.

Política: si una subvención está CERRADA con certeza (fecha_fin pasada O fecha_fin
NULL con fecha_inicio muy antigua), NO debe aparecer en ningún listado público.
Esta lógica vive aquí para aplicarse uniformemente en todos los endpoints.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, or_

from app.db.models import Subvencion


# Cuando fecha_fin es NULL, consideramos la convocatoria "indefinida" pero solo
# si fecha_inicio es reciente. Más antiguo que esto = asumimos cerrada.
_INDEFINIDA_MAX_AGE_DAYS = 365


def is_open_filter(today: date | None = None):
    """SQL filter que devuelve solo convocatorias razonablemente abiertas.

    Reglas:
    - estado='cerrada' (explícito) → NUNCA (excluido)
    - fecha_fin >= hoy → abierta segura
    - fecha_fin IS NULL Y fecha_inicio reciente (<1 año) → posiblemente abierta
    - fecha_fin IS NULL Y sin fecha_inicio → asumimos abierta
    - Resto → cerrada, fuera

    Uso:
        stmt = select(Subvencion).where(is_open_filter())
    """
    today = today or date.today()
    one_year_ago = today - timedelta(days=_INDEFINIDA_MAX_AGE_DAYS)
    return and_(
        Subvencion.estado != "cerrada",
        or_(
            Subvencion.fecha_fin >= today,
            and_(
                Subvencion.fecha_fin.is_(None),
                or_(
                    Subvencion.fecha_inicio.is_(None),
                    Subvencion.fecha_inicio >= one_year_ago,
                ),
            ),
        ),
    )
