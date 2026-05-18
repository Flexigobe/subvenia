# tests/unit/test_routes_search.py
from fastapi.testclient import TestClient

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


def test_home_returns_form():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "NIF" in html or "nif" in html
    assert "tamaño" in html.lower() or "tamano" in html.lower()
    # Finalidad no longer required in the form (Plan 7+: shown as label on results instead)
    assert "razon_social" in html


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
    """Plan 5: NIF is optional but validated when provided."""
    response = client.post(
        "/search",
        data={
            "nif": "INVALIDO",
            "razon_social": "TEST SL",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            "finalidad": ["digitalizacion"],
        },
    )
    assert response.status_code == 400
    assert "NIF" in response.text or "no válido" in response.text.lower()


def test_search_works_without_finalidad(db_session):
    """Plan 7: finalidad is now optional. Without it, the matcher returns all
    applicable subvenciones across all topics; the result page labels each one
    with its finalidad so the user can read what type they are."""
    db_session.add(
        Subvencion(
            source="bdns",
            external_id="ALL-TOPICS-1",
            titulo="Match sin finalidad filter",
            ambito="estatal",
            cnae_elegible=["6201"],
            finalidad=["digitalizacion"],
            estado="abierta",
            fecha_fin=date.today() + timedelta(days=30),
            beneficiarios={"tamanos": ["pequena"]},
            organismo="Min test",
        )
    )
    db_session.commit()
    response = client.post(
        "/search",
        data={
            "razon_social": "TEST SL",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            # No finalidad submitted — should still work and return matches
        },
    )
    assert response.status_code == 200
    assert "Match sin finalidad filter" in response.text


def test_subsidy_detail_renders(db_session):
    sub = Subvencion(
        source="bdns",
        external_id="DETAIL-1",
        titulo="Detalle ayuda",
        organismo="Ministerio X",
        ambito="estatal",
        cnae_elegible=["6201"],
        finalidad=["digitalizacion"],
        estado="abierta",
        fecha_inicio=date.today(),
        fecha_fin=date.today() + timedelta(days=60),
        importe_max_beneficiario=15000,
        descripcion="Descripción completa.",
        enlace_oficial="https://boe.es/detalle",
    )
    db_session.add(sub)
    db_session.commit()

    response = client.get(f"/subsidy/{sub.id}")
    assert response.status_code == 200
    assert "Detalle ayuda" in response.text
    assert "Descripción completa." in response.text
    assert "https://boe.es/detalle" in response.text


def test_subsidy_detail_404_when_not_found():
    response = client.get("/subsidy/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# ── Plan 5 tests ──────────────────────────────────────────────────────────────

def test_search_works_without_nif(db_session):
    """Plan 5: NIF is optional. The form must be submittable without it."""
    db_session.add(
        Subvencion(
            source="bdns",
            external_id="NO-NIF-1",
            titulo="Match sin NIF",
            ambito="estatal",
            cnae_elegible=["6201"],
            finalidad=["digitalizacion"],
            estado="abierta",
            fecha_fin=date.today() + timedelta(days=30),
            beneficiarios={"tamanos": ["pequena"]},
        )
    )
    db_session.commit()

    response = client.post(
        "/search",
        data={
            # No NIF
            "razon_social": "FLEXIGOBE SL",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            "finalidad": ["digitalizacion"],
        },
    )
    assert response.status_code == 200
    assert "Match sin NIF" in response.text


def test_search_still_validates_nif_when_provided():
    """Plan 5: when NIF is provided it must be validated."""
    response = client.post(
        "/search",
        data={
            "nif": "INVALIDO",
            "razon_social": "TEST SL",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            "finalidad": ["digitalizacion"],
        },
    )
    assert response.status_code == 400


def test_home_form_has_empresa_autocomplete():
    """Plan 5: home page must expose razón social autocomplete + optional NIF."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'name="razon_social"' in html
    assert 'hx-get="/api/empresa/search"' in html
    assert "empresa-suggestions" in html
    # NIF is now optional (no `required` attribute on the nif input)
    assert "opcional" in html.lower()
