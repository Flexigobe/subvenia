# tests/unit/test_routes_search.py
from fastapi.testclient import TestClient

from app.main import app

from app.db.session import get_db
from tests.conftest import TestSessionLocal


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

client = TestClient(app)


def test_home_returns_form():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "NIF" in html or "nif" in html
    assert "tamaño" in html.lower() or "tamano" in html.lower()
    assert 'name="finalidad"' in html or "finalidad" in html.lower()


from datetime import date, timedelta

from app.db.models import Subvencion


def test_search_returns_results_html(db_session):
    # Sembrar una subvención que matchea
    db_session.add(
        Subvencion(
            source="bdns",
            external_id="SEED-001",
            titulo="Kit Digital test",
            organismo="Red.es",
            ambito="estatal",
            cnae_elegible=["6201"],
            finalidad=["digitalizacion"],
            estado="abierta",
            fecha_fin=date.today() + timedelta(days=30),
            beneficiarios={"tamanos": ["micro", "pequena"]},
            importe_max_beneficiario=12000,
            enlace_oficial="https://boe.es/test",
        )
    )
    db_session.commit()

    response = client.post(
        "/search",
        data={
            "nif": "B12345674",
            "razon_social": "Flexigobe SL",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            "finalidad": ["digitalizacion"],
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "Kit Digital test" in html
    assert "Top 3 recomendadas" in html or "recomendadas" in html.lower()


def test_search_invalid_nif_returns_error():
    response = client.post(
        "/search",
        data={
            "nif": "INVALIDO",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            "finalidad": ["digitalizacion"],
        },
    )
    assert response.status_code == 400
    assert "NIF" in response.text or "no válido" in response.text.lower()


def test_search_requires_at_least_one_finalidad():
    response = client.post(
        "/search",
        data={
            "nif": "B12345674",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
        },
    )
    assert response.status_code == 422
