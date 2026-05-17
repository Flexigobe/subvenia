"""Test end-to-end del flujo de búsqueda completo."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Search, SearchResult, Subvencion
from app.db.session import get_db
from app.main import app
from tests.conftest import TestSessionLocal


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


def test_full_search_flow(db_session):
    # Sembrar varias subvenciones
    db_session.add_all([
        Subvencion(
            source="bdns", external_id=f"FLOW-{i}",
            titulo=f"Ayuda {i}",
            ambito="estatal",
            cnae_elegible=["6201"] if i % 2 == 0 else [],
            finalidad=["digitalizacion"],
            estado="abierta",
            fecha_fin=date.today() + timedelta(days=30 + i),
            beneficiarios={"tamanos": ["pequena"]},
            organismo="Ministerio test",
            importe_max_beneficiario=12000,
        )
        for i in range(5)
    ])
    db_session.commit()

    # POST /search
    response = client.post("/search", data={
        "nif": "B12345674",
        "razon_social": "Empresa Test SL",
        "cnae": "6201",
        "tamano": "pequena",
        "provincia": "08",
        "finalidad": ["digitalizacion"],
    })
    assert response.status_code == 200
    assert "Ayuda" in response.text

    # Verificar que se guardó la búsqueda
    searches = db_session.execute(select(Search)).scalars().all()
    assert len(searches) == 1
    s = searches[0]
    assert s.nif == "B12345674"
    assert s.razon_social == "Empresa Test SL"

    # Verificar que se guardaron los resultados
    results = db_session.execute(select(SearchResult).where(SearchResult.search_id == s.id)).scalars().all()
    assert len(results) == 5
    # Todos los rangs entre 1 y 5
    ranks = sorted(r.rank for r in results)
    assert ranks == [1, 2, 3, 4, 5]

    # Coger un subvencion_id y abrir su detalle
    first_subv_id = results[0].subvencion_id
    detail = client.get(f"/subsidy/{first_subv_id}")
    assert detail.status_code == 200
    assert "Ayuda" in detail.text
