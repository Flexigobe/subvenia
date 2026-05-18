"""Test that the Empresa model persists and queries correctly."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Empresa


def test_empresa_persists_and_finds_by_slug(db_session):
    empresa = Empresa(
        slug="flexigobe",
        razon_social="FLEXIGOBE SL",
        provincia="08",
        domicilio="C/ Ejemplo 1, Barcelona",
        objeto_social="Servicios informáticos",
        hoja_rm="H B 123456",
        capital_social=Decimal("3000.00"),
        fecha_constitucion=date(2024, 1, 15),
        actos=[{"fecha": "2024-01-15", "tipo": "Constitución", "detalle": "Capital 3000€"}],
    )
    db_session.add(empresa)
    db_session.commit()

    found = db_session.execute(
        select(Empresa).where(Empresa.slug == "flexigobe")
    ).scalar_one()
    assert found.razon_social == "FLEXIGOBE SL"
    assert found.provincia == "08"
    assert found.estado == "activa"  # default
    assert found.capital_social == Decimal("3000.00")
    assert found.actos[0]["tipo"] == "Constitución"


def test_empresa_hoja_rm_is_unique(db_session):
    import pytest
    from sqlalchemy.exc import IntegrityError

    db_session.add(Empresa(slug="a", razon_social="A SL", hoja_rm="H A 100"))
    db_session.commit()
    db_session.add(Empresa(slug="b", razon_social="B SL", hoja_rm="H A 100"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_empresa_estado_enum_accepts_known_values(db_session):
    e1 = Empresa(slug="a1", razon_social="A1", estado="activa")
    e2 = Empresa(slug="d2", razon_social="D2", estado="disuelta")
    e3 = Empresa(slug="c3", razon_social="C3", estado="concursal")
    db_session.add_all([e1, e2, e3])
    db_session.commit()
    rows = db_session.execute(select(Empresa).order_by(Empresa.slug)).scalars().all()
    assert {r.estado for r in rows} == {"activa", "disuelta", "concursal"}
