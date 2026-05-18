# tests/unit/test_routes_browse.py

from fastapi.testclient import TestClient

from app.db.models import Subvencion
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


def _make_sub(db, titulo, estado="abierta", ambito="estatal", external_id=None):
    """Helper to create a minimal Subvencion."""
    sub = Subvencion(
        source="bdns",
        external_id=external_id or titulo[:32],
        titulo=titulo,
        organismo="Organismo test",
        ambito=ambito,
        cnae_elegible=["6201"],
        finalidad=["digitalizacion"],
        estado=estado,
        enlace_oficial="https://boe.es/test",
    )
    db.add(sub)
    return sub


def test_browse_lists_all_with_default_filters(db_session):
    """GET /subvenciones with no params returns all 'abierta' items."""
    _make_sub(db_session, "Ayuda digital A", external_id="BROWSE-001")
    _make_sub(db_session, "Beca formación B", external_id="BROWSE-002")
    _make_sub(db_session, "Subvención I+D C", external_id="BROWSE-003")
    db_session.commit()

    response = client.get("/subvenciones")
    assert response.status_code == 200
    html = response.text
    assert "Ayuda digital A" in html
    assert "Beca formación B" in html
    assert "Subvención I+D C" in html


def test_browse_excludes_cerradas_by_default(db_session):
    """Default estado=abierta filter excludes 'cerrada' items."""
    _make_sub(db_session, "Subvención abierta", estado="abierta", external_id="BROWSE-OPEN")
    _make_sub(db_session, "Subvención cerrada", estado="cerrada", external_id="BROWSE-CLOSED")
    db_session.commit()

    response = client.get("/subvenciones")
    assert response.status_code == 200
    html = response.text
    assert "Subvención abierta" in html
    assert "Subvención cerrada" not in html


def test_browse_filters_by_q(db_session):
    """?q=digital returns only matching items."""
    _make_sub(db_session, "Ayuda digital", external_id="BROWSE-DIG")
    _make_sub(db_session, "Beca formación", external_id="BROWSE-FORM")
    db_session.commit()

    response = client.get("/subvenciones?q=digital")
    assert response.status_code == 200
    html = response.text
    assert "Ayuda digital" in html
    assert "Beca formación" not in html


def test_browse_filters_by_ambito(db_session):
    """?ambito=autonomico returns only autonomico items."""
    _make_sub(db_session, "Subvención estatal", ambito="estatal", external_id="BROWSE-EST")
    _make_sub(db_session, "Subvención autonómica", ambito="autonomico", external_id="BROWSE-AUTO")
    db_session.commit()

    response = client.get("/subvenciones?ambito=autonomico")
    assert response.status_code == 200
    html = response.text
    assert "Subvención autonómica" in html
    assert "Subvención estatal" not in html


def test_browse_paginates_when_over_20_items(db_session):
    """25 items → page 1 shows 20, page 2 shows 5; pagination footer visible."""
    for i in range(25):
        _make_sub(db_session, f"Subvención paginada {i:03d}", external_id=f"BROWSE-PAG-{i:03d}")
    db_session.commit()

    response1 = client.get("/subvenciones")
    assert response1.status_code == 200
    html1 = response1.text
    # Should show exactly 20 titles on page 1
    count_on_page1 = sum(
        1 for i in range(25) if f"Subvención paginada {i:03d}" in html1
    )
    assert count_on_page1 == 20
    assert "Página 1 de 2" in html1

    response2 = client.get("/subvenciones?page=2")
    assert response2.status_code == 200
    html2 = response2.text
    count_on_page2 = sum(
        1 for i in range(25) if f"Subvención paginada {i:03d}" in html2
    )
    assert count_on_page2 == 5
